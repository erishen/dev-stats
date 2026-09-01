"""仓库 ↔ 母文章关联数据。

关联来源：各 GitHub 仓库 README 中引用的 erishen.cn 文章链接（permalink slug
与 wordpress-tools 母文章 frontmatter 的 slug/wp_id 匹配）。扫描结果写入
data/repo_articles.json：repo -> [{wp_id, slug, title, url}]。

本模块提供按 wp_id 反向查询关联仓库的能力，供 juejin / segmentfault 子命令
在展示平台数据时补充"对应母文章 + GitHub 仓库"列。
"""

from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_REPO_ARTICLES = PROJECT_ROOT / "data" / "repo_articles.json"


def load_repo_articles(path: str | Path | None = None) -> dict:
    """加载 repo_articles.json；文件不存在或损坏时返回空 dict。"""
    p = Path(path) if path else DEFAULT_REPO_ARTICLES
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def build_article_repos(repo_articles: dict) -> dict:
    """反向索引：wp_id -> {slug, title, repos: [仓库名...]}。"""
    art: dict = {}
    for repo, arts in repo_articles.items():
        for a in arts:
            wp = str(a.get("wp_id") or "")
            if not wp:
                continue
            entry = art.setdefault(wp, {"slug": a.get("slug", ""), "title": a.get("title", ""), "repos": []})
            if repo not in entry["repos"]:
                entry["repos"].append(repo)
    return art


def repo_label(art_repos: dict, wp_id: str | int | None) -> str:
    """wp_id -> 关联仓库名（逗号分隔）；无关联返回空串。"""
    if wp_id is None:
        return ""
    entry = art_repos.get(str(wp_id))
    if not entry or not entry["repos"]:
        return ""
    return ", ".join(entry["repos"])
