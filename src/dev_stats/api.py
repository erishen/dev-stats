"""GitHub REST API 客户端：仓库列表 + star/fork 公开数据 + clone/views 流量数据。"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

import requests

API_ROOT = "https://api.github.com"
TIMEOUT = 15


def detect_token() -> str | None:
    """按优先级获取 token：环境变量 GITHUB_TOKEN -> gh CLI。"""
    env_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if env_token and env_token.strip():
        return env_token.strip()

    if shutil.which("gh"):
        try:
            result = subprocess.run(
                ["gh", "auth", "token"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (subprocess.SubprocessError, OSError):
            pass
    return None


@dataclass
class RepoStats:
    """单个仓库的统计数据。traffic 字段为 None 表示不可得（非本人仓库 / 未认证）。"""

    name: str
    full_name: str
    private: bool
    fork: bool
    stars: int
    forks: int
    watchers: int | None  # 列表接口不返回，仅单仓库详情可得
    open_issues: int
    language: str | None
    updated_at: str
    # 仓库详情（--detail，/repos/{full_name}，每仓库 1 请求）
    size_kb: int | None = None
    license: str | None = None
    archived: bool | None = None
    topics: list[str] = field(default_factory=list)
    default_branch: str | None = None
    created_at: str | None = None
    homepage: str | None = None
    subscribers: int | None = None
    # 社区健康（--community，/community/profile，每仓库 1 请求）
    community_health: int | None = None  # 0-100
    community_files: dict = field(default_factory=dict, repr=False)
    # 活跃度（--activity，每仓库 1-2 请求）
    commits_4w: int | None = None  # 近 4 周提交数（基于 Link header 估算）
    latest_release: str | None = None  # 最新 release tag
    # 完整流量（--traffic-full，需管理员权限）
    top_paths: str | None = None  # 近 14 天热门路径摘要
    top_referrers: str | None = None  # 近 14 天流量来源摘要
    # 近 14 天 clone/浏览（仅本人仓库可见）
    clones_total: int | None = None  # 近 14 天总 clone 次数
    clones_uniques: int | None = None  # 近 14 天独立克隆者数
    views_total: int | None = None
    views_uniques: int | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def traffic_available(self) -> bool:
        return self.clones_total is not None

    @property
    def sort_key_stars(self) -> int:
        return self.stars

    @property
    def sort_key_clones(self) -> int:
        return self.clones_total if self.clones_total is not None else -1

    @property
    def sort_key_views(self) -> int:
        return self.views_total if self.views_total is not None else -1

    @property
    def sort_key_size(self) -> int:
        return self.size_kb if self.size_kb is not None else -1

    @property
    def sort_key_health(self) -> int:
        return self.community_health if self.community_health is not None else -1

    @property
    def sort_key_commits(self) -> int:
        return self.commits_4w if self.commits_4w is not None else -1


def repo_from_item(item: dict) -> RepoStats:
    """将 /repos 列表接口的 JSON 项转换为 RepoStats。"""
    return RepoStats(
        name=item["name"],
        full_name=item["full_name"],
        private=item["private"],
        fork=item["fork"],
        stars=item["stargazers_count"],
        forks=item["forks_count"],
        watchers=item.get("subscribers_count"),
        open_issues=item["open_issues_count"],
        language=item.get("language"),
        updated_at=(item.get("pushed_at") or item.get("updated_at") or "")[:10],
    )


def page_count_from_links(links: dict) -> int | None:
    """从响应 Link header 的 rel=last 解析总页数（per_page=1 时即总数）。"""
    last = links.get("last")
    if not last:
        return None
    query = parse_qs(urlparse(last["url"]).query)
    pages = query.get("page", [])
    try:
        return int(pages[0])
    except (ValueError, IndexError):
        return None


class GitHubClient:
    """轻量 GitHub API 封装，自动分页与错误降级。"""

    def __init__(self, token: str | None = ..., per_page: int = 100):
        self.session = requests.Session()
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "dev-stats-cli",
        }
        self.token = detect_token() if token is ... else token
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        self.session.headers.update(headers)
        self.per_page = per_page

    @property
    def authenticated(self) -> bool:
        return bool(self.token)

    def _get_resp(self, path: str, **params) -> requests.Response | None:
        """GET 请求返回原始响应；404/403（权限或限流）返回 None，其余错误抛出。"""
        url = f"{API_ROOT}{path}"
        resp = self.session.get(url, params=params or None, timeout=TIMEOUT)
        if resp.status_code in (403, 404):
            return None
        resp.raise_for_status()
        return resp

    def get(self, path: str, **params) -> dict | list | None:
        """GET 请求；404/403 返回 None，其余错误抛出。"""
        resp = self._get_resp(path, **params)
        if resp is None:
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    def whoami(self) -> str | None:
        try:
            data = self.get("/user")
        except (requests.RequestException, ValueError):
            return None
        return data["login"] if isinstance(data, dict) else None

    def list_repos(self, user: str) -> list[RepoStats]:
        """列出用户全部仓库（自动分页）。"""
        repos: list[RepoStats] = []
        page = 1
        while True:
            data = self.get(
                f"/users/{user}/repos",
                per_page=self.per_page,
                page=page,
                sort="pushed",
                type="all",
            )
            if not data:
                break
            repos.extend(repo_from_item(item) for item in data)
            if len(data) < self.per_page:
                break
            page += 1
        return repos

    def fetch_traffic(self, repo: RepoStats) -> None:
        """拉取近 14 天 clone / 浏览量（仅仓库管理员可见，失败则保持 None）。"""
        clones = self.get(f"/repos/{repo.full_name}/traffic/clones")
        if isinstance(clones, dict):
            repo.clones_total = clones.get("count")
            repo.clones_uniques = clones.get("uniques")
        views = self.get(f"/repos/{repo.full_name}/traffic/views")
        if isinstance(views, dict):
            repo.views_total = views.get("count")
            repo.views_uniques = views.get("uniques")

    def fetch_traffic_full(self, repo: RepoStats) -> None:
        """在 clone/views 基础上追加热门路径与流量来源（仅仓库管理员可见）。"""
        self.fetch_traffic(repo)
        paths = self.get(f"/repos/{repo.full_name}/traffic/popular/paths")
        if isinstance(paths, list):
            repo.top_paths = (
                "; ".join(f"{p.get('path')}×{p.get('count')}" for p in paths[:3] if isinstance(p, dict)) or None
            )
        referrers = self.get(f"/repos/{repo.full_name}/traffic/popular/referrers")
        if isinstance(referrers, list):
            repo.top_referrers = (
                "; ".join(f"{r.get('referrer')}×{r.get('count')}" for r in referrers[:3] if isinstance(r, dict)) or None
            )

    def fetch_user_info(self, user: str) -> dict | None:
        """拉取账号公开信息：followers / following / 仓库数 / 注册时间。"""
        data = self.get(f"/users/{user}")
        return data if isinstance(data, dict) else None

    def fetch_repo_detail(self, repo: RepoStats) -> None:
        """拉取仓库详情字段（/repos/{full_name}，公开，每仓库 1 请求）。"""
        data = self.get(f"/repos/{repo.full_name}")
        if not isinstance(data, dict):
            return
        repo.size_kb = data.get("size")
        license_meta = data.get("license") or {}
        repo.license = license_meta.get("spdx_id") or None
        repo.archived = bool(data.get("archived"))
        repo.topics = data.get("topics") or []
        repo.default_branch = data.get("default_branch")
        repo.created_at = (data.get("created_at") or "")[:10] or None
        repo.homepage = data.get("homepage") or None
        repo.subscribers = data.get("subscribers_count")
        repo.stars = data.get("stargazers_count", repo.stars)
        repo.updated_at = (data.get("pushed_at") or repo.updated_at)[:10]

    def fetch_community(self, repo: RepoStats) -> None:
        """拉取社区健康度（/community/profile，公开，fork 返回 404 保持 None）。"""
        data = self.get(f"/repos/{repo.full_name}/community/profile")
        if not isinstance(data, dict):
            return
        repo.community_health = data.get("health_percentage")
        files = data.get("files") or {}
        repo.community_files = {
            "readme": bool(files.get("readme")),
            "contributing": bool(files.get("contributing")),
            "license": bool(files.get("license")),
            "code_of_conduct": bool(files.get("code_of_conduct")),
        }

    def fetch_commit_count(self, repo: RepoStats, since: str) -> None:
        """按 since 统计提交数（per_page=1 + Link header 估算，公开，每仓库 1 请求）。"""
        resp = self._get_resp(f"/repos/{repo.full_name}/commits", since=since, per_page=1)
        if resp is None:
            return
        try:
            data = resp.json()
        except ValueError:
            return
        if not isinstance(data, list):
            return
        count = page_count_from_links(resp.links)
        if count is not None:
            repo.commits_4w = count

    def fetch_latest_release(self, repo: RepoStats) -> None:
        """拉取最新 release tag（无 release 时 404 保持 None）。"""
        data = self.get(f"/repos/{repo.full_name}/releases/latest")
        if isinstance(data, dict):
            repo.latest_release = data.get("tag_name") or None
