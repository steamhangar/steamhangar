"""Daytime-window parsing and containment for the scheduler (WP 3.5).

Plan §7 Phase 3: "Configurable cron window (e.g. 09:00-17:00, every 3 h)".
This module is the *pure* half of that: string -> ``ScheduleWindow``, and
"is this moment inside the window". No database, no threads, no clock of its
own — everything is a function of the arguments, which is what makes the
scheduler's decisions testable without waiting for real time to pass.

Kept in its own module (rather than inside ``vault_api/scheduler.py``) for one
concrete reason: ``vault_api/config.py`` validates ``VAULT_SCHEDULE_WINDOW`` at
**startup**, so it needs the parser — and ``scheduler.py`` imports
``config.Settings``. A parser living in ``scheduler.py`` would make that a
circular import.

Format
------
``HH:MM-HH:MM``, zero-padded, 24-hour, e.g. ``09:00-17:00``. Whitespace around
the value and around either side of the ``-`` is tolerated
(``" 09:00 - 17:00 "``); everything else is rejected loudly rather than
guessed at.

Semantics, stated once so the rest of the codebase can just point here:

* **Start inclusive, end exclusive** — ``09:00-17:00`` is active from 09:00:00
  up to but not including 17:00:00. (Second/microsecond precision is ignored:
  containment is decided on whole minutes, so 16:59:59 is inside.)
* **Overnight windows are supported.** ``22:00-06:00`` (end earlier than
  start) means 22:00 through midnight *and* midnight through 06:00 — one
  contiguous night. This is deliberate: "prefill while nobody is gaming" is
  the natural homelab window and it crosses midnight. It is expressed as the
  union ``[start, 24:00) ∪ [00:00, end)``; no calendar day is tracked, so the
  window simply recurs every night.
* **``24:00`` is accepted as the END value only**, meaning "end of day". This
  is what makes an always-on window expressible: ``00:00-24:00``. As a *start*
  value it is rejected (a window starting at end-of-day is empty).
* **start == end is rejected**, e.g. ``09:00-09:00``. It reads as either "zero
  minutes" or "the whole day" depending on who you ask; refusing it and
  pointing at ``00:00-24:00`` removes the ambiguity instead of picking one
  silently.
* **Server-local time.** A moment is classified by its own wall-clock hour and
  minute, so callers must pass a datetime already in the timezone the window
  is meant to describe. ``vault_api/scheduler.py`` passes an *aware*
  local-time datetime; see its module docstring for the timezone and DST
  notes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

#: Minutes in a day; also the ``end_minute`` value for the ``24:00`` end-of-day
#: spelling (one past the last real minute-of-day, matching the exclusive-end
#: rule above).
MINUTES_PER_DAY = 24 * 60

#: Exactly two ASCII digits, a colon, exactly two ASCII digits. Deliberately
#: NOT ``\d`` (which matches non-ASCII digits) and deliberately not left to
#: ``int()``, which happily accepts ``" 4 "``, ``"+4"``, ``"1_0"`` (=10) and
#: non-ASCII digits — a LEARNINGS.md entry that cost a review round elsewhere
#: in this project. By the time ``int()`` runs below it can only ever see two
#: ASCII digits.
_TIME_RE = re.compile(r"^([0-9]{2}):([0-9]{2})$")


class ScheduleWindowError(ValueError):
    """A ``VAULT_SCHEDULE_WINDOW`` value that cannot be parsed.

    Its own type (not a bare ``ValueError``) so ``config.Settings.from_env``
    can catch exactly this and re-raise it as the startup ``RuntimeError``
    every other bad setting produces, without swallowing unrelated errors.
    """


def format_minute_of_day(minute: int) -> str:
    """``540 -> "09:00"``, ``1440 -> "24:00"`` — the inverse of the parser."""
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _parse_time_of_day(text: str, *, allow_end_of_day: bool) -> int:
    """``"09:00"`` -> minutes since midnight. Raises ``ScheduleWindowError``."""
    match = _TIME_RE.match(text)
    if match is None:
        raise ScheduleWindowError(
            f"{text!r} is not a valid time of day; expected zero-padded HH:MM "
            "(24-hour), e.g. '09:00'"
        )
    hour = int(match.group(1))
    minute = int(match.group(2))

    if minute > 59:
        raise ScheduleWindowError(
            f"{text!r} has an invalid minute ({minute}); must be 00-59"
        )
    if hour == 24:
        if not allow_end_of_day:
            raise ScheduleWindowError(
                "'24:00' is only accepted as the END of a window (meaning "
                "end of day); a window cannot start at 24:00"
            )
        if minute != 0:
            raise ScheduleWindowError(
                f"{text!r} is invalid; the only accepted 24-hour spelling is "
                "exactly '24:00'"
            )
        return MINUTES_PER_DAY
    if hour > 23:
        raise ScheduleWindowError(
            f"{text!r} has an invalid hour ({hour}); must be 00-23 "
            "(or exactly '24:00' as the window's end)"
        )
    return hour * 60 + minute


@dataclass(frozen=True)
class ScheduleWindow:
    """A recurring daily time window. Build it with :func:`parse_window`."""

    #: Minutes since midnight, inclusive. 0-1439.
    start_minute: int
    #: Minutes since midnight, exclusive. 0-1440 (1440 = end of day).
    end_minute: int
    #: The value as configured, echoed back by ``GET /v1/schedule`` so an
    #: operator sees what they typed rather than a re-rendered form.
    raw: str

    @property
    def overnight(self) -> bool:
        """True for a window that wraps past midnight (``22:00-06:00``)."""
        return self.end_minute < self.start_minute

    @property
    def normalized(self) -> str:
        """Canonical ``HH:MM-HH:MM`` rendering (``raw`` minus any whitespace)."""
        return (
            f"{format_minute_of_day(self.start_minute)}-"
            f"{format_minute_of_day(self.end_minute)}"
        )

    def contains(self, moment: datetime) -> bool:
        """Is ``moment``'s wall-clock time inside the window?

        Whole-minute precision (seconds are ignored), start inclusive, end
        exclusive — see the module docstring. ``moment`` may be naive or
        aware; only ``.hour``/``.minute`` are read, so it is the *caller's*
        job to pass a datetime in the timezone the window describes.
        """
        minute = moment.hour * 60 + moment.minute
        if self.overnight:
            return minute >= self.start_minute or minute < self.end_minute
        return self.start_minute <= minute < self.end_minute


#: D16b (Q7, docs/handover/d16-settings-audit.md 3.6): the one keyword that
#: spells "no window" for every place a download window is configured
#: (``VAULT_SCHEDULE_WINDOW``, a ``PATCH /v1/settings`` value, and the core
#: settings file's ``VAULT_UPSTREAM_RATE_WINDOW``). Exact and lowercase, after
#: the caller's usual whitespace strip; ``OFF`` or ``off,x`` are refused by
#: ``parse_window`` like any other bad value. Blank is NOT a spelling of
#: "off" any more: blank means "not set" (use Settings, else the default).
WINDOW_OFF = "off"


def parse_window_or_off(raw: str) -> ScheduleWindow | None:
    """``parse_window`` plus the ``off`` keyword (D16b, Q7).

    Returns ``None`` for ``off`` (scheduler off; vault-core's rate cap then
    applies around the clock) and a ``ScheduleWindow`` for a valid window.
    A blank value is still a caller error here, exactly like in
    ``parse_window``: what blank means ("not set") is decided by the caller,
    ``config.Settings.from_env`` (blank = default window) or the settings
    API (a blank PATCH value is stored as ``off``, review decision B2;
    resetting is spelled ``null``).
    """
    if raw.strip() == WINDOW_OFF:
        return None
    return parse_window(raw)


def parse_window(raw: str) -> ScheduleWindow:
    """Parse ``"09:00-17:00"``. Raises ``ScheduleWindowError`` on anything else.

    An empty/whitespace-only value is a caller error here, not a window:
    what blank means ("not set", D16b/Q7) is a *configuration* decision and
    lives in ``config.Settings.from_env``, which never calls this for a blank
    value. Getting one anyway is a bug, so it raises like any other bad input.
    The ``off`` keyword is handled one level up, in ``parse_window_or_off``.
    """
    text = raw.strip()
    if not text:
        raise ScheduleWindowError(
            "the window is empty; expected 'HH:MM-HH:MM', or 'off' for "
            "no window"
        )

    parts = text.split("-")
    if len(parts) != 2:
        raise ScheduleWindowError(
            f"{raw!r} must contain exactly one '-' separating start and end, "
            "e.g. '09:00-17:00'"
        )

    start_minute = _parse_time_of_day(parts[0].strip(), allow_end_of_day=False)
    end_minute = _parse_time_of_day(parts[1].strip(), allow_end_of_day=True)

    if start_minute == end_minute:
        raise ScheduleWindowError(
            f"{raw!r} starts and ends at the same time, which is ambiguous "
            "(zero minutes, or the whole day?). Use '00:00-24:00' for a "
            "window that is always open."
        )

    return ScheduleWindow(start_minute=start_minute, end_minute=end_minute, raw=text)


def next_open(window: ScheduleWindow, moment: datetime) -> datetime:
    """The first instant at or after ``moment`` that is inside ``window``.

    Returns ``moment`` unchanged when it is already inside. Otherwise the next
    occurrence of the window's start — today's if it is still ahead, else
    tomorrow's (which for an overnight window is always today's, since being
    outside an overnight window means being in the ``[end, start)`` daytime
    gap that precedes it).

    **Advisory only.** This exists to fill ``next_eligible_at`` in
    ``GET /v1/schedule``; no sweep decision is ever made from it. That matters
    because the arithmetic below (``replace`` + ``timedelta(days=1)``) keeps
    ``moment``'s UTC offset, so a boundary computed across a DST transition
    can be an hour off. The scheduler itself re-reads the clock every tick and
    re-evaluates ``contains`` from scratch, so its behaviour is unaffected —
    only this displayed estimate can be. See ``scheduler.py``'s module
    docstring.
    """
    if window.contains(moment):
        return moment
    start_today = moment.replace(
        hour=window.start_minute // 60,
        minute=window.start_minute % 60,
        second=0,
        microsecond=0,
    )
    if start_today > moment:
        return start_today
    return start_today + timedelta(days=1)
