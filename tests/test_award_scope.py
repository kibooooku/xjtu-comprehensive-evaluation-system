"""Reusable event-level facts, including administrative names and ambiguity."""
import pytest

from app import outcome_evidence, policy_suggestions
from app.award_scope import scope_levels


@pytest.mark.parametrize("region", [
    "广东", "广东省", "北京市", "重庆", "内蒙古自治区", "广西", "广西壮族自治区",
    "新疆维吾尔自治区", "宁夏回族自治区", "西藏自治区", "香港特别行政区", "澳门", "澳门特别行政区",
])
def test_region_full_names_and_short_names_share_provincial_scope(region):
    text = f"全国大学生工程创新竞赛{region}赛区二等奖"
    assert scope_levels(text) == {"PROVINCIAL"}
    assert outcome_evidence._scope(text) == policy_suggestions._scope(text) == "PROVINCIAL"


@pytest.mark.parametrize("text,expected", [
    ("全国大学生数学建模竞赛省级一等奖", "PROVINCIAL"),
    ("全国大赛广东赛区一等奖", "PROVINCIAL"),
    ("全国大学生工程训练综合能力竞赛全国总决赛二等奖", "NATIONAL"),
    ("挑战杯国赛一等奖", "NATIONAL"),
    ("全国大学生创新大赛国家级一等奖", "NATIONAL"),
    ("高水平国际竞赛一等奖", "INTERNATIONAL"),
    ("国际总决赛二等奖", "INTERNATIONAL"),
    ("校级摄影竞赛一等奖", "SCHOOL"),
    ("学校二等奖", "SCHOOL"),
    ("行业级工程设计竞赛一等奖", "LOCAL_OR_INDUSTRY"),
    ("市级竞赛三等奖", "LOCAL_OR_INDUSTRY"),
])
def test_different_events_use_shared_explicit_level_vocabulary(text, expected):
    assert scope_levels(text) == {expected}
    assert outcome_evidence._scope(text) == policy_suggestions._scope(text) == expected


@pytest.mark.parametrize("text", [
    "全国大学生数学建模竞赛一等奖",
    "全国大赛一等奖",
    "广州市赛区一等奖",
    "一等奖证书",
])
def test_event_brand_city_or_grade_alone_does_not_invent_a_level(text):
    assert scope_levels(text) == set()
    assert outcome_evidence._scope(text) is None and policy_suggestions._scope(text) == ""


@pytest.mark.parametrize("text", [
    "全国总决赛一等奖，另有广东省赛区一等奖",
    "国家级竞赛一等奖，校级二等奖",
    "国际总决赛二等奖，省级一等奖",
])
def test_conflicting_explicit_levels_require_review_in_both_callers(text):
    assert len(scope_levels(text)) == 2
    assert outcome_evidence._scope(text) is None and policy_suggestions._scope(text) == ""


def test_spacing_unicode_and_wrapped_administrative_name_use_same_level():
    text = "　广西壮族自\n治区 赛 区 二等奖\ufeff"
    assert scope_levels(text) == {"PROVINCIAL"}
    assert outcome_evidence._scope(text) == policy_suggestions._scope(text) == "PROVINCIAL"
