"""WP DOC-DIET-1: the plan/learnings split and its no-loss check.

`tools/doc-lines-check.py` proves the split against the pre-split files in
git. That ref is not available in every clone (CI checks out shallow), so
this module pins the checker's logic on synthetic text and the durable
invariants of the new layout in the working tree:

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
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL = REPO_ROOT / "tools" / "doc-lines-check.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("doc_lines_check", TOOL)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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
