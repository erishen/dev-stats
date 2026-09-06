"""思否模块离线测试：页面解析、逐篇抓取、排序、表格渲染、sf_id 扫描（不触网）。"""

from __future__ import annotations

import json
from datetime import date

import pytest

pytest.importorskip("dev_stats.segmentfault")

from dev_stats.cli import _scan_sf_ids, render_segmentfault_table, sort_sf_posts
from dev_stats.segmentfault import SegmentFaultClient, SegmentFaultPost


def _page(aid, title, views, uviews, votes, bookmarks, comments, created):
    art = {
        "id": aid,
        "title": title,
        "real_views": views,
        "real_unique_views": uviews,
        "votes": votes,
        "bookmarks": bookmarks,
        "comments": comments,
        "created": created,
    }
    payload = {"props": {"pageProps": {"initialState": {"articleDetail": {"artDetail": {aid: {"article": art}}}}}}}
    return (
        '<html><head><script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(payload)
        + "</script></head></html>"
    )


class FakeResp:
    def __init__(self, html):
        self.text = html

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self, pages):
        self._pages = pages
        self.calls = 0

    def get(self, url, timeout=None):
        self.calls += 1
        idx = min(self.calls, len(self._pages)) - 1
        return FakeResp(self._pages[idx])


def test_parse_page_parses_all_fields():
    html = _page("1190000048181252", "标题X", 320, 211, 5, 3, 2, "1787192690")
    post = SegmentFaultClient.parse_page(html, "1190000048181252")
    assert post.title == "标题X"
    assert post.view_count == 320
    assert post.unique_view_count == 211
    assert post.digg_count == 5
    assert post.bookmark_count == 3
    assert post.comment_count == 2
    assert post.url == "https://segmentfault.com/a/1190000048181252"
    assert post.publish_time == "2026-08-20"


def test_fetch_posts_sequential():
    sess = FakeSession(
        [
            _page("1", "篇一", 10, 8, 1, 0, 0, "1600000000"),
            _page("2", "篇二", 20, 15, 2, 1, 1, "1700000000"),
        ]
    )
    posts = SegmentFaultClient(session=sess).fetch_posts(["1", "2"])
    assert sess.calls == 2
    assert len(posts) == 2
    assert posts[0].title == "篇一"
    assert posts[1].view_count == 20


class Fake404Resp:
    def __init__(self):
        self.status_code = 404
        self.text = ""

    def raise_for_status(self):
        import requests

        raise requests.HTTPError("404 Client Error", response=self)


class FakeSessionWith404:
    """第 2 篇返回 404（已删除），其余正常。"""

    def __init__(self, pages):
        self._pages = pages
        self.calls = 0

    def get(self, url, timeout=None):
        self.calls += 1
        if self.calls == 2:
            return Fake404Resp()
        return FakeResp(self._pages.pop(0))


def test_fetch_posts_skips_deleted_404(capsys):
    sess = FakeSessionWith404(
        [
            _page("1", "篇一", 10, 8, 1, 0, 0, "1600000000"),
            _page("3", "篇三", 30, 25, 3, 1, 0, "1800000000"),
        ]
    )
    posts = SegmentFaultClient(session=sess).fetch_posts(["1", "2", "3"])
    # 404 被跳过，其余两篇正常返回
    assert len(posts) == 2
    assert [p.post_id for p in posts] == ["1", "3"]
    err = capsys.readouterr().err
    assert "已删除或不可见" in err
    assert "2" in err


def test_sort_sf_posts_by_views_with_missing_lowest():
    posts = [
        SegmentFaultPost("1", "a", "", 10, 5, 1, 0, 0, ""),
        SegmentFaultPost("2", "b", "", 30, 20, 5, 2, 1, ""),
        SegmentFaultPost("3", "c", "", None, None, 0, 0, 0, ""),
    ]
    ordered = sort_sf_posts(posts, "views")
    assert [p.title for p in ordered] == ["b", "a", "c"]


def test_render_segmentfault_table_columns_and_totals():
    posts = [
        SegmentFaultPost("1", "标题", "2024-01-01", 10, 8, 2, 1, 1, ""),
    ]
    table = render_segmentfault_table(posts, "erishen")
    headers = [c.header for c in table.columns]
    for col in ("文章", "阅读", "日均", "访客", "点赞", "收藏", "评论"):
        assert col in headers
    assert "共 1 篇" in table.caption


def test_sort_sf_posts_by_daily_views():
    posts = [
        SegmentFaultPost("1", "老文", "2026-01-04", 1000, 800, 1, 0, 0, ""),
        SegmentFaultPost("2", "新文", "2026-08-29", 135, 100, 2, 1, 1, ""),
        SegmentFaultPost("3", "无阅读", "2026-08-29", None, None, 0, 0, 0, ""),
    ]
    ordered = sort_sf_posts(posts, "daily", today=date(2026, 9, 1))
    assert [p.title for p in ordered] == ["新文", "老文", "无阅读"]


def test_scan_sf_ids_from_frontmatter(tmp_path):
    (tmp_path / "a.md").write_text("---\ntitle: 甲\nsf_id: 1190000048181252\n---\n正文", encoding="utf-8")
    (tmp_path / "b.md").write_text("---\ntitle: 乙\nsf_id: 1190000048218426\n---\n", encoding="utf-8")
    (tmp_path / "c.md").write_text("---\ntitle: 未发布\n---\n", encoding="utf-8")
    ids = _scan_sf_ids(str(tmp_path))
    assert ids == ["1190000048181252", "1190000048218426"]


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
