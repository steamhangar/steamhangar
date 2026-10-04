"""Steam tool apps: installed everywhere, never prefilled (WP API-FIX-4).

The single source of truth for "which app ids are tool packages". Every
place that decides whether to prefill an app reads this module, and so do
the game endpoints that tell the UIs how to render one.

Why it exists (production, 2026-10-04): agents report app 228980,
"Steamworks Common Redistributables", as installed on every Windows PC,
because Steam installs it next to games (DirectX, VC++ and .NET packages).
The scheduler prefilled every reported app, SteamPrefill cannot prefill
228980 (it is not an owned app), so its job always ended ``error`` and the
library showed "App 228980 / Failed / Retry download". Nothing was missing:
its depots are shared depots that Steam pulls together with each game, so
they are cached whenever a game that uses them is cached (see api/README.md
"Steam tool apps").

User decision 2026-10-04 (Weg A): vault-api keeps a small fixed list here.
Agents keep reporting the truth; vault-api decides not to prefill these.

What reads this module:

* ``scheduler.compute_targets`` drops tool apps from both target sources
  (installed and cached);
* ``event_sweep.run_miss_trigger`` skips them before any other guard;
* ``POST /v1/prefill/cached`` leaves them out of its selection;
* ``POST /v1/prefill`` rejects a body naming one with ``422``;
* ``jobs.enqueue_prefill`` refuses them as the last line
  (``ToolAppNotPrefillable``), so a new enqueue path cannot forget the rule;
* ``GET /v1/games`` / ``GET /v1/games/{appid}`` add ``tool_app`` and
  ``tool_app_name`` to each row.

Adding an app here is a code change on purpose: it changes what the
scheduler downloads, so it goes through review, not a setting.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class ToolApp:
    """One Steam tool package that vault-api never prefills."""

    appid: int
    #: Display name for UIs; vault-api has no other source for it (the app
    #: is not in any owned-games list).
    name: str
    #: Why it is never prefilled, for logs and docs.
    reason: str


_TOOL_APPS: dict[int, ToolApp] = {
    228980: ToolApp(
        appid=228980,
        name="Steamworks Common Redistributables",
        reason=(
            "Steam installs it next to games (DirectX, VC++ and .NET "
            "packages); it is not an owned app, so SteamPrefill cannot "
            "prefill it, and its depots are cached together with the games "
            "that use them"
        ),
    ),
}

#: Read-only view of every tool app, keyed by app id.
TOOL_APPS: Mapping[int, ToolApp] = MappingProxyType(_TOOL_APPS)


class ToolAppNotPrefillable(ValueError):
    """Raised by ``jobs.enqueue_prefill`` for a tool app."""

    def __init__(self, appid: int) -> None:
        self.appid = appid
        super().__init__(reject_detail(appid))


def get_tool_app(appid: int) -> ToolApp | None:
    """The tool-app entry for ``appid``, or ``None`` for an ordinary app."""
    return TOOL_APPS.get(appid)


def is_tool_app(appid: int) -> bool:
    """True when ``appid`` is a tool app that must never be prefilled."""
    return appid in TOOL_APPS


def split_tool_apps(appids: Iterable[int]) -> tuple[set[int], tuple[int, ...]]:
    """Split ``appids`` into (ordinary apps, sorted tool apps found)."""
    ordinary: set[int] = set()
    tools: set[int] = set()
    for appid in appids:
        (tools if appid in TOOL_APPS else ordinary).add(appid)
    return ordinary, tuple(sorted(tools))


def reject_detail(appid: int) -> str:
    """The ``422`` detail ``POST /v1/prefill`` answers for a tool app."""
    tool = TOOL_APPS.get(appid)
    name = tool.name if tool is not None else f"App {appid}"
    return (
        f"App {appid} ({name}) is a Steam tool package, it is cached together "
        "with the games that use it. It is never prefilled on its own."
    )
