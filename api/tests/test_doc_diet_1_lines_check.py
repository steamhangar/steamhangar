"""WP DOC-DIET-1: the plan/learnings split and its no-loss check.

`tools/doc-lines-check.py` proves the split by comparing two pinned commits
(the pre-split commit and the DOC-DIET-1 split commit, DOC-LINES-CHECK-1),
not the working tree, which later packages edit on purpose. Those commits
are not in every clone (CI checks out shallow, depth 1), so the end-to-end
proof runs only where the history is present (the devbox clone) and skips
with the reason otherwise. Everything else runs without history:

- the defaults are the pinned commits and `--new-ref WORKTREE` selects the
  working tree; a ref missing from the clone exits 2 with a clear message,
- the multiset check reports a dropped line, a dropped duplicate and a
  changed line, ignores blank lines and trailing whitespace, honours the
  explicit exempt list, and accepts a line that merely moved files,
- the learnings index stays <= 8 KB and every area file <= 25 KB (the
  index tells the orchestrator to split an area before it grows past it),
- every area file and archive file is linked from its index, and the core
  plan points to the archive and keeps section 12 (carried-over items).
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL = REPO_ROOT / "tools" / "doc-lines-check.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("doc_lines_check", TOOL)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _has_commit(ref: str) -> bool:
    if shutil.which("git") is None:
        return False
    res = subprocess.run(["git", "-C", str(REPO_ROOT), "cat-file", "-e", f"{ref}^{{commit}}"],
                         capture_output=True)
    return res.returncode == 0


def _needs_split_history(m: ModuleType) -> None:
    if not (_has_commit(m.OLD_REF) and _has_commit(m.SPLIT_REF)):
        pytest.skip("pinned DOC-DIET-1 commits not in this clone (shallow checkout or no git)")


def test_defaults_are_the_pinned_split_commits_and_worktree_is_opt_in(monkeypatch) -> None:
    m = _load()
    assert re.fullmatch(r"[0-9a-f]{40}", m.OLD_REF)
    assert re.fullmatch(r"[0-9a-f]{40}", m.SPLIT_REF)
    seen: list[tuple[str, str]] = []

    class FakeSource:
        def __init__(self, ref: str) -> None:
            self.ref = ref

    monkeypatch.setattr(m, "Source", FakeSource)
    monkeypatch.setattr(m, "run", lambda old, new: seen.append((old.ref, new.ref)) or 0)
    assert m.main([]) == 0
    assert m.main(["--new-ref", "WORKTREE"]) == 0
    assert m.main(["--old-ref", "abc", "--new-ref", "def"]) == 0
    assert seen == [(m.OLD_REF, m.SPLIT_REF), (m.OLD_REF, m.WORKTREE), ("abc", "def")]


def test_missing_ref_exits_2_and_says_why(capsys) -> None:
    m = _load()
    missing = "0" * 40
    assert m.main(["--old-ref", missing, "--new-ref", "WORKTREE"]) == 2
    err = capsys.readouterr().err
    if shutil.which("git") is None:
        assert "git is not available" in err
    else:
        assert f"ref {missing!r} is not a commit in this clone" in err
        assert "git fetch --unshallow" in err


def test_worktree_source_reads_the_working_tree() -> None:
    m = _load()
    src = m.Source(m.WORKTREE)
    assert src.label == "working tree"
    assert src.read(m.PLAN) == (REPO_ROOT / m.PLAN).read_text(encoding="utf-8")
    assert src.md_files(m.LEARN_DIR) == sorted(
        f"{m.LEARN_DIR}/{p.name}" for p in (REPO_ROOT / m.LEARN_DIR).glob("*.md"))


def test_head_source_reads_git_in_a_shallow_clone_too() -> None:
    # HEAD exists in every clone (CI's depth-1 checkout included), so the git
    # branch of Source runs everywhere, not only where the history is present.
    m = _load()
    if shutil.which("git") is None:
        pytest.skip("no git")
    src = m.Source("HEAD")
    assert src.label == "HEAD"
    for path in (m.PLAN, m.LEARN):
        shown = subprocess.run(["git", "-C", str(REPO_ROOT), "show", f"HEAD:{path}"],
                               capture_output=True, text=True, encoding="utf-8",
                               check=True).stdout
        assert src.read(path) == shown
        assert src.size(path) == len(shown.encode("utf-8"))
    listed = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-tree", "--name-only",
                             f"HEAD:{m.LEARN_DIR}"], capture_output=True, text=True,
                            encoding="utf-8", check=True).stdout.splitlines()
    assert src.md_files(m.LEARN_DIR) == sorted(
        f"{m.LEARN_DIR}/{n}" for n in listed if n.endswith(".md"))
    assert f"{m.LEARN_DIR}/process.md" in src.md_files(m.LEARN_DIR)


def test_unreadable_worktree_file_exits_2(monkeypatch, capsys) -> None:
    m = _load()

    def boom(self, encoding="utf-8"):
        raise OSError("simulated read error")

    monkeypatch.setattr(m.Path, "read_text", boom)
    assert m.main(["--old-ref", "WORKTREE", "--new-ref", "WORKTREE"]) == 2
    err = capsys.readouterr().err
    assert "cannot read docs/PROJECT_PLAN.md from the working tree" in err
    assert "simulated read error" in err


def test_non_utf8_git_output_exits_2(monkeypatch, capsys) -> None:
    m = _load()

    def undecodable(*args, **kwargs):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(m.subprocess, "run", undecodable)
    assert m.main(["--new-ref", "WORKTREE"]) == 2
    assert "output is not UTF-8" in capsys.readouterr().err


def test_split_commit_source_reads_git_not_the_working_tree() -> None:
    m = _load()
    _needs_split_history(m)
    src = m.Source(m.SPLIT_REF)
    areas = src.md_files(m.LEARN_DIR)
    assert f"{m.LEARN_DIR}/process.md" in areas
    assert all(a.endswith(".md") for a in areas)
    assert f"{m.PLAN_ARCHIVE}/README.md" in src.md_files(m.PLAN_ARCHIVE)
    shown = subprocess.run(["git", "-C", str(REPO_ROOT), "show", f"{m.SPLIT_REF}:{m.PLAN}"],
                           capture_output=True, text=True, encoding="utf-8", check=True).stdout
    assert src.read(m.PLAN) == shown


def test_default_run_proves_the_split_lost_no_line(capsys) -> None:
    m = _load()
    _needs_split_history(m)
    rc = m.main([])
    out = capsys.readouterr().out
    assert "VERDICT: PASS, no line lost" in out, out
    assert rc == 0


def test_moved_lines_pass_and_blank_or_trailing_space_is_ignored() -> None:
    m = _load()
    old = "# Title\n\n- [ ] item a\n- [x] item b  \n"
    assert m.missing_lines(old, ["# Title\n- [x] item b\n", "\n\n- [ ] item a\n"], {}) == []


def test_dropped_line_is_reported() -> None:
    m = _load()
    old = "keep\ndrop me\n"
    assert m.missing_lines(old, ["keep\n"], {}) == [("drop me", 1)]


def test_dropped_duplicate_is_reported() -> None:
    m = _load()
    old = "- [ ] nit\n- [ ] nit\n"
    assert m.missing_lines(old, ["- [ ] nit\n"], {}) == [("- [ ] nit", 1)]


def test_changed_line_is_reported_unless_exempt() -> None:
    m = _load()
    old = "  in B and are ticked there\n"
    new = ["  in section 12 and are ticked there\n"]
    assert m.missing_lines(old, new, {}) == [("  in B and are ticked there", 1)]
    assert m.missing_lines(old, new, {"  in B and are ticked there": "why"}) == []


def test_learnings_index_and_area_sizes() -> None:
    m = _load()
    index = REPO_ROOT / "docs" / "LEARNINGS.md"
    assert index.stat().st_size <= m.INDEX_TARGET
    areas = sorted((REPO_ROOT / "docs" / "learnings").glob("*.md"))
    assert areas, "docs/learnings/ has no area files"
    for p in areas:
        assert p.stat().st_size <= m.AREA_TARGET, f"{p.name} is over 25 KB, split the area"


def test_every_area_and_archive_file_is_indexed() -> None:
    index = (REPO_ROOT / "docs" / "LEARNINGS.md").read_text(encoding="utf-8")
    for p in (REPO_ROOT / "docs" / "learnings").glob("*.md"):
        assert f"(learnings/{p.name})" in index, p.name
    readme = (REPO_ROOT / "docs" / "plan-archive" / "README.md").read_text(encoding="utf-8")
    for p in (REPO_ROOT / "docs" / "plan-archive").glob("*.md"):
        if p.name != "README.md":
            assert f"]({p.name})" in readme, p.name


def test_core_plan_links_archive_and_keeps_carried_over_section() -> None:
    core = (REPO_ROOT / "docs" / "PROJECT_PLAN.md").read_text(encoding="utf-8")
    assert "plan-archive/README.md" in core
    assert re.search(r"^## 12\. Carried-over open items$", core, re.M)
    for link in re.findall(r"\]\((plan-archive/[^)]+)\)", core):
        assert (REPO_ROOT / "docs" / link).is_file(), link
