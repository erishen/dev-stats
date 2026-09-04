"""离线单元测试：排序、CSV 导出、表格渲染、token 探测优先级（不触网）。"""

from __future__ import annotations

import csv

import pytest
from dev_stats.api import GitHubClient, RepoStats, page_count_from_links, repo_from_item
from dev_stats.cli import export_csv, render_table, sort_repos


class FakeResp:
    """模拟 requests.Response：只暴露测试用到的 json() 与 links。"""

    def __init__(self, payload, links=None):
        self._payload = payload
        self.links = links or {}

    def json(self):
        return self._payload


def make_repo(
    name: str,
    *,
    stars=0,
    forks=0,
    clones=None,
    cloners=None,
    fork=False,
    private=False,
    updated="2024-01-01",
    lang="Python",
) -> RepoStats:
    return RepoStats(
        name=name,
        full_name=f"erishen/{name}",
        private=private,
        fork=fork,
        stars=stars,
        forks=forks,
        watchers=None,
        open_issues=0,
        language=lang,
        updated_at=updated,
        clones_total=clones,
        clones_uniques=cloners,
    )


@pytest.fixture
def sample_repos() -> list[RepoStats]:
    return [
        make_repo("alpha", stars=10, forks=1, clones=100, cloners=50),
        make_repo("bravo", stars=30, forks=2, clones=5, cloners=3),
        make_repo("charlie", stars=5, forks=9, clones=None, cloners=None),
        make_repo("delta", stars=5, forks=0, clones=200, cloners=120, fork=True),
    ]


def test_sort_by_stars_desc(sample_repos):
    ordered = sort_repos(sample_repos, "stars")
    assert [r.name for r in ordered][:2] == ["bravo", "alpha"]


def test_sort_by_clones_treats_missing_as_lowest(sample_repos):
    ordered = sort_repos(sample_repos, "clones")
    assert ordered[0].name == "delta"  # 200
    assert ordered[-1].name == "charlie"  # None -> -1


def test_sort_by_name_case_insensitive(sample_repos):
    ordered = sort_repos(sample_repos, "name")
    assert [r.name for r in ordered] == ["alpha", "bravo", "charlie", "delta"]


def test_sort_by_updated_desc(sample_repos):
    ordered = sort_repos(sample_repos, "updated")
    assert ordered[0].updated_at >= ordered[-1].updated_at


def test_export_csv_roundtrip(tmp_path, sample_repos):
    path = tmp_path / "out.csv"
    export_csv(sample_repos, str(path))
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 4
    assert rows[0]["name"] == "alpha"
    # 默认不含 traffic 非公开字段（脱敏）
    assert "clones_total_14d" not in rows[0]
    assert "views_total_14d" not in rows[0]


def test_export_csv_include_traffic(tmp_path, sample_repos):
    path = tmp_path / "out.csv"
    export_csv(sample_repos, str(path), include_traffic=True)
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 4
    assert rows[0]["name"] == "alpha"
    assert rows[0]["clones_total_14d"] == "100"
    # 缺失流量写入空值（None -> 空字符串）
    charlie = next(r for r in rows if r["name"] == "charlie")
    assert charlie["clones_total_14d"] in ("", "None")


def test_render_table_has_traffic_columns_when_available(sample_repos):
    table = render_table(sample_repos, "erishen", show_traffic=True)
    headers = [c.header for c in table.columns]
    assert "Clones" in headers and "Cloners" in headers
    # 4 行数据
    assert table.row_count == 4


def test_render_table_without_traffic(sample_repos):
    table = render_table(sample_repos, "cli", show_traffic=False)
    headers = [c.header for c in table.columns]
    assert "Clones" not in headers


def test_detect_token_prefers_env(monkeypatch):
    from dev_stats import api

    monkeypatch.setenv("GITHUB_TOKEN", "env-secret-token")
    assert api.detect_token() == "env-secret-token"


