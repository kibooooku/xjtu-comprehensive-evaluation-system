"""Evidence-backed suggestions from the supplied 2018 policy; never a manual-score validator.

Only automatic suggestions use these tables. Students can still edit their score;
publication/role eligibility and incomplete certificates remain human decisions.
"""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, ROUND_HALF_UP


POLICY_VERSION = "xjtu-2018-evidence-v1"
FACT_KEYS = ("award_level", "award_grade", "competition_kind", "research_type", "project_level",
             "publication_level", "author_rank", "assessment_result", "position_tier", "tenure_months",
             "practice_type", "volunteer_hours", "record_level", "recognition_level")
TARGET_CATEGORIES = {"RESEARCH", "ARTS", "POST", "SERVICE", "REWARD"}
RESEARCH_AWARDS = {"NATIONAL": ("10", "9", "8"), "INTERNATIONAL": ("10", "9", "8"),
                   "PROVINCIAL": ("8", "6", "4"), "SCHOOL": ("4", "3", "2", "1"),
                   "LOCAL_OR_INDUSTRY": ("4", "3", "2", "1")}
ART_AWARDS = {"NATIONAL": ("6", "5", "4", "3"), "INTERNATIONAL": ("6", "5", "4", "3"),
              "PROVINCIAL": ("4", "3.5", "3", "2"), "SCHOOL": ("2", "1.5", "1")}
POST_SCORES = {"TIER_1": ("4", "3.5", "3"), "TIER_2": ("3.5", "3", "2.5"),
               "TIER_3": ("2", "1.5", "1"), "TIER_4": ("1", ".75", ".5")}


def _text(value):
    return re.sub(r"[\s\u200b\ufeff]+", "", unicodedata.normalize("NFKC", str(value or "")))


def _single(values):
    values = set(values)
    return next(iter(values)) if len(values) == 1 else ""


def _number(value):
    if value.isdigit():
        return int(value)
    numbers = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12}
    return numbers.get(value)


def _scope(text):
    # 全国 in a competition brand is not evidence of winning its national round.
    # Explicit provincial qualifiers beat neither another explicit national claim
    # nor a contradictory attachment: those cases need human comparison.
    patterns = {
        "NATIONAL": r"全国(?:总)?决赛|国赛|国家级|国家[一二三特]等(?:级)?奖|全国[一二三特]等(?:级)?奖",
        "PROVINCIAL": r"省级|省赛|省[一二三特]等(?:级)?奖|(?:省|北京|上海|天津|重庆|河北|山西|辽宁|吉林|黑龙江|江苏|浙江|安徽|福建|江西|山东|河南|湖北|湖南|广东|海南|四川|贵州|云南|陕西|甘肃|青海|内蒙古|广西|西藏|宁夏|新疆|香港|澳门|台湾)(?:省|市|自治区)?赛区",
        "INTERNATIONAL": r"国际级|高水平国际|国际(?:总)?决赛|国际[一二三特]等(?:级)?奖",
        "SCHOOL": r"校级|校赛|学校[一二三特]等(?:级)?奖",
        "LOCAL_OR_INDUSTRY": r"地方行政部门级|行业级|企业级|学会级|协会级|市级竞赛|县级竞赛",
    }
    levels = {level for level, pattern in patterns.items() if re.search(pattern, text)}
    # OCR may return wrapped certificate lines out of reading order. A national
    # final's two explicit words still identify that round; a national brand
    # without 总决赛 does not. Any provincial conflict remains unresolved.
    if "全国" in text and "总决赛" in text:
        levels.add("NATIONAL")
    return _single(levels)


def _grade(text):
    return _single(grade for grade, pattern in {
        "SPECIAL": r"特等奖|特等级奖", "FIRST": r"一等奖|一等级奖",
        "SECOND": r"二等奖|二等级奖", "THIRD": r"三等奖|三等级奖", "EXCELLENT": r"优秀奖",
    }.items() if re.search(pattern, text))


