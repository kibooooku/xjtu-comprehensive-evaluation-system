"""Shared, conservative competition-level vocabulary; no file-specific rules.

Geometry callers may join located lines before using these rules. The text-only
caller must not override a completed geometry check or resolve mixed levels.
"""
from __future__ import annotations

import re
import unicodedata


_PROVINCE_NAMES = (
    "河北", "山西", "辽宁", "吉林", "黑龙江", "江苏", "浙江", "安徽", "福建", "江西",
    "山东", "河南", "湖北", "湖南", "广东", "海南", "四川", "贵州", "云南", "陕西",
    "甘肃", "青海", "台湾",
)
_REGION_PATTERNS = (
    *(re.escape(name) + r"(?:省)?" for name in _PROVINCE_NAMES),
    *(re.escape(name) + r"(?:市)?" for name in ("北京", "天津", "上海", "重庆")),
    r"内蒙古(?:自治区)?", r"广西(?:壮族自治区|自治区)?", r"西藏(?:自治区)?",
    r"宁夏(?:回族自治区|自治区)?", r"新疆(?:维吾尔自治区|自治区)?",
    r"香港(?:特别行政区)?", r"澳门(?:特别行政区)?",
)
# Exposed for location-aware line wrapping: a paragraph ending in a recognized
# province-level region can be followed by a nearby, aligned “赛区” line.
PROVINCIAL_QUALIFIER = "(?:" + "|".join(_REGION_PATTERNS) + ")"


def scope_levels(text):
    """Return every explicit level, so conflicts cannot silently pick a score.

    “全国” in an event name does not establish the student's round. Require a
    final, an explicit level, or a level attached to the award instead.
    """
    value = re.sub(r"[\s\u200b\ufeff]+", "", unicodedata.normalize("NFKC", str(text or "")))
    patterns = {
        "NATIONAL": r"全国(?:总)?决赛|国赛|国家级|国家[一二三特]等(?:级)?奖|全国[一二三特]等(?:级)?奖",
        "PROVINCIAL": r"省级|省赛|省[一二三特]等(?:级)?奖|" + PROVINCIAL_QUALIFIER + r"赛区",
        "INTERNATIONAL": r"高水平国际|国际(?:级|赛|赛事|比赛|竞赛|(?:总)?决赛|一等|二等|三等|特等)|世界(?:赛|大赛|比赛)",
        "SCHOOL": r"校级|校赛|学校[一二三特]等(?:级)?奖",
        "LOCAL_OR_INDUSTRY": r"地方行政部门级|行业级|企业级|学会级|协会级|市级竞赛|县级竞赛",
    }
    levels = {level for level, pattern in patterns.items() if re.search(pattern, value)}
    # OCR can wrap an explicit national final across lines or return those
    # lines out of order. A national brand without 总决赛 still adds no level.
    if "全国" in value and "总决赛" in value:
        levels.add("NATIONAL")
    return levels
