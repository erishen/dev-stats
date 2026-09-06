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
