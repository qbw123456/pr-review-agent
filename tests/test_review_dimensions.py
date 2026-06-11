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
    is_lock_file,
)


def test_is_lock_file():
    assert is_lock_file("package-lock.json") is True
    assert is_lock_file("src/foo.py") is False


def test_classify_lock():
    assert classify_file("package-lock.json") == ReviewDimension.LOCK


def test_classify_doc():
    assert classify_file("README.md") == ReviewDimension.DOC


def test_classify_config():
    assert classify_file("deploy/k8s.yaml") == ReviewDimension.CONFIG


def test_classify_api_from_diff():
    diff = """\
--- a/api.py
+++ b/api.py
@@ -1,3 +1,3 @@
-def get_user(user_id: int) -> dict:
+def get_user(user_id: str) -> dict:
     return {"id": user_id}
"""
    assert classify_file("api.py", diff) == ReviewDimension.API


def test_classify_security_from_path():
    assert classify_file("app/auth/login.py", "x = 1\n") == ReviewDimension.SECURITY


def test_classify_logic_default():
    assert classify_file("utils.py", "x = 1\n") == ReviewDimension.LOGIC


def test_extract_api_symbols():
    diff = "+def get_user(user_id: str) -> dict:\n+    pass\n"
    assert extract_api_symbols_from_diff(diff) == ["get_user"]


def test_classify_weight_trivial_comment_only():
    diff = "-    # old note\n+    # new note\n"
    assert (
        classify_change_weight("a.py", diff, ReviewDimension.LOGIC)
        == ChangeWeight.TRIVIAL
    )


def test_classify_weight_heavy_delete_none_check():
    diff = "-    if name is None:\n-        raise ValueError('n')\n"
    assert deletes_guard(diff) is True
    assert (
        classify_change_weight("utils.py", diff, ReviewDimension.LOGIC)
        == ChangeWeight.HEAVY
    )


def test_classify_weight_heavy_api_dimension():
    assert (
        classify_change_weight("api.py", "+pass\n", ReviewDimension.API)
        == ChangeWeight.HEAVY
    )


def test_classify_weight_normal_small_logic_change():
    diff = "-    return x\n+    return x + 1\n"
    assert (
        classify_change_weight("calc.py", diff, ReviewDimension.LOGIC)
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
    by_dim[ReviewDimension.LOGIC] = list(diffs.keys())

    clusters = _build_clusters(by_dim, diffs_by_path=diffs)
    trivial = [c for c in clusters if c.weight == ChangeWeight.TRIVIAL]
    heavy = [c for c in clusters if c.weight == ChangeWeight.HEAVY]

    assert len(trivial) == 1
    assert set(trivial[0].files) == {"c1.py", "c2.py", "c3.py", "c4.py", "c5.py"}
    assert len(heavy) == 2
    heavy_files = [c.files for c in heavy]
    assert ["none.py"] in heavy_files
    assert ["big.py"] in heavy_files


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
    api_files = plan.files_by_dimension.get(ReviewDimension.API, [])
    assert "api.py" in api_files
    assert len(plan.clusters) >= 1
    assert all(c.weight == ChangeWeight.HEAVY for c in plan.clusters if "api.py" in c.files)


def test_clusters_batch_normal_logic_files():
    paths = [f"f{i}.py" for i in range(10)]
    diffs = {p: "+x = 1\n" for p in paths}
    by_dim: dict[ReviewDimension, list[str]] = {d: [] for d in ReviewDimension}
    by_dim[ReviewDimension.LOGIC] = paths
    clusters = _build_clusters(by_dim, diffs_by_path=diffs)
    assert len(clusters) == 2
    assert all(c.dimension == ReviewDimension.LOGIC for c in clusters)
    assert all(c.weight == ChangeWeight.NORMAL for c in clusters)
    assert sum(len(c.files) for c in clusters) == 10


@patch("pr_review_agent.review_dimensions.diff_one_file", return_value="+x\n")
def test_build_plan_assigns_normal_weight_by_default(_mock_diff, tmp_path):
    repo = build_fixture_repo("obvious_none", tmp_path / "obvious_none")
    plan = build_pr_dimension_plan(repo, "main")
    logic_clusters = [c for c in plan.clusters if c.dimension == ReviewDimension.LOGIC]
    assert logic_clusters
    assert all(c.weight == ChangeWeight.NORMAL for c in logic_clusters)
