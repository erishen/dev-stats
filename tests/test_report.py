"""跨平台 report 子命令离线测试：合并、排序、表格渲染（不触网）。"""

from __future__ import annotations

from dev_stats.cli import _merge_platform_posts, render_report_table, sort_report_rows
from dev_stats.juejin import JuejinPost
from dev_stats.segmentfault import SegmentFaultPost


def test_merge_platform_posts_by_wp_id():
    jj = [JuejinPost("1", "甲", "2026-08-20", 100, 1, 0, "", "575")]
    sf = [SegmentFaultPost("2", "甲", "2026-08-20", 50, 40, 0, 0, 0, "", "575")]
    rows = _merge_platform_posts(jj, sf)
    assert len(rows) == 1
    e = rows[0]
    assert e["wp_id"] == "575"
    assert e["jj_views"] == 100
    assert e["sf_views"] == 50
    assert e["total_views"] == 150


def test_merge_platform_posts_keeps_platform_only_rows():
    jj = [JuejinPost("1", "只在掘金", "2026-08-20", 80, 1, 0, "", "")]
    sf = [SegmentFaultPost("2", "只在思否", "2026-08-20", 30, 20, 0, 0, 0, "", "")]
    rows = _merge_platform_posts(jj, sf)
    assert len(rows) == 2
    by_title = {e["title"]: e for e in rows}
    assert by_title["只在掘金"]["jj_views"] == 80
    assert by_title["只在思否"]["sf_views"] == 30


def test_sort_report_rows():
    rows = [
        {"pub": "2026-08-20", "total_views": 100, "total_daily": 1.0, "jj_views": 100, "sf_views": 0},
        {"pub": "2026-08-21", "total_views": 200, "total_daily": 2.0, "jj_views": 0, "sf_views": 200},
    ]
    assert sort_report_rows(rows, "total")[0]["total_views"] == 200
    assert sort_report_rows(rows, "daily")[0]["total_daily"] == 2.0
    assert sort_report_rows(rows, "juejin")[0]["jj_views"] == 100
    assert sort_report_rows(rows, "segmentfault")[0]["sf_views"] == 200
    assert sort_report_rows(rows, "date")[0]["pub"] == "2026-08-21"


def test_render_report_table_columns():
    rows = [
        {
            "wp_id": "575",
            "title": "甲",
            "pub": "2026-08-20",
            "jj_views": 100,
            "jj_daily": 10.0,
            "sf_views": 50,
            "sf_daily": 5.0,
            "total_views": 150,
            "total_daily": 15.0,
        }
    ]
    table = render_report_table(rows)
    headers = [c.header for c in table.columns]
    for col in ("掘金", "掘金日均", "思否", "思否日均", "合计", "总日均"):
        assert col in headers
    assert "共 1 篇" in table.caption


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-q"])


# ---------- run_report 降级逻辑（思未启用/失败不影响整体报告） ----------


def test_run_report_degrades_when_sf_disabled(monkeypatch, capsys):
    """SEGMENTFAULT_ENABLED 未设置时：不要求 sf_dir、跳过思否，仅掘金渲染，exit 0。"""
    import dev_stats.cli as cli

    class FakeJJClient:
        def fetch_posts(self, user_id):
            return [JuejinPost("9", "掘金独有", "2026-08-20", 100, 10.0, 1, 0)]

    monkeypatch.setattr(cli, "JuejinClient", FakeJJClient)
    monkeypatch.setenv("JUEJIN_USER_ID", "123")
    monkeypatch.delenv("SEGMENTFAULT_ENABLED", raising=False)
    monkeypatch.delenv("SEGMENTFAULT_ARTICLES_DIR", raising=False)
    monkeypatch.delenv("JUEJIN_ARTICLES_DIR", raising=False)

    rc = cli.run_report([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "仅含掘金数据" in out
    assert "掘金独有" in out


def test_run_report_degrades_when_sf_fetch_fails(monkeypatch, capsys):
    """思否启用但抓取整体失败：降级为仅掘金，exit 0。"""
    import dev_stats.cli as cli

    class FakeJJClient:
        def fetch_posts(self, user_id):
            return [JuejinPost("9", "掘金独有", "2026-08-20", 100, 10.0, 1, 0)]

    class BrokenSFClient:
        def __init__(self, session=None):
            pass

        def fetch_posts(self, ids):
            raise RuntimeError("WAF 拦截")

    monkeypatch.setattr(cli, "JuejinClient", FakeJJClient)
    monkeypatch.setattr(cli, "SegmentFaultClient", BrokenSFClient)
    monkeypatch.setenv("JUEJIN_USER_ID", "123")
    monkeypatch.setenv("SEGMENTFAULT_ENABLED", "true")
    monkeypatch.setenv("SEGMENTFAULT_ARTICLES_DIR", "/tmp/sf-articles")
    monkeypatch.setenv("JUEJIN_ARTICLES_DIR", "")
    monkeypatch.setattr(cli, "_scan_sf_wp", lambda d: {"9": "wp1"})

    rc = cli.run_report([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "降级为仅掘金" in out
    assert "掘金独有" in out


# ---------- 文章链接（标题可跳转 + 管道输出补链接清单） ----------


def test_merge_carries_platform_urls():
    jj = [JuejinPost("1", "甲", "2026-08-20", 100, 1, 0, "https://juejin.cn/post/1", wp_id="wp1")]
    sf = [
        SegmentFaultPost(
            "9", "甲", "2026-08-21", 50, 40, 2, 1, 0, "https://segmentfault.com/a/9"
        )
    ]
    sf[0].wp_id = "wp1"
    rows = _merge_platform_posts(jj, sf)
    assert len(rows) == 1
    assert rows[0]["jj_url"] == "https://juejin.cn/post/1"
    assert rows[0]["sf_url"] == "https://segmentfault.com/a/9"


def test_link_cell_wraps_url_and_escapes_title():
    from dev_stats.cli import _link_cell

    assert _link_cell("标题", "https://x.cn/1") == "[link=https://x.cn/1]标题[/link]"
    # 无链接退化为纯文本；标题里形似 markup 的方括号被转义，不会破坏表格渲染
    assert _link_cell("[bold]标题[/]", "") == "\\[bold]标题\\[/]"


def test_print_article_links_only_when_not_terminal():
    from rich.console import Console

    from dev_stats.cli import print_article_links

    buf = Console(file=open("/dev/null", "w"))
    assert buf.is_terminal is False
    print_article_links(buf, [("甲", "https://x.cn/1")])  # 非终端：应打印（不报错即可）
    print_article_links(buf, [("无链接", "")])  # 全无链接：跳过
