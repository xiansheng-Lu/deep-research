"""信源域名规则表与分类器（M2-6 T1）。

按 PRD §6 流程 5「统一抽取信源类型与可信分级」的要求，以确定性域名规则把
证据来源划分为：

- ``source_type``：official_doc / news / community / search（internal 由
  M5 私域检索来源直接赋值，本分类器永不产出）；
- ``source_level``：primary / secondary / tertiary。

纯函数、零 IO、零 token；规则为代码内常量（带中文注释），误判复盘依据写入
证据 ``metadata_.source_rule``（见 standardizer）。

匹配语义：

- **后缀规则**：``gov.cn`` 形态命中 host 恰好相等或以 ``.gov.cn`` 结尾
  （``stats.gov.cn``、``miit.gov.cn`` 均命中）；
- **精确 host**：``who.int`` 形态要求 host 完全相等（避免 ``evil-who.int``
  之类前缀仿冒）。

判定顺序：PRIMARY_OFFICIAL → PRIMARY_ACADEMIC → SECONDARY_NEWS →
TERTIARY_COMMUNITY → 新闻关键字弱兜底 → 默认 tertiary/search，首中即止。
"""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address

# ---------------------------------------------------------------------------
# 枚举（字符串值与前端 services/api/types.ts 枚举、Evidence 列约束一致）
# ---------------------------------------------------------------------------

SOURCE_TYPE_OFFICIAL_DOC = "official_doc"
SOURCE_TYPE_NEWS = "news"
SOURCE_TYPE_COMMUNITY = "community"
SOURCE_TYPE_SEARCH = "search"

SOURCE_LEVEL_PRIMARY = "primary"
SOURCE_LEVEL_SECONDARY = "secondary"
SOURCE_LEVEL_TERTIARY = "tertiary"

# ---------------------------------------------------------------------------
# 规则表（初版：以中国公域信源 + PRD/前端 fixture 样例为基线，迭代须带单测）
# ---------------------------------------------------------------------------

#: 一级：政府/官方/国际组织（official_doc + primary）。
#: 以「.」开头的为后缀规则，其余为精确 host。
PRIMARY_OFFICIAL: tuple[str, ...] = (
    # 中国政府与党政机关（各级 gov.cn 子站、部委、地方政府统一覆盖）
    ".gov.cn",
    # 海外政府
    ".gov",
    ".go.jp",
    ".go.kr",
    ".gouv.fr",
    # 军事
    ".mil",
    ".mil.cn",
    # 国际组织与官方机构（精确 host，避免通用后缀仿冒）
    "who.int",
    "un.org",
    "imf.org",
    "worldbank.org",
    "oecd.org",
    "wto.org",
)

#: 一级：学术机构与正式文献出版方（official_doc + primary，详设「学术机构→primary」）
PRIMARY_ACADEMIC: tuple[str, ...] = (
    ".edu.cn",
    ".edu",
    ".ac.cn",
    ".ac",
    ".ac.uk",
    ".ac.jp",
    # 预印本 / 文献标识 / 学术数据库
    "arxiv.org",
    "doi.org",
    "cnki.net",
    "wanfangdata.com.cn",
    # 学术出版商
    "nature.com",
    "science.org",
    "sciencedirect.com",
    "springer.com",
    "ieee.org",
    "acm.org",
)

#: 二级：通讯社 / 广电 / 主流报刊（news + secondary）
SECONDARY_NEWS: tuple[str, ...] = (
    # 中国通讯社/广电/主流媒体
    "xinhuanet.com",
    "news.cn",
    "people.com.cn",
    "cctv.com",
    "cntv.cn",
    "china.com.cn",
    "chinadaily.com.cn",
    "thepaper.cn",
    "caixin.com",
    "yicai.com",
    "stcn.com",
    "21jingji.com",
    # 国际通讯社与主流媒体
    "reuters.com",
    "apnews.com",
    "afp.com",
    "bloomberg.com",
    "bbc.com",
    "bbc.co.uk",
    "cnn.com",
    "nytimes.com",
    "wsj.com",
    "ft.com",
    "economist.com",
    "theguardian.com",
    "washingtonpost.com",
)

#: 三级：UGC / 问答 / 论坛 / 博客 / 开发者社区（community + tertiary）
TERTIARY_COMMUNITY: tuple[str, ...] = (
    "zhihu.com",
    "weibo.com",
    "xiaohongshu.com",
    "douban.com",
    "csdn.net",
    "jianshu.com",
    "juejin.cn",
    "cnblogs.com",
    "segmentfault.com",
    "stackoverflow.com",
    "serverfault.com",
    "reddit.com",
    "medium.com",
    "quora.com",
    # 代码托管的讨论区/issue/wiki（代码片段类社区内容）
    "github.com",
)