def test_detect_token_none_when_nothing(monkeypatch):
    from dev_stats import api

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(api.shutil, "which", lambda _x: None)
    assert api.detect_token() is None


def test_repo_from_item_null_pushed_at_falls_back():
    """回归：GitHub 列表接口 pushed_at 可能为 null（曾导致 torvalds 查询崩溃）。"""
    item = {
        "name": "r",
        "full_name": "u/r",
        "private": False,
        "fork": False,
        "stargazers_count": 1,
        "forks_count": 0,
        "open_issues_count": 0,
        "language": None,
        "pushed_at": None,
        "updated_at": "2020-05-06T07:08:09Z",
    }
    repo = repo_from_item(item)
    assert repo.updated_at == "2020-05-06"
    assert repo.language is None


def test_repo_from_item_missing_watchers_is_none():
    """回归：列表接口不返回 subscribers_count，watchers 应为 None 而非 KeyError。"""
    item = {
        "name": "r",
        "full_name": "u/r",
        "private": False,
        "fork": False,
        "stargazers_count": 0,
        "forks_count": 0,
        "open_issues_count": 0,
        "pushed_at": "2024-01-01T00:00:00Z",
    }
    repo = repo_from_item(item)
    assert repo.watchers is None
    assert repo.updated_at == "2024-01-01"


# ---- 新增指标层：Link 分页解析 / 详情 / 社区 / release / 排序 / 渲染 / CSV ----


def test_page_count_from_links_parses_last_page():
    links = {"last": {"url": "https://api.github.com/repos/u/r/commits?since=x&per_page=1&page=42"}}
    assert page_count_from_links(links) == 42


def test_page_count_from_links_missing_returns_none():
    assert page_count_from_links({}) is None
    assert page_count_from_links({"next": {"url": "https://api.github.com/x?page=2"}}) is None


def test_sort_by_size_health_commits(sample_repos):
    from dev_stats.cli import sort_repos

    repos = sample_repos[:]
    for r, size, health, commits in [
        (repos[0], 2048, 90, 30),
        (repos[1], 512, 50, 100),
        (repos[2], 5, 10, None),
        (repos[3], 64, 20, 5),
    ]:
        r.size_kb, r.community_health, r.commits_4w = size, health, commits
    assert sort_repos(repos, "size")[0].name == "alpha"  # 2048
    assert sort_repos(repos, "health")[0].name == "alpha"
    assert sort_repos(repos, "commits")[0].name == "bravo"  # 100
    assert sort_repos(repos, "commits")[-1].name == "charlie"  # None -> -1


def test_fetch_repo_detail_populates_fields(monkeypatch):
    client = GitHubClient(token=None)
    repo = make_repo("alpha")
    monkeypatch.setattr(
        client,
        "get",
        lambda path, **kw: {
            "full_name": "erishen/alpha",
            "size": 4096,
            "license": {"spdx_id": "MIT"},
            "archived": False,
            "topics": ["ai", "python"],
            "default_branch": "main",
            "created_at": "2020-01-02T03:04:05Z",
            "homepage": "https://example.com",
            "subscribers_count": 3,
            "stargazers_count": 99,
            "pushed_at": "2024-05-06T07:08:09Z",
        },
    )
    client.fetch_repo_detail(repo)
    assert repo.size_kb == 4096
    assert repo.license == "MIT"
    assert repo.topics == ["ai", "python"]
    assert repo.default_branch == "main"
    assert repo.created_at == "2020-01-02"
    assert repo.subscribers == 3
    assert repo.stars == 99
    assert repo.updated_at == "2024-05-06"


def test_fetch_repo_detail_404_keeps_fields(monkeypatch):
    client = GitHubClient(token=None)
    repo = make_repo("alpha")
    monkeypatch.setattr(client, "get", lambda path, **kw: None)
    client.fetch_repo_detail(repo)
    assert repo.size_kb is None and repo.license is None


