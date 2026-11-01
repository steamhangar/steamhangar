#!/usr/bin/env python3
"""DOC-DIET-1: prove that splitting the plan and the learnings lost no line.

`docs/PROJECT_PLAN.md` was split into a core plan plus `docs/plan-archive/`,
and `docs/LEARNINGS.md` into an index plus `docs/learnings/<area>.md`. This
check reads the OLD files from a git ref (default `wp/rc22-plan`, the branch
the split started from) and the NEW files from the working tree, then checks
per family that every non-empty old line (trailing whitespace ignored) still
occurs at least as often in the new set (multiset check). Lines that were
rewritten on purpose are listed in EXEMPT with the reason; nothing else may
go missing.

It also prints the sizes before and after against the DOC-DIET-1 targets
(core plan <= 70 KB, learnings index <= 8 KB, each area file <= 25 KB) and a
start-read estimate in tokens (bytes / 4) for a typical web and api package.

Usage (repo root):  python3 tools/doc-lines-check.py [--old-ref REF]
Exit status: 0 = no line lost, 1 = lines lost, 2 = usage / git error.
Size targets are reported, not enforced (see the printed verdict).
Stdlib only.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

PLAN = "docs/PROJECT_PLAN.md"
LEARN = "docs/LEARNINGS.md"
PLAN_ARCHIVE = "docs/plan-archive"
LEARN_DIR = "docs/learnings"

CORE_TARGET = 70 * 1024
INDEX_TARGET = 8 * 1024
AREA_TARGET = 25 * 1024

# Old lines that may be missing from the new set, with the reason. Keep this
# list explicit and short: every entry is a deliberate rewrite.
EXEMPT: dict[str, dict[str, str]] = {
    "plan": {
        "        in B and are ticked there, not repeated here":
            "rc22 block: B's open boxes moved to section 12, the line now "
            "says so (brief item 5)",
    },
    "learnings": {
        "Standing, project-proven findings. **Every coder and reviewer reads this":
            "index intro rewritten for the new read rule (brief item 3)",
        "before their first task of a session.** The orchestrator appends distilled":
            "index intro rewritten for the new read rule (brief item 3)",
        "findings after each review cycle — one line each, with the WP that proved it.":
            "index intro rewritten: append now goes to the area file",
        "These are not style preferences; each entry cost a review round to learn.":
            "index intro re-wrapped (the sentence is kept)",
    },
}


def norm_lines(text: str) -> list[str]:
    """Non-empty lines with trailing whitespace stripped."""
    return [ln.rstrip() for ln in text.splitlines() if ln.strip()]


def missing_lines(old: str, new_texts: list[str], exempt: dict[str, str]) -> list[tuple[str, int]]:
    """Old lines (with how many copies) that the new set does not contain often enough."""
    need = Counter(norm_lines(old))
    have: Counter[str] = Counter()
    for t in new_texts:
        have.update(norm_lines(t))
    lost = []
    for line, n in need.items():
        short = n - have.get(line, 0)
        if short > 0 and line not in exempt:
            lost.append((line, short))
    return lost


def git_show(ref: str, path: str) -> str:
    res = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "show", f"{ref}:{path}"],
        capture_output=True, text=True, encoding="utf-8",
    )
    if res.returncode != 0:
        raise SystemExit(f"git show {ref}:{path} failed: {res.stderr.strip()} "
                         "(fetch the ref or pass --old-ref)")
    return res.stdout


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def size(path: Path) -> int:
    return path.stat().st_size


def kb(n: int) -> str:
    return f"{n / 1024:6.1f} KB"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--old-ref", default="wp/rc22-plan",
                    help="git ref holding the pre-split files (default: %(default)s)")
    args = ap.parse_args(argv)

    old_plan = git_show(args.old_ref, PLAN)
    old_learn = git_show(args.old_ref, LEARN)

    core = REPO_ROOT / PLAN
    archive = sorted((REPO_ROOT / PLAN_ARCHIVE).glob("*.md"))
    index = REPO_ROOT / LEARN
    areas = sorted((REPO_ROOT / LEARN_DIR).glob("*.md"))
    if not archive or not areas:
        print("no archive or area files found", file=sys.stderr)
        return 2

    lost_plan = missing_lines(old_plan, [read(core)] + [read(p) for p in archive], EXEMPT["plan"])
    lost_learn = missing_lines(old_learn, [read(index)] + [read(p) for p in areas], EXEMPT["learnings"])

    print("== Lines (multiset, non-empty, trailing blanks ignored)")
    print(f"plan:      {len(norm_lines(old_plan))} old lines, lost {sum(n for _, n in lost_plan)}, "
          f"exempt {len(EXEMPT['plan'])}")
    print(f"learnings: {len(norm_lines(old_learn))} old lines, lost {sum(n for _, n in lost_learn)}, "
          f"exempt {len(EXEMPT['learnings'])}")
    for fam, lost in (("plan", lost_plan), ("learnings", lost_learn)):
        for line, n in lost:
            print(f"  LOST ({fam}) x{n}: {line}")

    print("\n== Sizes")
    old_p, old_l = len(old_plan.encode()), len(old_learn.encode())
    print(f"before: PROJECT_PLAN.md {kb(old_p)}   LEARNINGS.md {kb(old_l)}")
    c, i = size(core), size(index)
    print(f"after:  core plan       {kb(c)}  target <= {kb(CORE_TARGET)}  "
          f"{'ok' if c <= CORE_TARGET else 'OVER TARGET'}")
    print(f"        learnings index {kb(i)}  target <= {kb(INDEX_TARGET)}  "
          f"{'ok' if i <= INDEX_TARGET else 'OVER TARGET'}")
    for p in areas:
        s = size(p)
        print(f"        learnings/{p.name:<15} {kb(s)}  target <= {kb(AREA_TARGET)}  "
              f"{'ok' if s <= AREA_TARGET else 'OVER TARGET'}")
    arch_total = sum(size(p) for p in archive)
    print(f"        plan-archive ({len(archive)} files) {kb(arch_total)} (not read at start)")

    print("\n== Start read estimate (bytes / 4)")
    agents = sum(size(REPO_ROOT / ".claude/agents" / n) for n in ("coder.md",))
    base_old = old_p + old_l
    by_name = {p.stem: size(p) for p in areas}
    for label, picks in (("web package", ("process", "testing", "web")),
                         ("api package", ("process", "testing", "api"))):
        new = c + i + sum(by_name.get(n, 0) for n in picks)
        print(f"{label}: before ~{base_old // 4:,} tokens, after ~{new // 4:,} tokens "
              f"(core + index + {', '.join(n + '.md' for n in picks)}; agent file "
              f"~{agents // 4} tokens extra either way)")

    if lost_plan or lost_learn:
        print("\nVERDICT: FAIL, lines lost")
        return 1
    print("\nVERDICT: PASS, no line lost")
    return 0


if __name__ == "__main__":
    sys.exit(main())
