"""命令行入口：采集仓库统计数据并以表格输出，支持 CSV 导出。"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import UTC, date, datetime, timedelta

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from .api import GitHubClient, RepoStats

try:
    from .juejin import JuejinClient, JuejinPost
except ImportError:
    JuejinClient = None
    JuejinPost = None
from .link import build_article_repos, load_repo_articles, repo_label

try:
    from .segmentfault import SegmentFaultClient
except ImportError:
    SegmentFaultClient = None

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
        "--traffic-since-days",
        type=int,
        default=730,
        help="仅对最近 N 天内有更新的仓库拉取 traffic 数据（默认 730 天=2 年，老仓库记 0）",
    )
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
        "--actions", action="store_true", help="拉取最新一次 GitHub Actions 运行状态（成功/失败/运行中，每仓库 1 请求）"
    )
    parser.add_argument(
        "--no-user-info", action="store_true", help="不显示账号信息行（followers/关注/仓库数/注册年份）"
    )
    parser.add_argument(
        "--csv", metavar="PATH", default=None, help="导出 CSV 到指定路径（默认不含 traffic 非公开数据）"
    )
    parser.add_argument(
        "--include-traffic", action="store_true", help="CSV 导出包含 clone/views 流量数据（非公开，仅本地分享时使用）"
    )
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
    show_actions: bool = False,
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
    if show_actions:
        table.add_column("CI")
        table.add_column("CI 失败详情", overflow="fold", max_width=48)
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
        if show_actions:
            row += [_fmt_actions(r)]
            detail = r.actions_errors or r.actions_failed_jobs or UNAVAILABLE
            row += [detail if r.actions_conclusion in ("failure", "timed_out", "startup_failure") else UNAVAILABLE]
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


def _fmt_actions(r: RepoStats) -> str:
    """渲染 CI 列：未采集/无 workflow 为 -；运行中青色；成功绿、失败红、取消黄。"""
    if r.actions_status is None and r.actions_conclusion is None:
        return UNAVAILABLE
    if r.actions_status in ("in_progress", "queued", "waiting", "pending"):
        return f"[cyan]● {r.actions_status}[/cyan]"
    conclusion = r.actions_conclusion or "unknown"
    style = {"success": "green", "failure": "red", "timed_out": "red", "startup_failure": "red"}.get(
        conclusion, "yellow"
    )
    mark = "✓" if conclusion == "success" else ("✗" if style == "red" else "○")
    return f"[{style}]{mark} {conclusion}[/]"


def _fmt(value: int | None) -> str:
    return UNAVAILABLE if value is None else f"{value:,}"


def _fmt_daily(value: float) -> str:
    return UNAVAILABLE if value <= 0 else f"{value:.1f}"


def _strip_emoji(title: str) -> str:
    """去掉标题中的 emoji / 符号类字符，避免终端渲染宽度与 wcwidth 计算不一致导致表格错位。"""
    import unicodedata

    return "".join(ch for ch in title if unicodedata.category(ch) not in ("So", "Sk") and ch != "\ufe0f").strip()


def _daily_views(view_count: int | None, publish_time: str, today: date | None = None) -> float:
    """日均阅读 = 阅读量 / 距发布天数（至少 1 天）；缺发布时间或阅读时返回 0。

    today 可注入固定日期，便于测试；缺省用真实今天。
    """
    if view_count is None or not publish_time:
        return 0.0
    today = today or date.today()
    try:
        d = datetime.strptime(publish_time, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return 0.0
    days = (today - d).days + 1
    if days < 1:
        days = 1
    return view_count / days


def export_csv(repos: list[RepoStats], path: str, include_traffic: bool = False) -> None:
    """导出仓库统计到 CSV。默认不含 clone/views 等非公开 traffic 数据；
    加 include_traffic=True 才包含（仅本地分享时使用，勿公开）。"""
    base_fields = [
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
        "actions_workflow",
        "actions_status",
        "actions_conclusion",
        "actions_at",
        "actions_failed_jobs",
        "actions_errors",
        "pushed_at",
        "language",
    ]
    traffic_fields = [
        "clones_total_14d",
        "cloners_14d",
        "views_total_14d",
        "viewers_14d",
        "top_paths",
        "top_referrers",
    ]
    fields = base_fields + (traffic_fields if include_traffic else [])
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in repos:
            row = {
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
                "actions_workflow": r.actions_workflow,
                "actions_status": r.actions_status,
                "actions_conclusion": r.actions_conclusion,
                "actions_at": r.actions_at,
                "actions_failed_jobs": r.actions_failed_jobs,
                "actions_errors": r.actions_errors,
                "pushed_at": r.updated_at,
                "language": r.language,
            }
            if include_traffic:
                row.update(
                    {
                        "clones_total_14d": r.clones_total,
                        "cloners_14d": r.clones_uniques,
                        "views_total_14d": r.views_total,
                        "viewers_14d": r.views_uniques,
                        "top_paths": r.top_paths,
                        "top_referrers": r.top_referrers,
                    }
                )
            writer.writerow({k: row.get(k, "") for k in fields})


def main(argv: list[str] | None = None) -> int:
    load_dotenv()  # 从 .env 读取 GITHUB_TOKEN / JUEJIN_USER_ID 等配置（若存在）
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "segmentfault":
        return run_segmentfault(argv[1:])
    if argv and argv[0] == "juejin":
        return run_juejin(argv[1:])
    if argv and argv[0] == "report":
        return run_report(argv[1:])
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

    # 流量相关排序（clones/views）需先采集再排序；公开字段排序先排序截断以减少采集量
    traffic_sort = args.sort in ("clones", "views")
    if not traffic_sort:
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
        cutoff = date.today() - timedelta(days=args.traffic_since_days)
        traffic_count = 0
        with console.status(f"采集 clone/views 流量数据（仅 {args.traffic_since_days} 天内有更新的仓库）…"):
            for r in repos:
                try:
                    r_date = date.fromisoformat(str(r.updated_at)[:10])
                except (ValueError, TypeError):
                    r_date = date.min
                if r_date >= cutoff:
                    fetch_traffic(r)
                    traffic_count += 1
                    time.sleep(0.05)
        console.print(
            f"[dim]traffic 采集：{traffic_count}/{len(repos)} 个仓库（{args.traffic_since_days} 天内有更新）[/dim]"
        )
        if not any(r.traffic_available for r in repos):
            show_traffic = False
            show_traffic_full = False
        if traffic_sort:
            repos = sort_repos(repos, args.sort)
            if args.limit > 0:
                repos = repos[: args.limit]

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
    if args.actions:
        with console.status("采集最新 GitHub Actions 运行状态…"):
            for r in repos:
                client.fetch_latest_action(r)
                time.sleep(0.05)

    console.print(
        render_table(
            repos,
            user,
            show_traffic,
            show_detail=args.detail,
            show_community=args.community,
            show_activity=args.activity,
            show_traffic_full=show_traffic_full,
            show_actions=args.actions,
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
        export_csv(repos, args.csv, include_traffic=args.include_traffic)
        console.print(f"[green]已导出 CSV：{args.csv}[/green]")
        if not args.include_traffic:
            console.print(
                "[dim]提示：CSV 默认不含 clone/views 流量数据，加 --include-traffic 可包含（非公开数据，慎分享）[/dim]"
            )
    if client.rate_remaining is not None and client.rate_remaining < 500:
        console.print(
            f"[yellow][WARNING] GitHub API rate remaining low: "
            f"{client.rate_remaining}/{client.rate_limit or '?'} (resets hourly)[/yellow]"
        )
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
        choices=("views", "diggs", "comments", "date", "daily"),
        default="views",
        help="排序字段（默认 views 降序；daily=日均阅读，反映传播效率）",
    )
    parser.add_argument("--limit", type=int, default=0, help="仅显示前 N 篇，0 表示全部")
    parser.add_argument("--csv", metavar="PATH", default=None, help="导出 CSV 到指定路径")
    parser.add_argument(
        "--articles-dir",
        default=None,
        help="wordpress-tools 掘金文章目录（扫描 frontmatter 的 wp_id 关联母文章与 GitHub 仓库），缺省读 .env 的 JUEJIN_ARTICLES_DIR",
    )
    return parser


JUEJIN_SORT_KEYS = ("views", "diggs", "comments", "date", "daily")


def sort_posts(posts: list[JuejinPost], key: str, today: date | None = None) -> list[JuejinPost]:
    if key == "date":
        return sorted(posts, key=lambda p: p.publish_time, reverse=True)
    if key == "daily":
        return sorted(posts, key=lambda p: _daily_views(p.view_count, p.publish_time, today), reverse=True)
    attr = {
        "views": lambda p: p.sort_key_views,
        "diggs": lambda p: p.sort_key_diggs,
        "comments": lambda p: p.sort_key_comments,
    }[key]
    return sorted(posts, key=attr, reverse=True)


def _norm_title(title: str) -> str:
    """归一化标题用于跨表匹配：去 emoji/控制符、空白与小写。"""
    import re as _re
    import unicodedata

    cleaned = "".join(ch for ch in title if not unicodedata.category(ch).startswith("C"))
    return _re.sub(r"\s+", "", cleaned).lower()


def _scan_juejin_wp(articles_dir: str) -> dict[str, str]:
    """扫描 wordpress-tools 掘金文章 frontmatter，返回 归一化标题 -> wp_id。"""
    import glob
    import re as _re

    mapping: dict[str, str] = {}
    for f in glob.glob(os.path.join(articles_dir, "*.md")):
        try:
            with open(f, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        tm = _re.search(r"^title:\s*[\"']?(?P<t>[^\"'\n]+)", text, _re.M)
        wm = _re.search(r"^wp_id:\s*(?P<w>\d+)", text, _re.M)
        if tm and wm:
            mapping[_norm_title(tm.group("t"))] = wm.group("w")
    return mapping


def _match_juejin_wp(posts: list[JuejinPost], mapping: dict[str, str]) -> None:
    """按标题匹配填充每篇掘金文章的 wp_id（原地修改）。"""
    for p in posts:
        p.wp_id = mapping.get(_norm_title(p.title), "")


def render_juejin_table(posts: list[JuejinPost], user_id: str, art_repos: dict | None = None) -> Table:
    table = Table(title=f"掘金账号 {user_id} 的文章数据", title_style="bold cyan")
    table.add_column("文章", overflow="ellipsis", no_wrap=True, max_width=40)
    table.add_column("发布时间", justify="right")
    table.add_column("关联", justify="left")
    table.add_column("阅读", justify="right")
    table.add_column("日均", justify="right")
    table.add_column("点赞", justify="right")
    table.add_column("评论", justify="right")
    art_repos = art_repos or {}
    for p in posts:
        table.add_row(
            _strip_emoji(p.title) or UNAVAILABLE,
            p.publish_time or UNAVAILABLE,
            repo_label(art_repos, p.wp_id) or UNAVAILABLE,
            _fmt(p.view_count),
            _fmt_daily(_daily_views(p.view_count, p.publish_time)),
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


def export_juejin_csv(posts: list[JuejinPost], path: str, art_repos: dict | None = None) -> None:
    fields = [
        "title",
        "post_id",
        "url",
        "publish_time",
        "view_count",
        "daily_views",
        "digg_count",
        "comment_count",
        "wp_id",
        "repos",
    ]
    art_repos = art_repos or {}
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
                    "daily_views": f"{_daily_views(p.view_count, p.publish_time):.2f}",
                    "digg_count": p.digg_count,
                    "comment_count": p.comment_count,
                    "wp_id": p.wp_id,
                    "repos": repo_label(art_repos, p.wp_id),
                }
            )


def run_juejin(argv: list[str]) -> int:
    if JuejinClient is None:
        console = Console()
        console.print("[yellow]掘金功能为本地模块，当前环境未找到 dev_stats.juejin。[/yellow]")
        return 0
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

    dir_path = args.articles_dir or os.environ.get("JUEJIN_ARTICLES_DIR")
    if dir_path:
        try:
            _match_juejin_wp(posts, _scan_juejin_wp(dir_path))
        except OSError as exc:
            console.print(f"[yellow]扫描掘金文章目录失败，跳过关联：{exc}[/yellow]")
    art_repos = build_article_repos(load_repo_articles())

    posts = sort_posts(posts, args.sort)
    if args.limit > 0:
        posts = posts[: args.limit]
    console.print(render_juejin_table(posts, user_id, art_repos))
    if args.csv:
        export_juejin_csv(posts, args.csv, art_repos)
        console.print(f"[green]已导出 CSV：{args.csv}[/green]")
    return 0


# ---------- 跨平台汇总（report）子命令 ----------


def build_report_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dev-stats report",
        description="汇总掘金 + 思否文章数据，按母文章(wp_id)合并为跨平台对比表",
    )
    parser.add_argument(
        "--sort",
        choices=("total", "daily", "juejin", "segmentfault", "date"),
        default="daily",
        help="排序字段（默认 daily=总日均阅读降序；total=合计阅读）",
    )
    parser.add_argument("--limit", type=int, default=0, help="仅显示前 N 篇，0 表示全部")
    parser.add_argument("--csv", metavar="PATH", default=None, help="导出 CSV 到指定路径")
    return parser


def _merge_platform_posts(jj_posts, sf_posts):
    """按 wp_id 合并掘金与思否数据为对比行（无 wp_id 的按平台+id 单独成行）。"""
    rows = {}
    for p in jj_posts:
        key = p.wp_id or ("jj:" + p.post_id)
        e = rows.setdefault(
            key,
            {"wp_id": p.wp_id, "title": "", "pub": "", "jj_views": 0, "jj_daily": 0.0, "sf_views": 0, "sf_daily": 0.0},
        )
        e["title"] = e["title"] or p.title
        e["jj_views"] = p.view_count or 0
        e["jj_daily"] = _daily_views(p.view_count, p.publish_time)
        if not e["pub"] or (p.publish_time and p.publish_time < e["pub"]):
            e["pub"] = p.publish_time
    for p in sf_posts:
        key = p.wp_id or ("sf:" + p.post_id)
        e = rows.setdefault(
            key,
            {"wp_id": p.wp_id, "title": "", "pub": "", "jj_views": 0, "jj_daily": 0.0, "sf_views": 0, "sf_daily": 0.0},
        )
        e["title"] = e["title"] or p.title
        e["sf_views"] = p.view_count or 0
        e["sf_daily"] = _daily_views(p.view_count, p.publish_time)
        if not e["pub"] or (p.publish_time and p.publish_time < e["pub"]):
            e["pub"] = p.publish_time
    out = []
    for e in rows.values():
        e["total_views"] = e["jj_views"] + e["sf_views"]
        e["total_daily"] = _daily_views(e["total_views"], e["pub"])
        out.append(e)
    return out


def sort_report_rows(rows, key):
    if key == "date":
        return sorted(rows, key=lambda e: e["pub"] or "", reverse=True)
    attr = {
        "total": lambda e: e["total_views"],
        "daily": lambda e: e["total_daily"],
        "juejin": lambda e: e["jj_views"],
        "segmentfault": lambda e: e["sf_views"],
    }[key]
    return sorted(rows, key=attr, reverse=True)


def render_report_table(rows, art_repos=None) -> Table:
    table = Table(title="内容平台整体汇总（掘金 + 思否）", title_style="bold cyan")
    table.add_column("文章", overflow="ellipsis", no_wrap=True, max_width=40)
    table.add_column("发布时间", justify="right")
    table.add_column("关联", justify="left")
    table.add_column("掘金", justify="right")
    table.add_column("掘金日均", justify="right")
    table.add_column("思否", justify="right")
    table.add_column("思否日均", justify="right")
    table.add_column("合计", justify="right")
    table.add_column("总日均", justify="right")
    art_repos = art_repos or {}
    for e in rows:
        table.add_row(
            _strip_emoji(e["title"]) or UNAVAILABLE,
            e["pub"] or UNAVAILABLE,
            repo_label(art_repos, e["wp_id"]) or UNAVAILABLE,
            _fmt(e["jj_views"]),
            _fmt_daily(e["jj_daily"]),
            _fmt(e["sf_views"]),
            _fmt_daily(e["sf_daily"]),
            _fmt(e["total_views"]),
            _fmt_daily(e["total_daily"]),
        )
    total_views = sum(e["total_views"] for e in rows)
    table.caption = "  |  ".join([f"共 {len(rows)} 篇", f"总阅读 {total_views:,}"])
    return table


def export_report_csv(rows, path: str, art_repos: dict | None = None) -> None:
    fields = [
        "title",
        "wp_id",
        "publish_time",
        "jj_views",
        "jj_daily",
        "sf_views",
        "sf_daily",
        "total_views",
        "total_daily",
        "repos",
    ]
    art_repos = art_repos or {}
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for e in rows:
            writer.writerow(
                {
                    "title": e["title"],
                    "wp_id": e["wp_id"],
                    "publish_time": e["pub"],
                    "jj_views": e["jj_views"],
                    "jj_daily": f"{e['jj_daily']:.2f}",
                    "sf_views": e["sf_views"],
                    "sf_daily": f"{e['sf_daily']:.2f}",
                    "total_views": e["total_views"],
                    "total_daily": f"{e['total_daily']:.2f}",
                    "repos": repo_label(art_repos, e["wp_id"]),
                }
            )


def run_report(argv: list[str]) -> int:
    args = build_report_parser().parse_args(argv)
    console = Console()
    user_id = os.environ.get("JUEJIN_USER_ID")
    sf_dir = os.environ.get("SEGMENTFAULT_ARTICLES_DIR")
    if not user_id:
        console.print("[red]未配置 JUEJIN_USER_ID（.env）。[/red]")
        return 2
    if not sf_dir:
        console.print("[red]未配置 SEGMENTFAULT_ARTICLES_DIR（.env）。[/red]")
        return 2

    try:
        jj_posts = JuejinClient().fetch_posts(user_id)
    except Exception as exc:
        console.print(f"[red]拉取掘金失败：{exc}[/red]")
        return 1
    jj_dir = os.environ.get("JUEJIN_ARTICLES_DIR")
    if jj_dir:
        _match_juejin_wp(jj_posts, _scan_juejin_wp(jj_dir))

    try:
        wp_map = _scan_sf_wp(sf_dir)
        sf_posts = SegmentFaultClient().fetch_posts(sorted(set(wp_map)))
    except Exception as exc:
        console.print(f"[red]拉取思否失败：{exc}[/red]")
        return 1
    for p in sf_posts:
        p.wp_id = wp_map.get(p.post_id, "")

    rows = _merge_platform_posts(jj_posts, sf_posts)
    rows = sort_report_rows(rows, args.sort)
    if args.limit > 0:
        rows = rows[: args.limit]
    art_repos = build_article_repos(load_repo_articles())
    console.print(render_report_table(rows, art_repos))
    if args.csv:
        export_report_csv(rows, args.csv, art_repos)
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
        choices=("views", "diggs", "comments", "date", "daily"),
        default="views",
        help="排序字段（默认 views 降序；daily=日均阅读，反映传播效率）",
    )
    parser.add_argument("--limit", type=int, default=0, help="仅显示前 N 篇，0 表示全部")
    parser.add_argument("--csv", metavar="PATH", default=None, help="导出 CSV 到指定路径")
    return parser


def _scan_sf_wp(articles_dir: str) -> dict[str, str]:
    """扫描思否文章 frontmatter，返回 sf_id -> wp_id（wp_id 缺失则为空串）。"""
    import glob
    import re as _re

    mapping: dict[str, str] = {}
    for f in glob.glob(os.path.join(articles_dir, "*.md")):
        try:
            with open(f, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        sm = _re.search(r"^sf_id:\s*(?P<id>\d+)", text, _re.M)
        wm = _re.search(r"^wp_id:\s*(?P<w>\d+)", text, _re.M)
        if sm:
            mapping[sm.group("id")] = wm.group("w") if wm else ""
    return mapping


def _scan_sf_ids(articles_dir: str) -> list[str]:
    """从 wordpress-tools 文章 frontmatter 提取思否文章 ID（sf_id）。"""
    return sorted(set(_scan_sf_wp(articles_dir)))


def sort_sf_posts(posts, key: str, today: date | None = None):
    if key == "date":
        return sorted(posts, key=lambda p: p.publish_time, reverse=True)
    if key == "daily":
        return sorted(posts, key=lambda p: _daily_views(p.view_count, p.publish_time, today), reverse=True)
    attr = {
        "views": lambda p: p.sort_key_views,
        "diggs": lambda p: p.sort_key_diggs,
        "comments": lambda p: p.sort_key_comments,
    }[key]
    return sorted(posts, key=attr, reverse=True)


def render_segmentfault_table(posts, source: str, art_repos: dict | None = None) -> Table:
    table = Table(title=f"思否账号 {source} 的文章数据", title_style="bold cyan")
    table.add_column("文章", overflow="ellipsis", no_wrap=True, max_width=40)
    table.add_column("发布时间", justify="right")
    table.add_column("关联", justify="left")
    table.add_column("阅读", justify="right")
    table.add_column("日均", justify="right")
    table.add_column("访客", justify="right")
    table.add_column("点赞", justify="right")
    table.add_column("收藏", justify="right")
    table.add_column("评论", justify="right")
    art_repos = art_repos or {}
    for p in posts:
        table.add_row(
            _strip_emoji(p.title) or UNAVAILABLE,
            p.publish_time or UNAVAILABLE,
            repo_label(art_repos, p.wp_id) or UNAVAILABLE,
            _fmt(p.view_count),
            _fmt_daily(_daily_views(p.view_count, p.publish_time)),
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


def export_segmentfault_csv(posts, path: str, art_repos: dict | None = None) -> None:
    fields = [
        "title",
        "post_id",
        "url",
        "publish_time",
        "view_count",
        "daily_views",
        "unique_view_count",
        "digg_count",
        "bookmark_count",
        "comment_count",
        "wp_id",
        "repos",
    ]
    art_repos = art_repos or {}
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
                    "daily_views": f"{_daily_views(p.view_count, p.publish_time):.2f}",
                    "unique_view_count": p.unique_view_count,
                    "digg_count": p.digg_count,
                    "bookmark_count": p.bookmark_count,
                    "comment_count": p.comment_count,
                    "wp_id": p.wp_id,
                    "repos": repo_label(art_repos, p.wp_id),
                }
            )


def run_segmentfault(argv: list[str]) -> int:
    if SegmentFaultClient is None:
        console = Console()
        console.print("[yellow]思否功能为本地模块，当前环境未找到 dev_stats.segmentfault。[/yellow]")
        return 0
    if not os.environ.get("SEGMENTFAULT_ENABLED"):
        console = Console()
        console.print(
            "[yellow]思否抓取功能默认禁用（本地个人使用，公开部署请勿启用）。[/yellow]\n"
            "在 .env 中设置 SEGMENTFAULT_ENABLED=true 后启用。"
        )
        return 0
    args = build_segmentfault_parser().parse_args(argv)
    console = Console()
    ids = []
    wp_map: dict[str, str] = {}
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
        wp_map = _scan_sf_wp(dir_path)
        ids = sorted(set(wp_map))
    if not ids:
        console.print("[yellow]没有可查的思否文章 ID。[/yellow]")
        return 1

    client = SegmentFaultClient()
    try:
        posts = client.fetch_posts(ids)
    except Exception as exc:
        console.print(f"[red]拉取思否文章失败：{exc}[/red]")
        return 1
    for p in posts:
        p.wp_id = wp_map.get(p.post_id, "")
    art_repos = build_article_repos(load_repo_articles())

    posts = sort_sf_posts(posts, args.sort)
    if args.limit > 0:
        posts = posts[: args.limit]
    console.print(render_segmentfault_table(posts, str(len(ids)), art_repos))
    if args.csv:
        export_segmentfault_csv(posts, args.csv, art_repos)
        console.print(f"[green]已导出 CSV：{args.csv}[/green]")
    return 0
