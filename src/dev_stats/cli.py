"""命令行入口：采集仓库统计数据并以表格输出，支持 CSV 导出。"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import UTC, datetime, timedelta

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from .api import GitHubClient, RepoStats
from .juejin import JuejinClient, JuejinPost
from .segmentfault import SegmentFaultClient

SORT_KEYS = ("stars", "forks", "clones", "views", "updated", "name", "size", "health", "commits")
UNAVAILABLE = "-"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dev-stats",
        description="查询 GitHub 个人项目的 star / fork / clone 情况（clone 数据仅本人仓库可见）",
    )
    parser.add_argument("--user", "-u", default=None, help="GitHub 用户名，缺省为自己（需 gh 登录或 GITHUB_TOKEN）")
    parser.add_argument("--sort", choices=SORT_KEYS, default="stars", help="排序字段（默认 stars 降序）")
    parser.add_argument("--limit", type=int, default=0, help="仅显示前 N 个仓库，0 表示全部")
    parser.add_argument("--no-forks", action="store_true", help="排除 fork 出来的仓库")
    parser.add_argument("--include-private", action="store_true", help="包含私有仓库（仅查询自己且已认证时生效）")
    parser.add_argument("--no-traffic", action="store_true", help="跳过 clone/浏览流量采集（只查公开数据，速度更快）")
    parser.add_argument(
        "--traffic-full", action="store_true", help="流量采集扩展到热门路径与来源（需本人仓库 + 管理员权限）"
    )
    parser.add_argument(
        "--detail", action="store_true", help="拉取仓库详情：体积 / License / topics / 归档状态（每仓库 1 请求）"
    )
    parser.add_argument(
        "--community", action="store_true", help="拉取社区健康分 0-100（README/许可/行为准则等，每仓库 1 请求）"
    )
    parser.add_argument(
        "--activity", action="store_true", help="拉取活跃度：近 4 周提交数 + 最新 release（每仓库 1-2 请求）"
    )
    parser.add_argument(
        "--no-user-info", action="store_true", help="不显示账号信息行（followers/关注/仓库数/注册年份）"
    )
    parser.add_argument("--csv", metavar="PATH", default=None, help="导出 CSV 到指定路径")
    parser.add_argument("--token", default=None, help="手动指定 GitHub token（默认自动读取 gh CLI / 环境变量）")
    return parser


def sort_repos(repos: list[RepoStats], key: str) -> list[RepoStats]:
    if key == "name":
        return sorted(repos, key=lambda r: r.name.lower())
    if key == "updated":
        return sorted(repos, key=lambda r: r.updated_at, reverse=True)
    attr = {
        "stars": lambda r: r.sort_key_stars,
        "forks": lambda r: r.forks,
        "clones": lambda r: r.sort_key_clones,
        "views": lambda r: r.sort_key_views,
        "size": lambda r: r.sort_key_size,
        "health": lambda r: r.sort_key_health,
        "commits": lambda r: r.sort_key_commits,
    }[key]
    return sorted(repos, key=attr, reverse=True)


def render_table(
    repos: list[RepoStats],
    user: str,
    show_traffic: bool,
    show_detail: bool = False,
    show_community: bool = False,
    show_activity: bool = False,
    show_traffic_full: bool = False,
) -> Table:
    table = Table(title=f"{user} 的 GitHub 仓库统计（clone/views 为近 14 天数据）", title_style="bold cyan")
    table.add_column("仓库", overflow="fold")
    table.add_column("Star", justify="right")
    table.add_column("Fork", justify="right")
    table.add_column("Issue", justify="right")
    if show_detail:
        table.add_column("Size", justify="right")
        table.add_column("License")
        table.add_column("Topics", justify="right")
    if show_traffic:
        table.add_column("Clones", justify="right")
        table.add_column("Cloners", justify="right")
        table.add_column("Views", justify="right")
    if show_traffic_full:
        table.add_column("热门路径")
        table.add_column("来源")
    if show_community:
        table.add_column("健康分", justify="right")
    if show_activity:
        table.add_column("4w提交", justify="right")
        table.add_column("Release")
    table.add_column("最近推送")
    table.add_column("语言")

    for r in repos:
        name = r.name if not r.private else f"{r.name} (private)"
        if r.fork:
            name += " (fork)"
        row = [name, _fmt(r.stars), _fmt(r.forks), _fmt(r.open_issues)]
        if show_detail:
            row += [_fmt_size(r.size_kb), r.license or UNAVAILABLE, _fmt(len(r.topics))]
        if show_traffic:
            row += [_fmt(r.clones_total), _fmt(r.clones_uniques), _fmt(r.views_total)]
        if show_traffic_full:
            row += [r.top_paths or UNAVAILABLE, r.top_referrers or UNAVAILABLE]
        if show_community:
            row += [UNAVAILABLE if r.community_health is None else f"{r.community_health}"]
        if show_activity:
            row += [_fmt(r.commits_4w), r.latest_release or UNAVAILABLE]
        row += [r.updated_at or UNAVAILABLE, r.language or UNAVAILABLE]
        table.add_row(*row)

    total_stars = sum(r.stars for r in repos)
    total_clones = sum(r.clones_total for r in repos if r.clones_total is not None)
    summary = [f"共 {len(repos)} 个仓库", f"Star 合计 {total_stars}"]
    if show_traffic and any(r.clones_total is not None for r in repos):
        summary.append(f"14 天 Clone 合计 {total_clones}")
    table.caption = "  |  ".join(summary)
    return table


def _fmt_size(size_kb: int | None) -> str:
    if size_kb is None:
        return UNAVAILABLE
    if size_kb >= 1024:
        return f"{size_kb / 1024:.1f}M"
    return f"{size_kb}K"


def _fmt(value: int | None) -> str:
    return UNAVAILABLE if value is None else f"{value:,}"


def export_csv(repos: list[RepoStats], path: str) -> None:
    fields = [
        "name",
        "full_name",
        "private",
        "fork",
        "stars",
        "forks",
        "open_issues",
        "size_kb",
        "license",
        "archived",
        "topics",
        "default_branch",
        "created_at",
        "homepage",
        "subscribers",
        "community_health",
        "has_readme",
        "has_contributing",
        "has_license",
        "has_code_of_conduct",
        "commits_4w",
        "latest_release",
        "clones_total_14d",
        "cloners_14d",
        "views_total_14d",
        "viewers_14d",
        "top_paths",
        "top_referrers",
        "pushed_at",
        "language",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in repos:
            writer.writerow(
                {
                    "name": r.name,
                    "full_name": r.full_name,
                    "private": r.private,
                    "fork": r.fork,
                    "stars": r.stars,
                    "forks": r.forks,
                    "open_issues": r.open_issues,
                    "size_kb": r.size_kb,
                    "license": r.license,
                    "archived": r.archived,
                    "topics": ";".join(r.topics),
                    "default_branch": r.default_branch,
                    "created_at": r.created_at,
                    "homepage": r.homepage,
                    "subscribers": r.subscribers,
                    "community_health": r.community_health,
                    "has_readme": r.community_files.get("readme"),
                    "has_contributing": r.community_files.get("contributing"),
                    "has_license": r.community_files.get("license"),
                    "has_code_of_conduct": r.community_files.get("code_of_conduct"),
                    "commits_4w": r.commits_4w,
                    "latest_release": r.latest_release,
                    "clones_total_14d": r.clones_total,
                    "cloners_14d": r.clones_uniques,
                    "views_total_14d": r.views_total,
                    "viewers_14d": r.views_uniques,
                    "top_paths": r.top_paths,
                    "top_referrers": r.top_referrers,
                    "pushed_at": r.updated_at,
                    "language": r.language,
                }
            )


def main(argv: list[str] | None = None) -> int:
    load_dotenv()  # 从 .env 读取 GITHUB_TOKEN / JUEJIN_USER_ID 等配置（若存在）
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "segmentfault":
        return run_segmentfault(argv[1:])
    if argv and argv[0] == "juejin":
        return run_juejin(argv[1:])
    args = build_parser().parse_args(argv)
    console = Console()

    client = GitHubClient(token=args.token) if args.token else GitHubClient()
    me = client.whoami()
    user = args.user or me
    if not user:
        console.print(
            "[red]未指定 --user，且未检测到登录态。[/red] 请先 `gh auth login`、export GITHUB_TOKEN，或用 --user 指定用户名。"
        )
        return 2

    try:
        repos = client.list_repos(user)
    except Exception as exc:
        console.print(f"[red]拉取仓库列表失败：{exc}[/red]")
        return 1

    if not repos:
        console.print(f"[yellow]用户 {user} 没有可见仓库。[/yellow]")
        return 1

    if args.no_forks:
        repos = [r for r in repos if not r.fork]
    if not args.include_private:
        # 查询自己时列表会包含私有仓库，默认隐藏（--include-private 展示）
        repos = [r for r in repos if not r.private]

    # 先排序并截断，使 --limit 同时限制后续采集量（避免对全部仓库逐项请求）
    repos = sort_repos(repos, args.sort)
    if args.limit > 0:
        repos = repos[: args.limit]

    # 流量数据仅仓库管理员可见：认证用户 == 查询用户 时才采集
    show_traffic = False
    show_traffic_full = False
    if not args.no_traffic and client.authenticated and me and me.lower() == user.lower():
        show_traffic = True
        fetch_traffic = client.fetch_traffic_full if args.traffic_full else client.fetch_traffic
        show_traffic_full = args.traffic_full
        with console.status("采集 clone/views 流量数据…"):
            for r in repos:
                fetch_traffic(r)
        if not any(r.traffic_available for r in repos):
            show_traffic = False
            show_traffic_full = False

    # 可选指标层：详情 / 社区健康 / 活跃度（均为公开数据，按 flag 逐仓库请求）
    if args.detail:
        with console.status("采集仓库详情…"):
            for r in repos:
                client.fetch_repo_detail(r)
    if args.community:
        with console.status("采集社区健康度…"):
            for r in repos:
                client.fetch_community(r)
    if args.activity:
        since = (datetime.now(UTC) - timedelta(days=28)).isoformat()
        with console.status("采集近 4 周提交数与 release…"):
            for r in repos:
                client.fetch_commit_count(r, since)
                client.fetch_latest_release(r)

    console.print(
        render_table(
            repos,
            user,
            show_traffic,
            show_detail=args.detail,
            show_community=args.community,
            show_activity=args.activity,
            show_traffic_full=show_traffic_full,
        )
    )

    if not args.no_user_info:
        info = client.fetch_user_info(user)
        if info:
            created = (info.get("created_at") or "")[:4] or UNAVAILABLE
            console.print(
                "[dim]账号：@{} · 关注者 {} · 关注 {} · 公开仓库 {} · 注册于 {}\n[/dim]".format(
                    user,
                    _fmt(info.get("followers")),
                    _fmt(info.get("following")),
                    _fmt(info.get("public_repos")),
                    created,
                )
            )
    if not show_traffic:
        if not client.authenticated:
            console.print("[dim]提示：未检测到 token（gh / GITHUB_TOKEN），clone 流量数据不可用。[/dim]")
        elif user.lower() != (me or "").lower():
            console.print("[dim]提示：clone/views 数据仅对本人管理的仓库可见，他人仓库只显示公开指标。[/dim]")

    if args.csv:
        export_csv(repos, args.csv)
        console.print(f"[green]已导出 CSV：{args.csv}[/green]")
    return 0


# ---------- 掘金（juejin）子命令 ----------


def build_juejin_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dev-stats juejin",
        description="查询掘金账号文章的阅读 / 点赞 / 评论数据（公开数据，无需登录，需 user_id）",
    )
    parser.add_argument(
        "--user-id",
        default=None,
        help="掘金 user_id（个人主页 URL 里的数字串），缺省读 .env 的 JUEJIN_USER_ID",
    )
    parser.add_argument(
        "--sort",
        choices=("views", "diggs", "comments", "date"),
        default="views",
        help="排序字段（默认 views 降序）",
    )
    parser.add_argument("--limit", type=int, default=0, help="仅显示前 N 篇，0 表示全部")
    parser.add_argument("--csv", metavar="PATH", default=None, help="导出 CSV 到指定路径")
    return parser


JUEJIN_SORT_KEYS = ("views", "diggs", "comments", "date")


def sort_posts(posts: list[JuejinPost], key: str) -> list[JuejinPost]:
    if key == "date":
        return sorted(posts, key=lambda p: p.publish_time, reverse=True)
    attr = {
        "views": lambda p: p.sort_key_views,
        "diggs": lambda p: p.sort_key_diggs,
        "comments": lambda p: p.sort_key_comments,
    }[key]
    return sorted(posts, key=attr, reverse=True)


def render_juejin_table(posts: list[JuejinPost], user_id: str) -> Table:
    table = Table(title=f"掘金账号 {user_id} 的文章数据", title_style="bold cyan")
    table.add_column("文章", overflow="fold")
    table.add_column("发布时间", justify="right")
    table.add_column("阅读", justify="right")
    table.add_column("点赞", justify="right")
    table.add_column("评论", justify="right")
    for p in posts:
        table.add_row(
            p.title or UNAVAILABLE,
            p.publish_time or UNAVAILABLE,
            _fmt(p.view_count),
            _fmt(p.digg_count),
            _fmt(p.comment_count),
        )
    total_views = sum(p.view_count or 0 for p in posts)
    total_diggs = sum(p.digg_count or 0 for p in posts)
    total_comments = sum(p.comment_count or 0 for p in posts)
    table.caption = "  |  ".join(
        [f"共 {len(posts)} 篇", f"总阅读 {total_views:,}", f"总点赞 {total_diggs:,}", f"总评论 {total_comments:,}"]
    )
    return table


def export_juejin_csv(posts: list[JuejinPost], path: str) -> None:
    fields = ["title", "post_id", "url", "publish_time", "view_count", "digg_count", "comment_count"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for p in posts:
            writer.writerow(
                {
                    "title": p.title,
                    "post_id": p.post_id,
                    "url": p.url,
                    "publish_time": p.publish_time,
                    "view_count": p.view_count,
                    "digg_count": p.digg_count,
                    "comment_count": p.comment_count,
                }
            )


def run_juejin(argv: list[str]) -> int:
    args = build_juejin_parser().parse_args(argv)
    console = Console()
    user_id = args.user_id or os.environ.get("JUEJIN_USER_ID")
    if not user_id:
        console.print(
            "[red]未指定 --user-id，且 .env/JUEJIN_USER_ID 未配置。[/red] "
            "打开你的掘金主页（juejin.cn），URL 里的数字串即 user_id，可写入 .env 的 JUEJIN_USER_ID。"
        )
        return 2

    client = JuejinClient()
    try:
        posts = client.fetch_posts(user_id)
    except Exception as exc:
        console.print(f"[red]拉取掘金文章失败：{exc}[/red]")
        return 1
    if not posts:
        console.print(f"[yellow]该掘金账号没有可查的已发布文章（user_id={user_id}）。[/yellow]")
        return 1

    posts = sort_posts(posts, args.sort)
    if args.limit > 0:
        posts = posts[: args.limit]
    console.print(render_juejin_table(posts, user_id))
    if args.csv:
        export_juejin_csv(posts, args.csv)
        console.print(f"[green]已导出 CSV：{args.csv}[/green]")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# ---------- 思否（segmentfault）子命令 ----------


def build_segmentfault_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dev-stats segmentfault",
        description="查询思否账号文章的阅读 / 访客 / 点赞 / 收藏 / 评论（公开文章页，无需登录）",
    )
    parser.add_argument(
        "--ids",
        default=None,
        help="逗号分隔的思否文章 ID（https://segmentfault.com/a/<id> 里的数字），缺省从 --articles-dir 扫描",
    )
    parser.add_argument(
        "--articles-dir",
        default=None,
        help="wordpress-tools 文章目录（扫描 frontmatter 的 sf_id），缺省读 .env 的 SEGMENTFAULT_ARTICLES_DIR",
    )
    parser.add_argument(
        "--sort",
        choices=("views", "diggs", "comments", "date"),
        default="views",
        help="排序字段（默认 views 降序）",
    )
    parser.add_argument("--limit", type=int, default=0, help="仅显示前 N 篇，0 表示全部")
    parser.add_argument("--csv", metavar="PATH", default=None, help="导出 CSV 到指定路径")
    return parser


def _scan_sf_ids(articles_dir: str) -> list[str]:
    """从 wordpress-tools 文章 frontmatter 提取思否文章 ID（sf_id）。"""
    import glob
    import re as _re

    out = []
    for f in glob.glob(os.path.join(articles_dir, "*.md")):
        try:
            with open(f, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        m = _re.search(r"^sf_id:\s*(?P<id>\d+)", text, _re.M)
        if m:
            out.append(m.group("id"))
    return sorted(set(out))


def sort_sf_posts(posts, key: str):
    if key == "date":
        return sorted(posts, key=lambda p: p.publish_time, reverse=True)
    attr = {
        "views": lambda p: p.sort_key_views,
        "diggs": lambda p: p.sort_key_diggs,
        "comments": lambda p: p.sort_key_comments,
    }[key]
    return sorted(posts, key=attr, reverse=True)


def render_segmentfault_table(posts, source: str) -> Table:
    table = Table(title=f"思否账号 {source} 的文章数据", title_style="bold cyan")
    table.add_column("文章", overflow="fold")
    table.add_column("发布时间", justify="right")
    table.add_column("阅读", justify="right")
    table.add_column("访客", justify="right")
    table.add_column("点赞", justify="right")
    table.add_column("收藏", justify="right")
    table.add_column("评论", justify="right")
    for p in posts:
        table.add_row(
            p.title or UNAVAILABLE,
            p.publish_time or UNAVAILABLE,
            _fmt(p.view_count),
            _fmt(p.unique_view_count),
            _fmt(p.digg_count),
            _fmt(p.bookmark_count),
            _fmt(p.comment_count),
        )
    total_views = sum(p.view_count or 0 for p in posts)
    total_diggs = sum(p.digg_count or 0 for p in posts)
    total_comments = sum(p.comment_count or 0 for p in posts)
    table.caption = "  |  ".join(
        [f"共 {len(posts)} 篇", f"总阅读 {total_views:,}", f"总点赞 {total_diggs:,}", f"总评论 {total_comments:,}"]
    )
    return table


def export_segmentfault_csv(posts, path: str) -> None:
    fields = [
        "title",
        "post_id",
        "url",
        "publish_time",
        "view_count",
        "unique_view_count",
        "digg_count",
        "bookmark_count",
        "comment_count",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for p in posts:
            writer.writerow(
                {
                    "title": p.title,
                    "post_id": p.post_id,
                    "url": p.url,
                    "publish_time": p.publish_time,
                    "view_count": p.view_count,
                    "unique_view_count": p.unique_view_count,
                    "digg_count": p.digg_count,
                    "bookmark_count": p.bookmark_count,
                    "comment_count": p.comment_count,
                }
            )


def run_segmentfault(argv: list[str]) -> int:
    args = build_segmentfault_parser().parse_args(argv)
    console = Console()
    ids = []
    if args.ids:
        ids = [s.strip() for s in args.ids.split(",") if s.strip()]
    else:
        dir_path = args.articles_dir or os.environ.get("SEGMENTFAULT_ARTICLES_DIR")
        if not dir_path:
            console.print(
                "[red]未指定 --ids / --articles-dir，且 .env/SEGMENTFAULT_ARTICLES_DIR 未配置。[/red] "
                "思否文章 ID 在你的 wordpress-tools 文章 frontmatter 的 sf_id 字段里。"
            )
            return 2
        ids = _scan_sf_ids(dir_path)
    if not ids:
        console.print("[yellow]没有可查的思否文章 ID。[/yellow]")
        return 1

    client = SegmentFaultClient()
    try:
        posts = client.fetch_posts(ids)
    except Exception as exc:
        console.print(f"[red]拉取思否文章失败：{exc}[/red]")
        return 1

    posts = sort_sf_posts(posts, args.sort)
    if args.limit > 0:
        posts = posts[: args.limit]
    console.print(render_segmentfault_table(posts, str(len(ids))))
    if args.csv:
        export_segmentfault_csv(posts, args.csv)
        console.print(f"[green]已导出 CSV：{args.csv}[/green]")
    return 0