def _assessment(text):
    results = []
    pattern = r"(?:考核|考评|评定|评估)(?:结果|等级|等第|成绩)?(?:为|是|达到|获评|评为)?[:：\"“”']*(不合格|优秀|良好|合格|优|良)(?![\u4e00-\u9fff])"
    for match in re.finditer(pattern, text):
        results.append({"优秀": "EXCELLENT", "优": "EXCELLENT", "良好": "GOOD", "良": "GOOD", "合格": "PASS", "不合格": "FAIL"}[match.group(1)])
    # Chinese prose often continues directly after the full words.
    for match in re.finditer(r"(?:考核|考评)(?:结果|等级|等第)?(?:为|是)?[:：]*(不合格|优秀|良好|合格)", text):
        results.append({"优秀": "EXCELLENT", "良好": "GOOD", "合格": "PASS", "不合格": "FAIL"}[match.group(1)])
    return _single(results)


def _position(text):
    tiers = []
    if re.search(r"(?:大学生党委副书记|大学生党委委员|院党总支副书记|院团工委副书记|(?:校|院)(?:级及以上)?学生组织正职|(?:校|院)学生会主席)(?!团|助理)", text):
        tiers.append("TIER_1")
    if re.search(r"(?:(?<!副)党总支委员|(?:校|院)(?:级及以上)?学生组织副职|(?:校|院)学生会副主席|优秀社团正职)(?!助理)", text):
        tiers.append("TIER_2")
    if re.search(r"(?:党支部委员|支委|(?:校|院)(?:级及以上)?学生组织部长|(?:校|院)学生会部长|甲级社团正职|(?<!副)团支书|(?<!副)班长)(?!助理)", text):
        tiers.append("TIER_3")
    if re.search(r"团学组织干事|学生会干事|班级一般干部|一般班干部|学生社团一般干部|宿舍长|舍长", text):
        tiers.append("TIER_4")
    return _single(tiers)


def _tenure(text):
    months = {_number(value) for value in re.findall(r"(?:任期|任职|工作时间)(?:已|为|满|达|累计)?([一二三四五六七八九十\d]+)个?月", text)}
    if re.search(r"(?:任期|任职|工作)(?:已|为)?满一年|任期一年|任职一年", text):
        months.add(12)
    months.discard(None)
    if re.search(r"不满一年|未满一年|不足一年", text):
        return "PARTIAL"
    return str(next(iter(months))) if len(months) == 1 else ""


def _author_rank(text):
    values = [_number(value) for pair in re.findall(r"第([一二三四五六七八九十\d]+)(?:位)?作者|作者(?:排名|排序|位次)[:：]?([一二三四五六七八九十\d]+)", text) for value in pair if value]
    return _single(str(value) for value in values if value is not None and value > 0)


def _personal_author_rank(text, student_name):
    name = _text(student_name)
    if not name:
        return ""
    escaped = re.escape(name)
    matches = []
    for pattern in (escaped + r"(?:同学|学生)?(?:为|是)?第([一二三四五六七八九十\d]+)(?:位)?作者",
                    r"第([一二三四五六七八九十\d]+)(?:位)?作者[:：]?" + escaped):
        matches.extend(_number(value) for value in re.findall(pattern, text))
    return _single(str(value) for value in matches if value is not None and value > 0)


def _research_type(text):
    values = []
    if re.search(r"发明专利|实用新型|软件著作权|专利证书", text):
        values.append("PATENT")
    if re.search(r"出版专著|专著出版|学术专著", text):
        values.append("MONOGRAPH")
    if re.search(r"(?:期刊|会议)论文|论文录用|发表论文", text):
        values.append("PAPER")
    if re.search(r"科研项目|项目立项|创新训练项目|创业训练项目|大创项目", text):
        values.append("PROJECT")
    if re.search(r"竞赛|大赛|锦标赛|挑战杯|蓝桥杯|(?:获|获得|荣获)[一二三特]等奖", text) and not values:
        values.append("COMPETITION")
    return _single(values)


def _amount(value):
    return str(Decimal(str(value)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))


