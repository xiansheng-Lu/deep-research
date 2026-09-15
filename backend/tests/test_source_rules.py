"""M2-6 T1 信源域名规则分类器单测（AC-1/AC-2）。"""

from __future__ import annotations

import pytest

from app.retrieval.source_rules import (
    SOURCE_LEVEL_PRIMARY,
    SOURCE_LEVEL_SECONDARY,
    SOURCE_LEVEL_TERTIARY,
    SOURCE_TYPE_COMMUNITY,
    SOURCE_TYPE_NEWS,
    SOURCE_TYPE_OFFICIAL_DOC,
    SOURCE_TYPE_SEARCH,
    classify_source,
)


class TestPrimaryOfficial:
    @pytest.mark.parametrize(
        "domain",
        [
            "stats.gov.cn",
            "miit.gov.cn",
            "www.gov.cn",
            "beijing.gov.cn",
            "whitehouse.gov",
            "who.int",
            "un.org",
            "imf.org",
        ],
    )
    def test_government_and_ingo_are_primary_official(self, domain: str) -> None:
        result = classify_source(domain)
        assert result.source_level == SOURCE_LEVEL_PRIMARY
        assert result.source_type == SOURCE_TYPE_OFFICIAL_DOC
        assert result.rule.startswith(("PRIMARY_OFFICIAL",))

    def test_www_and_case_insensitive(self) -> None:
        result = classify_source("WWW.STATS.GOV.CN")
        assert result.source_level == SOURCE_LEVEL_PRIMARY
        assert result.source_type == SOURCE_TYPE_OFFICIAL_DOC


class TestPrimaryAcademic:
    @pytest.mark.parametrize(
        "domain",
        [
            "tsinghua.edu.cn",
            "mit.edu",
            "siat.ac.cn",
            "arxiv.org",
            "doi.org",
            "nature.com",
            "science.org",
            "ieee.org",
        ],
    )
    def test_academic_is_primary_official(self, domain: str) -> None:
        result = classify_source(domain)
        assert result.source_level == SOURCE_LEVEL_PRIMARY
        assert result.source_type == SOURCE_TYPE_OFFICIAL_DOC
        assert result.rule.startswith("PRIMARY_ACADEMIC")


class TestSecondaryNews:
    @pytest.mark.parametrize(
        "domain",
        [
            "xinhuanet.com",
            "people.com.cn",
            "cctv.com",
            "thepaper.cn",
            "caixin.com",
            "reuters.com",
            "bloomberg.com",
            "bbc.com",
            "bbc.co.uk",
            "nytimes.com",
        ],
    )
    def test_mainstream_media_is_secondary_news(self, domain: str) -> None:
        result = classify_source(domain)
        assert result.source_level == SOURCE_LEVEL_SECONDARY
        assert result.source_type == SOURCE_TYPE_NEWS


class TestTertiaryCommunity:
    @pytest.mark.parametrize(
        "domain",
        [
            "zhihu.com",
            "weibo.com",
            "csdn.net",
            "juejin.cn",
            "stackoverflow.com",
            "reddit.com",
            "github.com",
        ],
    )
    def test_ugc_is_tertiary_community(self, domain: str) -> None:
        result = classify_source(domain)
        assert result.source_level == SOURCE_LEVEL_TERTIARY
        assert result.source_type == SOURCE_TYPE_COMMUNITY


class TestDefaultAndEdgeCases:
    def test_plain_company_site_is_tertiary_search(self) -> None:
        result = classify_source("example.com")
        assert result.source_level == SOURCE_LEVEL_TERTIARY
        assert result.source_type == SOURCE_TYPE_SEARCH
        assert result.rule == "DEFAULT"

    @pytest.mark.parametrize("empty", ["", None, "   ", "."])
    def test_empty_domain_defaults_safely(self, empty: str | None) -> None:
        result = classify_source(empty)
        assert result.source_level == SOURCE_LEVEL_TERTIARY
        assert result.source_type == SOURCE_TYPE_SEARCH

    @pytest.mark.parametrize("ip", ["192.168.1.1", "10.0.0.1", "[2001:db8::1]"])
    def test_ip_address_defaults_safely(self, ip: str) -> None:
        result = classify_source(ip)
        assert result.source_level == SOURCE_LEVEL_TERTIARY
        assert result.source_type == SOURCE_TYPE_SEARCH

    def test_news_label_fallback_matches_whole_label(self) -> None:
        # 独立点分隔标签 news → 弱兜底命中媒体
        result = classify_source("news.example.com")
        assert result.source_level == SOURCE_LEVEL_SECONDARY
        assert result.source_type == SOURCE_TYPE_NEWS
        assert result.rule.startswith("NEWS_FALLBACK")

    @pytest.mark.parametrize(
        "spoof",
        [
            "fake-news.example.com",  # 连写标签，不命中
            "mynews.example.org",  # 前缀拼接，不命中
            "newsletter.example.com",  # 非整词，不命中
        ],
    )
    def test_substring_spoof_not_news(self, spoof: str) -> None:
        # AC-2：收紧子串匹配，仿冒域不判媒体
        result = classify_source(spoof)
        assert result.source_type != SOURCE_TYPE_NEWS
        assert result.source_level == SOURCE_LEVEL_TERTIARY

    def test_priority_official_before_community(self) -> None:
        # 歧义锁定：github.com 仅在社区表；而规则优先级以官方/学术为先
        result = classify_source("github.com")
        assert result.source_type == SOURCE_TYPE_COMMUNITY
        # gov.cn 即便同时含新闻性质也以官方表首中
        official = classify_source("news.gov.cn")
        assert official.source_type == SOURCE_TYPE_OFFICIAL_DOC
        assert official.source_level == SOURCE_LEVEL_PRIMARY
