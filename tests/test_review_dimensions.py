"""Review dimension classifier and clustering (no LLM)."""

from tests.golden.loader import build_fixture_repo

from pr_review_agent.review_dimensions import (
    ReviewDimension,
    build_pr_dimension_plan,
    classify_file,
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


def test_clusters_batch_logic_files():
    from pr_review_agent.review_dimensions import DimensionCluster, _build_clusters

    paths = [f"f{i}.py" for i in range(10)]
    by_dim = {ReviewDimension.LOGIC: paths}
    for d in ReviewDimension:
        if d not in by_dim:
            by_dim[d] = []
    clusters = _build_clusters(by_dim)
    assert len(clusters) == 2
    assert all(c.dimension == ReviewDimension.LOGIC for c in clusters)
    assert sum(len(c.files) for c in clusters) == 10