#: 新闻弱兜底关键字：四张规则表全未命中时，对 host 最后两级做整词比较。
#: 保留 M1 既有英文媒体识别能力，但收紧为「点分隔的完整标签」匹配，
#: 避免 fake-news.example.com 这类子串误判。
_NEWS_FALLBACK_LABELS: frozenset[str] = frozenset(
    {
        "news",
        "press",
        "times",
        "reuters",
        "xinhua",
        "bloomberg",
        "bbc",
        "cnn",
        "nytimes",
        "wsj",
        "guardian",
    }
)


@dataclass(frozen=True, slots=True)
class SourceClassification:
    """域名分类结果。

    Attributes:
        source_type: 五值信源类型（本分类器不产出 internal）。
        source_level: 三级来源层级。
        rule: 命中的规则标识（``表名:规则``）；未命中规则表走兜底/默认时
            为 ``NEWS_FALLBACK:<标签>`` 或 ``DEFAULT``，供 metadata 留痕。
    """

    source_type: str
    source_level: str
    rule: str


def _normalize_host(domain: str | None) -> str:
    """归一化 host：小写、去空白、去末尾点与 ``www.`` 前缀。"""
    host = (domain or "").strip().lower().rstrip(".")
    if host.startswith("www.") and len(host) > 4:
        host = host[4:]
    return host


def _is_ip(host: str) -> bool:
    """host 是否为字面 IPv4/IPv6（IP 一律不参与域名规则匹配）。"""
    try:
        ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


def _matches(host: str, pattern: str) -> bool:
    """后缀/精确匹配：``.x`` 为后缀，其余为精确 host。"""
    if pattern.startswith("."):
        suffix = pattern
        return host == suffix[1:] or host.endswith(suffix)
    return host == pattern


def _host_labels(host: str) -> frozenset[str]:
    """host 全部点分隔标签集合（整词比较用）。

    ``news.example.com`` 含整词标签 news → 命中弱兜底；
    ``fake-news.example.com`` 只有连写标签 fake-news → 不命中。
    """
    return frozenset(p for p in host.split(".") if p)


def classify_source(domain: str | None) -> SourceClassification:
    """按域名规则判定信源类型与来源等级。

    空域名/IP 地址/内网 host 一律归默认 (tertiary, search)，不抛异常。
    """
    host = _normalize_host(domain)
    if not host or _is_ip(host):
        return SourceClassification(SOURCE_TYPE_SEARCH, SOURCE_LEVEL_TERTIARY, "DEFAULT:empty_or_ip")

    # 四张规则表按优先级首中即止
    for table_name, source_type, level, table in (
        ("PRIMARY_OFFICIAL", SOURCE_TYPE_OFFICIAL_DOC, SOURCE_LEVEL_PRIMARY, PRIMARY_OFFICIAL),
        ("PRIMARY_ACADEMIC", SOURCE_TYPE_OFFICIAL_DOC, SOURCE_LEVEL_PRIMARY, PRIMARY_ACADEMIC),
        ("SECONDARY_NEWS", SOURCE_TYPE_NEWS, SOURCE_LEVEL_SECONDARY, SECONDARY_NEWS),
        ("TERTIARY_COMMUNITY", SOURCE_TYPE_COMMUNITY, SOURCE_LEVEL_TERTIARY, TERTIARY_COMMUNITY),
    ):
        for pattern in table:
            if _matches(host, pattern):
                return SourceClassification(source_type, level, f"{table_name}:{pattern}")

    # 新闻关键字弱兜底：点分隔标签整词命中（仅在显式规则表未命中时）
    labels = _host_labels(host)
    hit_label = next((label for label in _NEWS_FALLBACK_LABELS if label in labels), None)
    if hit_label is not None:
        return SourceClassification(
            SOURCE_TYPE_NEWS,
            SOURCE_LEVEL_SECONDARY,
            f"NEWS_FALLBACK:{hit_label}",
        )

    return SourceClassification(SOURCE_TYPE_SEARCH, SOURCE_LEVEL_TERTIARY, "DEFAULT")


__all__ = [
    "SourceClassification",
    "classify_source",
    "PRIMARY_OFFICIAL",
    "PRIMARY_ACADEMIC",
    "SECONDARY_NEWS",
    "TERTIARY_COMMUNITY",
]
