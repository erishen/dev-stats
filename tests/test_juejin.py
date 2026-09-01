"""掘金模块离线测试：字段解析、翻页、时间转换、排序、表格渲染（不触网）。"""

from __future__ import annotations

from datetime import date

from dev_stats.cli import render_juejin_table, sort_posts
from dev_stats.juejin import JuejinClient, JuejinPost


class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _item(aid, title, view, digg, comment, ctime):
    return {
        "article_info": {
            "article_id": aid,
            "title": title,
            "view_count": view,
            "digg_count": digg,
            "comment_count": comment,
            "ctime": ctime,
        }
    }


def _body(items, has_more=False):
    return {"err_no": 0, "data": items, "has_more": has_more}


class FakeSession:
    def __init__(self, pages):
        self._pages = pages
        self.calls = 0

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls += 1
        idx = min(self.calls, len(self._pages)) - 1
        return FakeResp(self._pages[idx])


def test_fetch_posts_parses_fields_and_paginates():
    sess = FakeSession(
        [
            _body([_item("1", "标题A", 100, 5, 2, "1600000000")], has_more=True),
            _body([_item("2", "标题B", 50, 1, 0, "1700000000")]),
        ]
    )
    posts = JuejinClient(session=sess).fetch_posts("123")
    assert sess.calls == 2
    first = next(iter(posts))
    assert first.title == "标题A"
    assert first.view_count == 100
    assert first.url == "https://juejin.cn/post/1"
    assert first.publish_time == "2020-09-13"


def test_fetch_posts_stops_on_no_more():
    sess = FakeSession([_body([_item("1", "单页", 1, 0, 0, "1600000000")])])
    posts = JuejinClient(session=sess).fetch_posts("123")
    assert sess.calls == 1
    assert len(posts) == 1


def test_sort_posts_by_views_desc_with_missing_lowest():
    posts = [
        JuejinPost("1", "a", "", 10, 1, 0, ""),
        JuejinPost("2", "b", "", 30, 5, 2, ""),
        JuejinPost("3", "c", "", None, 0, 0, ""),
    ]
    ordered = sort_posts(posts, "views")
    assert [p.title for p in ordered] == ["b", "a", "c"]


def test_render_juejin_table_columns_and_totals():
    posts = [JuejinPost("1", "标题", "2024-01-01", 10, 2, 1, "")]
    table = render_juejin_table(posts, "123")
    headers = [c.header for c in table.columns]
    assert "阅读" in headers and "点赞" in headers and "评论" in headers
    assert "日均" in headers
    assert "共 1 篇" in table.caption


def test_sort_posts_by_daily_views():
    posts = [
        JuejinPost("1", "老文", "2026-01-04", 1000, 1, 0, ""),
        JuejinPost("2", "新文", "2026-08-29", 135, 2, 2, ""),
        JuejinPost("3", "无时间", "", 100, 1, 0, ""),
    ]
    ordered = sort_posts(posts, "daily", today=date(2026, 9, 1))
    assert [p.title for p in ordered] == ["新文", "老文", "无时间"]
