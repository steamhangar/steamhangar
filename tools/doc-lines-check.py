#!/usr/bin/env python3
"""DOC-DIET-1: prove that splitting the plan and the learnings lost no line.

`docs/PROJECT_PLAN.md` was split into a core plan plus `docs/plan-archive/`,
and `docs/LEARNINGS.md` into an index plus `docs/learnings/<area>.md`. This
check reads the OLD files from `--old-ref` and the NEW files from
`--new-ref`, then checks per family that every non-empty old line (trailing
whitespace ignored) still occurs at least as often in the new set (multiset
check). Lines that were rewritten on purpose are listed in EXEMPT with the
reason; nothing else may go missing.

Both refs default to pinned commits, so the run is a reproducible proof of
the split itself (DOC-LINES-CHECK-1): `--old-ref` defaults to the pre-split
commit (`wp/rc22-plan` when the split started, the split commit's parent)
and `--new-ref` to the DOC-DIET-1 split commit (its merge `fec7af5` has the
same docs). Later packages rewrite and tick plan lines on purpose, so the
working tree is NOT the default: `--new-ref WORKTREE` reads the new files
from the working tree, for someone who re-splits and wants to prove that
run (expect later edits to show as lost lines on a current checkout).

It also prints the sizes of the new set against the DOC-DIET-1 targets
(core plan <= 70 KB, learnings index <= 8 KB, each area file <= 25 KB) and a
start-read estimate in tokens (bytes / 4) for a typical web and api package.

Usage (repo root):
  python3 tools/doc-lines-check.py [--old-ref REF] [--new-ref REF|WORKTREE]
Exit status: 0 = no line lost, 1 = lines lost, 2 = usage / git error, also
when a ref is missing from the clone (a shallow clone, e.g. CI's default
`actions/checkout` depth 1, lacks both pinned commits: fetch the history
with `git fetch --unshallow`, or pass refs that exist).
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

# Pre-split commit: the tip of `wp/rc22-plan` when DOC-DIET-1 started, and the
# parent of the split commit. Pinned by hash: a fresh clone has the branch only
# as `origin/wp/rc22-plan`, and a branch may move.
OLD_REF = "86299c56842efa41e4f1c72ef2076989021fa98e"
# DOC-DIET-1 split commit (892f708, "docs: split plan into core and archive,
# index learnings by area"); merged as fec7af5 with identical docs and tools.
SPLIT_REF = "892f708e9e1e2193d7ee9dcda36a324d92535890"
WORKTREE = "WORKTREE"

# Old lines that may be missing from the new set, with the reason. Keep this
# list explicit and short: every entry is a deliberate rewrite.
EXEMPT: dict[str, dict[str, str]] = {
    "plan": {
        "        in B and are ticked there, not repeated here":
            "rc22 block: B's open boxes moved to section 12, the line now "
            "says so (brief item 5)",
        "      - [ ] **DOC-DIET-1** (docs + agents, user decision 2026-11-01,":
            "DOC-DIET-1 ticked its own box in the split commit 892f708",
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


class RefError(Exception):
    """A git ref or a file in it is unavailable (exit 2)."""


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args],
            capture_output=True, text=True, encoding="utf-8",
        )
    except FileNotFoundError as exc:
        raise RefError(f"git is not available ({exc})") from exc
    except UnicodeDecodeError as exc:
        raise RefError(f"git {' '.join(args)}: output is not UTF-8 ({exc})") from exc


def require_commit(ref: str) -> None:
    """Fail with a clear message when `ref` is not a commit in this clone."""
    res = _git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    if res.returncode != 0:
        raise RefError(
            f"ref {ref!r} is not a commit in this clone. A shallow clone "
            "(CI's default checkout, depth 1) lacks the pinned DOC-DIET-1 "
            "commits: run `git fetch --unshallow` (or fetch the ref), or pass "
            "--old-ref/--new-ref refs that exist.")


class Source:
    """The files of one side, read from a git commit or the working tree."""

    def __init__(self, ref: str) -> None:
        self.ref = ref
        if ref != WORKTREE:
            require_commit(ref)

    @property
    def label(self) -> str:
        return "working tree" if self.ref == WORKTREE else self.ref

    def read(self, path: str) -> str:
        if self.ref == WORKTREE:
            try:
                return (REPO_ROOT / path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise RefError(f"cannot read {path} from the working tree ({exc})") from exc
        res = _git("show", f"{self.ref}:{path}")
        if res.returncode != 0:
            raise RefError(f"git show {self.ref}:{path} failed: {res.stderr.strip()}")
        return res.stdout

    def size(self, path: str) -> int:
        return len(self.read(path).encode("utf-8"))

    def md_files(self, directory: str) -> list[str]:
        """Sorted repo-relative paths of the *.md files directly in `directory`."""
        if self.ref == WORKTREE:
            return sorted(p.relative_to(REPO_ROOT).as_posix()
                          for p in (REPO_ROOT / directory).glob("*.md"))
        res = _git("ls-tree", "-z", "--name-only", f"{self.ref}:{directory}")
        if res.returncode != 0:
            raise RefError(f"git ls-tree {self.ref}:{directory} failed: {res.stderr.strip()}")
        return sorted(f"{directory}/{n}" for n in res.stdout.split("\0") if n.endswith(".md"))


def kb(n: int) -> str:
    return f"{n / 1024:6.1f} KB"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--old-ref", default=OLD_REF,
                    help="git ref holding the pre-split files (default: the "
                         "pre-split commit %(default)s, wp/rc22-plan at split time)")
    ap.add_argument("--new-ref", default=SPLIT_REF,
                    help="git ref holding the split result, or WORKTREE for the "
                         "working tree (default: the DOC-DIET-1 split commit "
                         "%(default)s)")
    args = ap.parse_args(argv)
    try:
        return run(Source(args.old_ref), Source(args.new_ref))
    except RefError as exc:
        print(f"doc-lines-check: {exc}", file=sys.stderr)
        return 2


def run(old: Source, new: Source) -> int:
    old_plan = old.read(PLAN)
    old_learn = old.read(LEARN)

    archive = new.md_files(PLAN_ARCHIVE)
    areas = new.md_files(LEARN_DIR)
    if not archive or not areas:
        print(f"no archive or area files found in {new.label}", file=sys.stderr)
        return 2

    lost_plan = missing_lines(old_plan, [new.read(PLAN)] + [new.read(p) for p in archive],
                              EXEMPT["plan"])
    lost_learn = missing_lines(old_learn, [new.read(LEARN)] + [new.read(p) for p in areas],
                               EXEMPT["learnings"])

    print(f"old: {old.label}")
    print(f"new: {new.label}")
    print("\n== Lines (multiset, non-empty, trailing blanks ignored)")
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
    c, i = new.size(PLAN), new.size(LEARN)
    print(f"after:  core plan       {kb(c)}  target <= {kb(CORE_TARGET)}  "
          f"{'ok' if c <= CORE_TARGET else 'OVER TARGET'}")
    print(f"        learnings index {kb(i)}  target <= {kb(INDEX_TARGET)}  "
          f"{'ok' if i <= INDEX_TARGET else 'OVER TARGET'}")
    by_name: dict[str, int] = {}
    for p in areas:
        s = new.size(p)
        name = Path(p).name
        by_name[Path(p).stem] = s
        print(f"        learnings/{name:<15} {kb(s)}  target <= {kb(AREA_TARGET)}  "
              f"{'ok' if s <= AREA_TARGET else 'OVER TARGET'}")
    arch_total = sum(new.size(p) for p in archive)
    print(f"        plan-archive ({len(archive)} files) {kb(arch_total)} (not read at start)")

    print("\n== Start read estimate (bytes / 4)")
    agents = new.size(".claude/agents/coder.md")
    base_old = old_p + old_l
    for label, picks in (("web package", ("process", "testing", "web")),
                         ("api package", ("process", "testing", "api"))):
        est = c + i + sum(by_name.get(n, 0) for n in picks)
        print(f"{label}: before ~{base_old // 4:,} tokens, after ~{est // 4:,} tokens "
              f"(core + index + {', '.join(n + '.md' for n in picks)}; agent file "
              f"~{agents // 4} tokens extra either way)")

    if lost_plan or lost_learn:
        print("\nVERDICT: FAIL, lines lost")
        return 1
    print("\nVERDICT: PASS, no line lost")
    return 0


if __name__ == "__main__":
    sys.exit(main())
