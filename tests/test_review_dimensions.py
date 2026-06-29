"""Review dimension classifier and clustering (no LLM)."""

from unittest.mock import patch

from tests.golden.loader import build_fixture_repo

from pr_review_agent.review_dimensions import (
    ChangeWeight,
    ReviewDimension,
    _build_clusters,
    build_pr_dimension_plan,
    classify_change_weight,
    classify_file,
    deletes_guard,
    extract_api_symbols_from_diff,
    extract_preflight_symbols,
    filter_api_like_files,
    is_api_like_change,
    is_lock_file,
    needs_caller_preflight,
)


def test_is_lock_file():
    assert is_lock_file("package-lock.json") is True
    assert is_lock_file("src/foo.py") is False


def test_classify_lock():
    assert classify_file("package-lock.json") == ReviewDimension.LOCK


def test_classify_meta_doc():
    assert classify_file("README.md") == ReviewDimension.META


def test_classify_meta_config():
    assert classify_file("deploy/k8s.yaml") == ReviewDimension.META


def test_classify_code_with_api_diff():
    diff = """\
--- a/api.py
+++ b/api.py
@@ -1,3 +1,3 @@
-def get_user(user_id: int) -> dict:
+def get_user(user_id: str) -> dict:
     return {"id": user_id}
"""
    assert classify_file("api.py", diff) == ReviewDimension.CODE
    assert is_api_like_change(diff) is True


def test_classify_security_from_path():
    assert classify_file("app/auth/login.py", "x = 1\n") == ReviewDimension.SECURITY


def test_classify_code_default():
    assert classify_file("utils.py", "x = 1\n") == ReviewDimension.CODE


def test_extract_api_symbols():
    diff = "+def get_user(user_id: str) -> dict:\n+    pass\n"
    assert extract_api_symbols_from_diff(diff) == ["get_user"]


def test_extract_preflight_symbols_class_rename():
    diff = "-class OldService:\n+class NewService:\n"
    names = {sym.name: sym.kind for sym in extract_preflight_symbols(diff)}
    assert names["OldService"] == "legacy_class"


def test_extract_preflight_symbols_inheritance_change():
    diff = "-class Foo(Bar):\n+class Foo(Baz):\n"
    names = {sym.name: sym.kind for sym in extract_preflight_symbols(diff)}
    assert names["Foo"] == "class"


def test_extract_preflight_symbols_deleted_public_method():
    diff = "-    def publish(self, msg):\n-        pass\n"
    names = {sym.name: sym.kind for sym in extract_preflight_symbols(diff)}
    assert names["publish"] == "function"


def test_extract_preflight_symbols_init_and_class(tmp_path):
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
    names = {
        sym.name: sym.kind
        for sym in extract_preflight_symbols(diff, "svc.py", repo)
    }
    assert names["UserService"] == "class"
    assert "__init__" not in names


def test_needs_caller_preflight_for_class_inheritance():
    diff = "-class Foo(Bar):\n+class Foo(Baz):\n"
    assert needs_caller_preflight(diff) is True


def test_classify_weight_trivial_comment_only():
    diff = "-    # old note\n+    # new note\n"
    assert (
        classify_change_weight("a.py", diff, ReviewDimension.CODE)
        == ChangeWeight.TRIVIAL
    )


def test_classify_weight_heavy_delete_none_check():
    diff = "-    if name is None:\n-        raise ValueError('n')\n"
    assert deletes_guard(diff) is True
    assert (
        classify_change_weight("utils.py", diff, ReviewDimension.CODE)
        == ChangeWeight.HEAVY
    )


def test_classify_weight_heavy_api_like_diff():
    diff = "+def get_user(user_id: str) -> dict:\n+    pass\n"
    assert is_api_like_change(diff) is True
    assert (
        classify_change_weight("api.py", diff, ReviewDimension.CODE)
        == ChangeWeight.HEAVY
    )


def test_classify_weight_normal_small_logic_change():
    diff = "-    return x\n+    return x + 1\n"
    assert (
        classify_change_weight("calc.py", diff, ReviewDimension.CODE)
        == ChangeWeight.NORMAL
    )