def test_fetch_community_populates_health(monkeypatch):
    client = GitHubClient(token=None)
    repo = make_repo("alpha")
    monkeypatch.setattr(
        client,
        "get",
        lambda path, **kw: {
            "health_percentage": 85,
            "files": {"readme": {"url": "x"}, "contributing": None, "license": {"key": "mit"}, "code_of_conduct": None},
        },
    )
    client.fetch_community(repo)
    assert repo.community_health == 85
    assert repo.community_files["readme"] is True
    assert repo.community_files["contributing"] is False


def test_fetch_latest_release(monkeypatch):
    client = GitHubClient(token=None)
    repo = make_repo("alpha")
    monkeypatch.setattr(client, "get", lambda path, **kw: {"tag_name": "v1.2.0"})
    client.fetch_latest_release(repo)
    assert repo.latest_release == "v1.2.0"

    repo2 = make_repo("bravo")
    monkeypatch.setattr(client, "get", lambda path, **kw: None)
    client.fetch_latest_release(repo2)
    assert repo2.latest_release is None


def test_fetch_commit_count_from_link_header(monkeypatch):
    client = GitHubClient(token=None)
    repo = make_repo("alpha")
    links = {"last": {"url": "https://api.github.com/repos/erishen/alpha/commits?since=x&per_page=1&page=57"}}
    monkeypatch.setattr(client, "_get_resp", lambda path, **kw: FakeResp([], links=links))
    client.fetch_commit_count(repo, "2026-08-01T00:00:00Z")
    assert repo.commits_4w == 57


def test_render_table_detail_community_activity_columns(sample_repos):
    table = render_table(
        sample_repos,
        "erishen",
        show_traffic=True,
        show_detail=True,
        show_community=True,
        show_activity=True,
    )
    headers = [c.header for c in table.columns]
    for expected in ("Size", "License", "Topics", "健康分", "4w提交", "Release", "Clones"):
        assert expected in headers


def test_export_csv_has_new_columns(tmp_path, sample_repos):
    path = tmp_path / "out.csv"
    sample_repos[0].size_kb = 2048
    sample_repos[0].license = "MIT"
    sample_repos[0].topics = ["ai"]
    sample_repos[0].community_health = 90
    sample_repos[0].commits_4w = 12
    export_csv(sample_repos, str(path))
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    first = rows[0]
    assert first["size_kb"] == "2048"
    assert first["license"] == "MIT"
    assert first["topics"] == "ai"
    assert first["community_health"] == "90"
    assert first["commits_4w"] == "12"


# ---- --actions 指标层：最新 workflow run 状态 ----


def test_fetch_latest_action_populates_fields(monkeypatch):
    client = GitHubClient(token=None)
    repo = make_repo("alpha")
    monkeypatch.setattr(
        client,
        "get",
        lambda path, **kw: {
            "total_count": 3,
            "workflow_runs": [
                {
                    "name": "CI",
                    "status": "completed",
                    "conclusion": "success",
                    "run_started_at": "2026-09-03T10:00:00Z",
                    "created_at": "2026-09-03T10:00:01Z",
                }
            ],
        },
    )
    client.fetch_latest_action(repo)
    assert repo.actions_status == "completed"
    assert repo.actions_conclusion == "success"
    assert repo.actions_workflow == "CI"
    assert repo.actions_at == "2026-09-03"  # run_started_at 优先于 created_at


def test_fetch_latest_action_failure_and_running(monkeypatch):
    client = GitHubClient(token=None)

    repo_fail = make_repo("bravo")
    monkeypatch.setattr(
        client,
        "get",
        lambda path, **kw: {"workflow_runs": [{"name": "test", "status": "completed", "conclusion": "failure"}]},
    )
    client.fetch_latest_action(repo_fail)
    assert repo_fail.actions_conclusion == "failure"

    repo_run = make_repo("charlie")
    monkeypatch.setattr(
        client,
        "get",
        lambda path, **kw: {"workflow_runs": [{"name": "CI", "status": "in_progress", "conclusion": None}]},
    )
    client.fetch_latest_action(repo_run)
    assert repo_run.actions_status == "in_progress"
    assert repo_run.actions_conclusion is None


