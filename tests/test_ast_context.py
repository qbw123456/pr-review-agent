"""Tests for AST-based changed-symbol context extraction."""

from __future__ import annotations

from pathlib import Path

from pr_review_agent.ast_context import (
    class_names_with_init_changed,
    extract_caller_slices_for_symbol,
    extract_slices_for_file,
    find_slices_for_lines,
    find_symbol_reference_lines,
    format_ast_context_block,
    format_caller_ast_context_block,
    parse_diff_new_line_numbers,
)
from pr_review_agent.review_dimensions import find_callers_for_api_files
from tests.golden.loader import build_fixture_repo

GREET_SOURCE = '''"""String helpers."""


def greet(name: str) -> str:
    """Return a greeting for the given name."""
    return f"Hello, {name.upper()}"
'''

DELETE_NONE_DIFF = """\
diff --git a/utils.py b/utils.py
--- a/utils.py
+++ b/utils.py
@@ -4,7 +4,5 @@
 def greet(name: str) -> str:
     \"\"\"Return a greeting for the given name.\"\"\"
-    if name is None:
-        raise ValueError("name cannot be None")
     return f"Hello, {name.upper()}"
"""


def test_parse_diff_new_lines_on_deletion():
    changed = parse_diff_new_line_numbers(DELETE_NONE_DIFF)
    assert 6 in changed


def test_find_greet_slice_after_none_removal():
    changed = parse_diff_new_line_numbers(DELETE_NONE_DIFF)
    slices = find_slices_for_lines(GREET_SOURCE, "utils.py", changed)
    assert len(slices) == 1
    assert slices[0].name == "greet"
    assert slices[0].kind == "function"
    assert "if name is None" not in slices[0].source
    assert "return f" in slices[0].source


def test_format_ast_block_from_fixture(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "utils.py").write_text(GREET_SOURCE, encoding="utf-8")
    block = format_ast_context_block(repo, [("utils.py", DELETE_NONE_DIFF)])
    assert "AST 变更区域" in block
    assert "`greet`" in block
    assert "def greet" in block


def test_extract_slices_skips_non_python(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.js").write_text("function f() {}", encoding="utf-8")
    assert extract_slices_for_file(repo, "app.js", "+function f() {}\n") == []


def test_module_level_fallback():
    source = "X = 1\nY = 2\n"
    slices = find_slices_for_lines(source, "cfg.py", {2})
    assert len(slices) == 1
    assert slices[0].kind == "module"


def test_obvious_none_golden_fixture(tmp_path):
    repo = build_fixture_repo("obvious_none", tmp_path / "obvious_none")
    from pr_review_agent.git_utils import diff_one_file

    diff = diff_one_file(repo, "main", "utils.py")
    block = format_ast_context_block(repo, [("utils.py", diff)])
    assert "greet" in block
    assert "if name is None" not in block


CLIENT_SOURCE = '''"""API client."""

from api import get_user


def fetch_demo_user() -> dict:
    return get_user(1)
'''


def test_find_symbol_reference_lines_in_caller():
    lines = find_symbol_reference_lines(CLIENT_SOURCE, "client.py", "get_user")
    assert 7 in lines


def test_extract_caller_slice_fetch_demo_user(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "client.py").write_text(CLIENT_SOURCE, encoding="utf-8")
    slices = extract_caller_slices_for_symbol(repo, "client.py", "get_user")
    assert len(slices) == 1
    assert slices[0].name == "fetch_demo_user"
    assert slices[0].related_symbol == "get_user"
    assert "get_user(1)" in slices[0].source
    assert "from api import get_user" not in slices[0].source


def test_find_symbol_reference_lines_for_class_instantiation():
    source = "from svc import UserService\n\n\ndef build() -> UserService:\n    return UserService(1)\n"
    lines = find_symbol_reference_lines(source, "client.py", "UserService")
    assert any(line for line in lines)


def test_class_names_with_init_changed(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "svc.py").write_text(
        "class UserService:\n"
        "    def __init__(self, user_id: int):\n"
        "        self.user_id = user_id\n",
        encoding="utf-8",
    )
    diff = """\
@@ -1,3 +1,3 @@
 class UserService:
-    def __init__(self, user_id: int):
+    def __init__(self, user_id: str):
         self.user_id = user_id
"""
    assert class_names_with_init_changed(repo, "svc.py", diff) == ["UserService"]


def test_extract_caller_slice_for_class_usage(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "client.py").write_text(
        "from svc import UserService\n\n\ndef build() -> UserService:\n    return UserService(1)\n",
        encoding="utf-8",
    )
    slices = extract_caller_slices_for_symbol(repo, "client.py", "UserService")
    assert len(slices) == 1
    assert slices[0].name == "build"
    assert "UserService(1)" in slices[0].source


def test_cross_file_api_caller_ast_golden(tmp_path):
    repo = build_fixture_repo("cross_file_api", tmp_path / "cross_file_api")
    from pr_review_agent.git_utils import diff_one_file

    caller_map = find_callers_for_api_files(repo, "main", ["api.py"])
    assert "get_user" in caller_map
    assert "client.py" in caller_map["get_user"]

    block = format_caller_ast_context_block(repo, caller_map)
    assert "AST 调用方切片" in block
    assert "fetch_demo_user" in block
    assert "get_user(1)" in block
    assert "def get_user" not in block

    diff = diff_one_file(repo, "main", "api.py")
    def_block = format_ast_context_block(repo, [("api.py", diff)])
    assert "def get_user" in def_block