def normalize_policy_suggestion(category_code, texts, revision, suggestion, linked_outcome=None, student_name=""):
    """Return a new suggestion and safe provenance, without mutating any stored draft."""
    if category_code not in TARGET_CATEGORIES:
        return dict(suggestion), {"status": "NOT_APPLICABLE", "version": POLICY_VERSION}, []
    text = "\n".join(_text(item.get("text")) for item in texts)
    compact = _text(text)
    payload = dict(suggestion.get("payload_json") or {}) if isinstance(suggestion.get("payload_json"), dict) else {}
    # The model may return prose where the form requires an enum; only grounded
    # facts replace these fields. Never retain a guessed assessment or scope.
    for key in FACT_KEYS:
        payload.pop(key, None)
        if key in (revision.get("payload_json") or {}):
            payload[key] = revision["payload_json"][key]
    facts, issues, score, rule = {}, [], None, ""
    requires_result = True
    linked = linked_outcome or {}
    linked_facts = linked.get("facts") if linked.get("status") == "MATCHED" and isinstance(linked.get("facts"), dict) else {}
    # Do not undo a completed layout check by concatenating raw OCR response
    # order again: it can join separate columns or hide a conflicting round.
    scope = linked_facts.get("award_level") or ("" if linked.get("is_roster") or linked.get("award_level_checked") else _scope(compact))
    # A document containing multiple people's results cannot provide the user's
    # grade merely because a grade appears somewhere in its text. The geometry
    # linkage service supplies the grade/assessment attached to this applicant.
    grade = str(linked_facts.get("award_grade") or "")
    assessment = {"QUALIFIED": "PASS", "UNQUALIFIED": "FAIL"}.get(linked_facts.get("assessment_result"), linked_facts.get("assessment_result", ""))
    if scope and category_code == "ARTS":
        facts["award_level"] = scope
    if grade and category_code in {"RESEARCH", "ARTS"}:
        facts["award_grade"] = grade
    if assessment:
        facts["assessment_result"] = assessment

    if category_code == "RESEARCH":
        kind = _research_type(compact)
        if kind:
            facts["research_type"] = kind
        if kind == "COMPETITION":
            if scope:
                facts["award_level"] = scope
            table = RESEARCH_AWARDS.get(scope)
            index = {"SPECIAL": 0, "FIRST": 0, "SECOND": 1, "THIRD": 2, "EXCELLENT": 3}.get(grade)
            if scope == "INTERNATIONAL" and "高水平国际" not in compact:
                issues.append("国际科研竞赛需核对是否属高水平国际项目")
            elif table and index is not None and index < len(table):
                score, rule = table[index], "PDF6_RESEARCH_COMPETITION"
            else:
                issues.append("需核对竞赛级别及获奖等第")
        elif kind in {"PAPER", "MONOGRAPH", "PATENT"}:
            requires_result = False
            rank = str(linked_facts.get("author_rank") or _personal_author_rank(compact, student_name))
            rank = rank if rank.isascii() and rank.isdigit() and int(rank) > 0 else ""
            if rank:
                facts["author_rank"] = rank
            affiliated = bool(re.search(r"第一(?:署名|作者)?单位[:：]?(?:西安交通大学|Xi['’]?anJiaotongUniversity)", compact, re.IGNORECASE))
            if not affiliated:
                issues.append("需核对第一署名单位是否为西安交通大学")
            if kind == "PAPER":
                publication = _single(value for value, pattern in {
                    "INTERNATIONAL_RECOGNIZED": r"学校认定的?国际期刊|校认定国际期刊",
                    "CORE_OR_INTL_CONFERENCE": r"国内核心期刊|国际会议论文",
                    "GENERAL_OR_DOMESTIC_CONFERENCE": r"国内一般期刊|国内会议论文",
                }.items() if re.search(pattern, compact))
                if publication:
                    facts["publication_level"] = publication
                n = int(rank) if rank else None
                if affiliated and n and publication:
                    table = {"INTERNATIONAL_RECOGNIZED": ("10", "8", "6", "2"), "CORE_OR_INTL_CONFERENCE": ("6", "5", "4", "1"), "GENERAL_OR_DOMESTIC_CONFERENCE": ("3", "2", "1")}[publication]
                    if n <= len(table) or publication != "GENERAL_OR_DOMESTIC_CONFERENCE":
                        score, rule = table[min(n, len(table))-1], "PDF6_7_PAPER"
                        if "入围优秀论文" in compact and re.search(r"国际.*会议|国内.*会议", compact):
                            score = str(min(Decimal(10), Decimal(score)+1))
                if score is None:
                    issues.append("需核对论文类别、学校认定及学生作者排序")
            elif kind == "MONOGRAPH":
                if affiliated and rank:
                    n = int(rank)
                    if n <= 8:
                        score, rule = str((10, 8, 6)[n-1] if n <= 3 else 9-n), "PDF7_MONOGRAPH"
                    else:
                        issues.append("专著第九及以后作者的递减边界需人工核定")
                elif affiliated and "参编" in compact:
                    score, rule = "1", "PDF7_MONOGRAPH_CONTRIBUTOR"
                else:
                    issues.append("需核对专著作者排序或参编身份")
            else:
                # The original PDF references i/ii without explicit anchors.
                # Do not invent a patent-rank table from nearby prose.
                issues.append("专利类型与作者顺序需按原办法由审查员核定")
        elif kind == "PROJECT":
            if scope:
                facts["project_level"] = scope
            facts.pop("award_level", None)
            issues.append("办法未列科研立项或结项等级的独立固定分值，需人工核定")
        else:
            issues.append("需核对科研成果类型或竞赛获奖依据")

    elif category_code == "ARTS":
        sport = bool(re.search(r"体育|运动会|田径|篮球|足球|排球|乒乓|羽毛球|跳绳|绕绳|游泳|夜跑|长跑|短跑|跳高|跳远|健身操", compact))
        art = bool(re.search(r"文艺|艺术|美术|书法|摄影|绘画|歌唱|舞蹈|器乐|音乐|设计赛", compact))
        kind = "SPORT" if sport and not art else "ART" if art and not sport else ""
        if kind:
            facts["competition_kind"] = kind
        if kind == "ART":
            table, index = ART_AWARDS.get(scope), {"FIRST": 0, "SECOND": 1, "THIRD": 2, "EXCELLENT": 3}.get(grade)
            if table and index is not None and index < len(table):
                score, rule = table[index], "PDF7_ART_COMPETITION"
        elif kind == "SPORT":
            place = int(grade.removeprefix("PLACE_")) if re.fullmatch(r"PLACE_[1-8]", grade) else None
            if place:
                facts["award_grade"] = f"PLACE_{place}"
            if linked_facts.get("record_level") == "PROVINCIAL_OR_ABOVE":
                score, rule, facts["record_level"] = "5", "PDF8_SPORT_RECORD", "PROVINCIAL_OR_ABOVE"
            elif linked_facts.get("record_level") == "SCHOOL":
                score, rule, facts["record_level"] = "3", "PDF8_SPORT_RECORD", "SCHOOL"
            elif scope in {"INTERNATIONAL", "NATIONAL", "PROVINCIAL", "SCHOOL"} and place:
                top = ART_AWARDS[scope][:3]
                if place <= 3:
                    score, rule = top[place-1], "PDF7_8_SPORT_COMPETITION"
                elif place <= 8:
                    score, rule = {"INTERNATIONAL": "3", "NATIONAL": "3", "PROVINCIAL": "2", "SCHOOL": ".5"}[scope], "PDF7_8_SPORT_COMPETITION"
        if score is None:
            issues.append("需核对文艺或体育类别、赛事级别及奖项或名次")

    elif category_code == "POST":
        tier = linked_facts.get("position_tier") or ("" if linked.get("is_roster") else _position(compact))
        tier = tier if tier in POST_SCORES else ""
        tenure = linked_facts.get("tenure_months") or _tenure(compact)
        tenure = str(tenure) if str(tenure).isdigit() else "PARTIAL" if tenure == "PARTIAL" else ""
        if tier:
            facts["position_tier"] = tier
        if tenure and tenure != "PARTIAL":
            facts["tenure_months"] = tenure
        if assessment == "FAIL":
            score, rule = "0", "PDF8_9_POST_FAIL"
        elif tier and tenure and tenure != "PARTIAL" and int(tenure) >= 12 and assessment in {"EXCELLENT", "GOOD", "PASS"}:
            score, rule = POST_SCORES[tier][("EXCELLENT", "GOOD", "PASS").index(assessment)], "PDF8_9_POST_FULL_YEAR"
        else:
            issues.append("任职需明确岗位层级、考评和满一年任期；不足一年由人工酌情核定")

    elif category_code == "SERVICE":
        volunteer = "志愿" in compact
        practice = "社会实践" in compact
        placement = bool(re.search(r"挂职|政府见习|国际组织见习|国际组织实习", compact))
        if sum((volunteer, practice, placement)) != 1:
            issues.append("需核对本小项的服务类型和相应证明")
        elif volunteer:
            requires_result = False
            facts["practice_type"] = "VOLUNTEER"
            values = {Decimal(value) for value in re.findall(r"(?:志愿服务|志愿|服务)?(?:累计|总计|认证)?(?:工时|时长|时间)(?:共|为|累计)?[:：]?(\d+(?:\.\d+)?)小时", compact)}
            if len(values) == 1:
                hours = next(iter(values));facts["volunteer_hours"] = str(hours)
                score, rule = str(min(Decimal(1), hours/32)), "PDF5_VOLUNTEER"
            else:
                issues.append("需核对本学年可计入的志愿服务工时")
        elif practice:
            facts["practice_type"] = "SCHOOL_SOCIAL_PRACTICE"
            school_practice = scope == "SCHOOL" or linked_facts.get("practice_type") == "SCHOOL_SOCIAL_PRACTICE" or (not linked.get("is_roster") and "校级" in compact)
            if school_practice and assessment in {"EXCELLENT", "GOOD", "PASS"} and re.search(r"团队|队员|成员", compact):
                score, rule = "1", "PDF5_SOCIAL_PRACTICE"
            elif assessment == "FAIL":
                score, rule = "0", "PDF5_SOCIAL_PRACTICE_FAIL"
            else:
                issues.append("社会实践需确认校级、团队成员身份及考核合格")
        elif placement:
            requires_result = False
            facts["practice_type"] = "PLACEMENT"
            if re.search(r"省级|部级|国际组织", compact):
                score, rule = "2", "PDF5_PLACEMENT"
            elif re.search(r"市级|县级", compact):
                score, rule = "1", "PDF5_PLACEMENT"
            else:
                issues.append("需核对挂职或政府见习的组织级别")
        extra_honor = linked_facts.get("advanced_individual") is True
        if not extra_honor and re.search(r"先进个人|受到表彰|获得.*表彰|荣获.*表彰", compact):
            issues.append("材料含额外荣誉或表彰，需核对是否属于本人及对应额外分")
        if extra_honor and score is not None:
            if volunteer:
                issues.append("志愿工时与额外荣誉需分别核定，当前年度志愿子项汇总上限1分")
            else:
                score = str(Decimal(score)+1)

    elif category_code == "REWARD":
        if re.search(r"精神文明|见义勇为|勇斗歹徒|舍己救人|拾金不昧", compact) and re.search(r"表彰|荣誉|称号", compact):
            levels = {level for level, pattern in {"NATIONAL": r"国家级|全国", "PROVINCIAL": r"省级|省人民", "CITY": r"市级|市人民", "SCHOOL": r"校级|学校表彰", "COLLEGE": r"院级|书院表彰|学院表彰"}.items() if re.search(pattern, compact)}
            level = linked_facts.get("recognition_level") or (scope if scope in {"NATIONAL", "PROVINCIAL", "SCHOOL"} else ("" if linked.get("is_roster") else _single(levels)))
            if level:
                facts["recognition_level"] = level
                score, rule = {"NATIONAL": "5", "PROVINCIAL": "4", "CITY": "3", "SCHOOL": "2", "COLLEGE": "1"}[level], "PDF9_HONOR"
        if score is None:
            issues.append("需核对荣誉或表彰行为及表彰机构级别")

    result = dict(suggestion)
    payload.update(facts)
    result["payload_json"] = payload
    if requires_result and linked.get("status") != "MATCHED":
        issues.append("未能将材料中的结果明确关联到本人，请人工核对本人的等级或考评")
    if category_code == "SERVICE" and not requires_result and (linked.get("is_roster") or int(linked.get("identity_count") or 0) <= 0):
        issues.append("服务工时或见习证明尚未明确关联本人，需核对本人对应记录")
    if score is not None and not issues:
        result["requested_score"] = _amount(score)
        result["explanation"] = f"依据已关联本人的材料事实及评定办法，按{rule}建议{_amount(score)}分，请核对原件。"
        return result, {"status": "RULE_APPLIED", "version": POLICY_VERSION, "rule": rule, "facts": facts, "requires_confirmation": False}, []
    result["requested_score"] = _amount(revision.get("requested_score", 0))
    issues = list(dict.fromkeys(issues or ["材料尚不足以确定政策计分，请核对原件"]))
    result["explanation"] = "；".join(issues) + "。分值保留当前稿，请核对。"
    return result, {"status": "NEEDS_REVIEW", "version": POLICY_VERSION, "rule": rule, "facts": facts, "requires_confirmation": True}, issues
