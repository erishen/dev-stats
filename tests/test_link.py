"""关联模块与跨平台标题匹配测试（不触网）。"""

from __future__ import annotations

from dev_stats.cli import _match_juejin_wp, _norm_title, _scan_juejin_wp, _scan_sf_wp
from dev_stats.juejin import JuejinPost
from dev_stats.link import build_article_repos, load_repo_articles, repo_label


def test_load_missing_file_returns_empty(tmp_path):
    assert load_repo_articles(str(tmp_path / "nope.json")) == {}


def test_build_article_repos_reverse_index():
    data = {
        "cicdkit": [{"wp_id": "721", "slug": "cicdkit", "title": "CICD", "url": "x"}],
        "erishen": [
            {"wp_id": "721", "slug": "cicdkit", "title": "CICD", "url": "x"},
            {"wp_id": "548", "slug": "spring_rbac", "title": "RBAC", "url": "y"},
        ],
    }
    art = build_article_repos(data)
    assert art["721"]["repos"] == ["cicdkit", "erishen"]
    assert art["548"]["repos"] == ["erishen"]
    assert repo_label(art, "721") == "cicdkit, erishen"
    assert repo_label(art, 721) == "cicdkit, erishen"
    assert repo_label(art, "999") == ""
    assert repo_label(art, None) == ""


def test_norm_title_normalizes_space_and_case():
    assert _norm_title("  AutoGen  PSE 架构 ") == "autogenpse架构"


def test_match_juejin_wp_by_title(tmp_path):
    (tmp_path / "a.md").write_text('---\ntitle: "🏗️ AutoGen PSE 架构解析"\nwp_id: 575\n---\n', encoding="utf-8")
    posts = [JuejinPost("1", "🏗️ AutoGen PSE 架构解析", "", 10, 1, 0, "")]
    mapping = _scan_juejin_wp(str(tmp_path))
    assert mapping == {"🏗️autogenpse架构解析": "575"}
    _match_juejin_wp(posts, mapping)
    assert posts[0].wp_id == "575"


def test_scan_sf_wp_from_frontmatter(tmp_path):
    (tmp_path / "a.md").write_text("---\ntitle: 甲\nsf_id: 1190000048181252\nwp_id: 575\n---\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("---\ntitle: 乙\nsf_id: 1190000048218426\n---\n", encoding="utf-8")
    m = _scan_sf_wp(str(tmp_path))
    assert m["1190000048181252"] == "575"
    assert m["1190000048218426"] == ""


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-q"])