def test_fetch_latest_action_success_skips_failure_detail(monkeypatch):
    """回归：成功 run 不应额外请求 jobs/annotations（省 API 配额）。"""
    client = GitHubClient(token=None)
    repo = make_repo("alpha")
    calls: list[str] = []

    def fake_get(path, **kw):
        calls.append(path)
        return {"workflow_runs": [{"name": "CI", "status": "completed", "conclusion": "success", "id": 1}]}

    monkeypatch.setattr(client, "get", fake_get)
    client.fetch_latest_action(repo)
    assert repo.actions_conclusion == "success"
    assert all("/jobs" not in p and "annotations" not in p for p in calls)


def test_fetch_latest_action_failure_detail(monkeypatch):
    """失败 run 追加采集：失败 job:step 摘要 + 注解报错（截断、过滤非 failure 级别）。"""
    client = GitHubClient(token=None)
    repo = make_repo("nsgm")

    def fake_get(path, **kw):
        if path.endswith("/actions/runs"):
            return {"workflow_runs": [{"name": "CI", "status": "completed", "conclusion": "failure", "id": 42}]}
        if "/actions/runs/42/jobs" in path:
            return {
                "jobs": [
                    {
                        "name": "test",
                        "conclusion": "failure",
                        "steps": [
                            {"name": "Setup", "conclusion": "success"},
                            {"name": "Run tests", "conclusion": "failure"},
                        ],
                        "check_run_url": "https://api.github.com/repos/erishen/nsgm/check-runs/999",
                    },
                    {"name": "lint", "conclusion": "success"},
                ]
            }
        if "/check-runs/999/annotations" in path:
            return [
                {"annotation_level": "failure", "message": "AssertionError: expected 200 got 500 " + "x" * 100},
                {"annotation_level": "warning", "message": "should be ignored"},
            ]
        return None

    monkeypatch.setattr(client, "get", fake_get)
    client.fetch_latest_action(repo)
    assert repo.actions_failed_jobs == "test: Run tests"
    assert repo.actions_errors.startswith("AssertionError")
    assert len(repo.actions_errors) <= 80  # 单条截断
    assert "ignored" not in repo.actions_errors  # 非 failure 级别注解被过滤


def test_fetch_latest_action_404_or_empty_keeps_none(monkeypatch):
    client = GitHubClient(token=None)
    repo = make_repo("alpha")
    monkeypatch.setattr(client, "get", lambda path, **kw: None)  # 无 workflow -> 404
    client.fetch_latest_action(repo)
    assert repo.actions_status is None and repo.actions_conclusion is None

    repo2 = make_repo("bravo")
    monkeypatch.setattr(client, "get", lambda path, **kw: {"workflow_runs": []})
    client.fetch_latest_action(repo2)
    assert repo2.actions_status is None and repo2.actions_conclusion is None


def test_render_table_actions_column(sample_repos):
    sample_repos[0].actions_status = "completed"
    sample_repos[0].actions_conclusion = "success"
    sample_repos[1].actions_status = "in_progress"
    sample_repos[1].actions_conclusion = None
    table = render_table(sample_repos, "erishen", show_traffic=True, show_actions=True)
    headers = [c.header for c in table.columns]
    assert "CI" in headers


def test_export_csv_actions_columns(tmp_path, sample_repos):
    path = tmp_path / "out.csv"
    sample_repos[0].actions_workflow = "CI"
    sample_repos[0].actions_status = "completed"
    sample_repos[0].actions_conclusion = "failure"
    sample_repos[0].actions_at = "2026-09-03"
    export_csv(sample_repos, str(path))
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    first = rows[0]
    assert first["actions_workflow"] == "CI"
    assert first["actions_conclusion"] == "failure"
    assert first["actions_at"] == "2026-09-03"