def test_logic_trivial_vs_heavy_split():
    diffs = {
        "c1.py": "-# a\n+# b\n",
        "c2.py": "+# c\n",
        "c3.py": "+# d\n",
        "c4.py": "+# e\n",
        "c5.py": "+# f\n",
        "none.py": "-    if name is None:\n-        raise ValueError('n')\n",
        "big.py": "\n".join(f"+line {i}" for i in range(250)),
    }
    by_dim: dict[ReviewDimension, list[str]] = {d: [] for d in ReviewDimension}
    by_dim[ReviewDimension.CODE] = list(diffs.keys())

    clusters = _build_clusters(by_dim, diffs_by_path=diffs)
    trivial = [c for c in clusters if c.weight == ChangeWeight.TRIVIAL]
    heavy = [c for c in clusters if c.weight == ChangeWeight.HEAVY]

    assert len(trivial) == 1
    assert set(trivial[0].files) == {"c1.py", "c2.py", "c3.py", "c4.py", "c5.py"}
    assert len(heavy) == 1
    assert set(heavy[0].files) == {"none.py", "big.py"}


def test_heavy_guard_deletes_batch_together():
    diffs = {
        "a.py": "-    if name is None:\n-        raise ValueError('n')\n",
        "b.py": "-    if not items:\n-        return []\n",
    }
    by_dim: dict[ReviewDimension, list[str]] = {d: [] for d in ReviewDimension}
    by_dim[ReviewDimension.CODE] = list(diffs.keys())
    clusters = _build_clusters(by_dim, diffs_by_path=diffs)
    heavy = [c for c in clusters if c.weight == ChangeWeight.HEAVY]
    assert len(heavy) == 1
    assert set(heavy[0].files) == {"a.py", "b.py"}


def test_heavy_oversized_diff_still_solo():
    huge = "\n".join(f"+fill line {i} with padding\n" for i in range(500))
    assert len(huge) > 4000
    diffs = {"huge.py": huge, "small.py": "-    if x is None:\n"}
    by_dim: dict[ReviewDimension, list[str]] = {d: [] for d in ReviewDimension}
    by_dim[ReviewDimension.CODE] = list(diffs.keys())
    clusters = _build_clusters(by_dim, diffs_by_path=diffs)
    heavy = [c for c in clusters if c.weight == ChangeWeight.HEAVY]
    assert len(heavy) == 2
    solo = [c for c in heavy if c.files == ["huge.py"]]
    batched = [c for c in heavy if set(c.files) == {"small.py"}]
    assert len(solo) == 1
    assert len(batched) == 1


def test_build_plan_lock_only(tmp_path):
    repo = build_fixture_repo("lock_only", tmp_path / "lock_only")
    plan = build_pr_dimension_plan(repo, "main")
    assert plan.lock_only is True
    assert plan.reviewable_files == []
    assert ReviewDimension.LOCK in plan.files_by_dimension


def test_build_plan_cross_file_api(tmp_path):
    repo = build_fixture_repo("cross_file_api", tmp_path / "cross_file_api")
    plan = build_pr_dimension_plan(repo, "main")
    assert plan.lock_only is False
    assert "api.py" in plan.reviewable_files
    code_files = plan.files_by_dimension.get(ReviewDimension.CODE, [])
    assert "api.py" in code_files
    assert len(plan.clusters) >= 1
    assert all(c.weight == ChangeWeight.HEAVY for c in plan.clusters if "api.py" in c.files)
    api_like = filter_api_like_files(repo, "main", code_files)
    assert "api.py" in api_like


def test_clusters_batch_normal_logic_files():
    paths = [f"f{i}.py" for i in range(10)]
    diffs = {p: "+x = 1\n" for p in paths}
    by_dim: dict[ReviewDimension, list[str]] = {d: [] for d in ReviewDimension}
    by_dim[ReviewDimension.CODE] = paths
    clusters = _build_clusters(by_dim, diffs_by_path=diffs)
    assert len(clusters) == 2
    assert all(c.dimension == ReviewDimension.CODE for c in clusters)
    assert all(c.weight == ChangeWeight.NORMAL for c in clusters)
    assert sum(len(c.files) for c in clusters) == 10


@patch("pr_review_agent.review_dimensions.diff_one_file", return_value="+x\n")
def test_build_plan_assigns_normal_weight_by_default(_mock_diff, tmp_path):
    repo = build_fixture_repo("obvious_none", tmp_path / "obvious_none")
    plan = build_pr_dimension_plan(repo, "main")
    code_clusters = [c for c in plan.clusters if c.dimension == ReviewDimension.CODE]
    assert code_clusters
    assert all(c.weight == ChangeWeight.NORMAL for c in code_clusters)
