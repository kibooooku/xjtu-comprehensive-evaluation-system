"""Evidence-backed suggestions from the supplied 2018 policy; never a manual-score validator.

Only automatic suggestions use these tables. Students can still edit their score;
publication/role eligibility and incomplete certificates remain human decisions.
"""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .award_scope import scope_levels


POLICY_VERSION = "xjtu-2018-evidence-v2"
FACT_KEYS = ("award_level", "award_grade", "competition_kind", "research_type", "project_level",
             "publication_level", "author_rank", "assessment_result", "position_tier", "tenure_months",
             "practice_type", "volunteer_hours", "record_level", "recognition_level", "recognition_kind",
             "activity_count", "theory_level", "excellent_trainee", "award_system", "placement_level")
TARGET_CATEGORIES = {"COLLECTIVE", "IDEOLOGY", "RESEARCH", "ARTS", "POST", "SERVICE", "REWARD"}
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
    numbers = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12}
    if value in numbers:
        return numbers[value]
    if value.count('十') == 1:
        left, right = value.split('十')
        if (not left or left in numbers) and (not right or right in numbers):
            return (numbers[left] if left else 1) * 10 + (numbers[right] if right else 0)
    return None


def _scope(text):
    return _single(scope_levels(text))


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
    explicit = re.findall(r"(?:任期|任职|工作时间)(?:已|为|满|达|累计)?([+\-−]?\d+(?:\.\d+)?)个?月", text)
    if any((number := _number_fact(value.replace('−', '-'))) is None or number != int(number) for value in explicit):
        return 'INVALID'
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


def _count(text):
    counts = set()
    for value in re.findall(r"(?<![每\d.])([+\-−]?(?:\d+(?:\.\d+)?|[零一二两三四五六七八九十百千]+))(?:次|场)", text):
        if re.fullmatch(r'[+\-−]?\d+(?:\.\d+)?', value):
            number = _number_fact(value.replace('−', '-'))
            if number is None or number != int(number):
                return None
            counts.add(int(number))
        else:
            number = _number(value)
            if number is None:
                return None
            counts.add(number)
    return next(iter(counts)) if len(counts) == 1 else None


def _number_fact(value):
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and number >= 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _contexts(linked, full_text, identity):
    """Personal fields never come from other people's rows or common headings."""
    located = 'personal_contexts' in linked or 'common_contexts' in linked
    personal = _text('\n'.join(str(item.get('text') or '') for item in linked.get('personal_contexts') or [] if isinstance(item, dict)))
    common = _text('\n'.join(str(item.get('text') or '') for item in linked.get('common_contexts') or [] if isinstance(item, dict)))
    if not located:
        # Compatibility with existing non-roster certificates and legacy tests.
        personal = full_text if identity and not linked.get('is_roster') else ''
        common = full_text
    return personal, common, located


def _missing_facts(issues, facts):
    labels = [
        ('未能将计分依据明确关联', 'IDENTITY', '可明确对应本人的记录'),
        ('本人结果存在冲突', 'PERSONAL_RESULT', '本人对应的唯一计分结果'),
        ('活动次数', 'ACTIVITY_COUNT', '本人的活动次数'),
        ('培训次数', 'ACTIVITY_COUNT', '本人的培训次数'),
        ('理论学习培训', 'TRAINING_TYPE', '党团组织的理论学习培训依据'),
        ('突出贡献', 'MANUAL_DISCRETION', '突出贡献的人工认定'),
        ('竞赛级别', 'AWARD_LEVEL_AND_GRADE', '赛事级别和本人获奖等第'),
        ('高水平国际', 'INTERNATIONAL_ELIGIBILITY', '高水平国际竞赛认定'),
        ('第一署名单位', 'FIRST_AFFILIATION', '西安交通大学第一署名单位'),
        ('论文类别', 'PUBLICATION_LEVEL_AND_RANK', '论文类别及本人作者排序'),
        ('专著作者', 'AUTHOR_RANK', '本人专著作者排序或参编身份'),
        ('第九', 'MONOGRAPH_POLICY', '专著第九及以后作者的政策边界'),
        ('专利', 'PATENT_POLICY', '专利条款的人工核定'),
        ('结项', 'PROJECT_POLICY', '科研项目独立计分政策'),
        ('科研成果类型', 'RESEARCH_TYPE', '科研成果类型'),
        ('文艺或体育', 'COMPETITION_KIND_LEVEL_AND_GRADE', '文体计分方式、级别和本人奖次或名次'),
        ('岗位层级', 'POSITION_TIER', '本人的岗位层级'),
        ('任职考评', 'ASSESSMENT', '本人任职考评结果'),
        ('任职满一年', 'TENURE', '本人满一年任期'),
        ('不足一年', 'PARTIAL_TENURE', '任职不足一年的人工酌定分值'),
        ('服务类型', 'PRACTICE_TYPE', '社会服务类型'),
        ('志愿服务工时', 'VOLUNTEER_HOURS', '本人的有效志愿服务工时'),
        ('社会实践', 'PRACTICE_LEVEL_AND_ASSESSMENT', '校级实践及本人团队成员身份和考评'),
        ('见习', 'PLACEMENT_LEVEL', '挂职或见习的组织级别'),
        ('额外荣誉', 'ADDITIONAL_HONOR', '额外荣誉及加分依据'),
        ('荣誉或表彰', 'HONOR_KIND_AND_LEVEL', '本人的表彰行为和表彰机构级别'),
        ('否定', 'NEGATED_BASIS', '原件中否定表述与实际计分依据'),
    ]
    result = []
    for issue in issues:
        if '竞赛级别及获奖等第' in issue or '文艺或体育类别' in issue:
            fields = [('award_level', 'AWARD_LEVEL', '赛事级别'), ('award_grade', 'AWARD_GRADE', '本人获奖等第或实际名次')]
            missing = [{'code': code, 'label': label} for key, code, label in fields if not facts.get(key)]
            if not missing:
                missing = [{'code': 'AWARD_POLICY', 'label': '当前级别和奖项对应的政策金额或计分方式'}]
            for item in missing:
                if item not in result:
                    result.append(item)
            continue
        match = next(((code, label) for fragment, code, label in labels if fragment in issue), ('SCORING_BASIS', issue))
        item = {'code': match[0], 'label': match[1]}
        if item not in result:
            result.append(item)
    return result


def normalize_policy_suggestion(category_code, texts, revision, suggestion, linked_outcome=None, student_name=""):
    """Return a new suggestion and safe provenance, without mutating any stored draft."""
    if category_code == 'POLITICAL':
        category_code = 'IDEOLOGY'
    if category_code not in TARGET_CATEGORIES:
        result = dict(suggestion)
        result['requested_score'] = _amount(revision.get('requested_score', 0))
        message = '当前大类尚无自动计分规则，请手动核定。'
        return result, {'status': 'NEEDS_REVIEW', 'version': POLICY_VERSION, 'requires_confirmation': True,
                        'value': None, 'retained_value': result['requested_score'], 'message': message,
                        'missing_facts': [{'code': 'CATEGORY_RULE', 'label': '当前大类的计分规则'}]}, [message]
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
    linked = linked_outcome or {}
    identity = bool(linked.get('identity_matched')) if 'identity_matched' in linked else bool(linked.get('identity_count') or linked.get('status') == 'MATCHED')
    personal, common, located = _contexts(linked, compact, identity)
    contextual = personal + '\n' + common
    allowed_facts = linked.get('status') == 'MATCHED' or (located and identity and linked.get('status') != 'AMBIGUOUS')
    linked_facts = linked.get('facts') if allowed_facts and isinstance(linked.get('facts'), dict) else {}
    # Do not undo a completed layout check by concatenating raw OCR response
    # order again: it can join separate columns or hide a conflicting round.
    conflicts = any(anchor.get('award_level_conflict') for anchor in linked.get('anchors') or [])
    checked_levels = {level for context in linked.get('common_contexts') or [] for level in context.get('award_levels') or []}
    if linked.get('award_level_checked'):
        # A checked-but-unresolved layout must not be rescued by joining its
        # raw personal text again. Only geometry-validated common levels apply.
        scope = '' if conflicts else (linked_facts.get('award_level') or _single(checked_levels))
    else:
        scope = linked_facts.get('award_level') or (_scope(contextual) if located and not conflicts else ('' if linked.get('is_roster') else _scope(compact)))
    # A document containing multiple people's results cannot provide the user's
    # grade merely because a grade appears somewhere in its text. The geometry
    # linkage service supplies the grade/assessment attached to this applicant.
    grade = str(linked_facts.get('award_grade') or (_grade(personal) if located and identity and linked.get('status') != 'AMBIGUOUS' else ''))
    assessment = {"QUALIFIED": "PASS", "UNQUALIFIED": "FAIL"}.get(linked_facts.get("assessment_result"), linked_facts.get("assessment_result", ""))
    if not assessment and located and identity and linked.get('status') != 'AMBIGUOUS':
        assessment = _assessment(personal)
    if scope and category_code == "ARTS":
        facts["award_level"] = scope
    if grade and category_code in {"RESEARCH", "ARTS"}:
        facts["award_grade"] = grade
    if assessment:
        facts["assessment_result"] = assessment
    if re.search(r'未参加|未参与|没有参加|没有参与|缺席|取消参加|未到场|已作废|已撤销|未认证|未通过认证|不予认定|不予计入|未参编|并非参编|未担任|未任职|没有担任', personal):
        issues.append('材料中的本人参与含否定或作废表述，请核对实际计分依据')
    if category_code in {'RESEARCH', 'ARTS', 'REWARD'} and re.search(r'未(?:获得|获评|荣获|被评为|被授予|被表彰|受到表彰)|没有(?:获得|获评)', personal):
        issues.append('材料中的本人获奖或表彰含否定表述，请核对实际结果')

    if category_code == 'COLLECTIVE':
        if re.search(r'突出贡献|重大贡献', personal):
            issues.append('集体活动突出贡献由人工认定，不能自动按大类上限计分')
        else:
            count = _number_fact(linked_facts.get('activity_count'))
            count = int(count) if count is not None and count == int(count) else _count(personal)
            if count is None and not re.search(r'[零一二两三四五六七八九十百千\d]+(?:次|场)', personal) and identity and re.search(r'活动|广场舞|夜跑|讲座|团日|班会|春游|出游|宣讲|联谊', contextual):
                count = 1  # One named activity with this person's participation; explicit counts win.
            if count is not None:
                facts['activity_count'] = str(count)
                score, rule = str(min(Decimal(3), Decimal(count) * Decimal('.3'))), 'CONFIRMED_COLLECTIVE_PER_EVENT'
            else:
                issues.append('需核对本人的活动次数')

    elif category_code == 'IDEOLOGY':
        training = bool(re.search(r'党团|党课|团课|党员|团员|政治|理论|青年大学习', contextual) and re.search(r'学习|培训|教育|研修|党课|团课', contextual))
        if not training:
            issues.append('需核对党团组织的理论学习培训依据')
        count = _number_fact(linked_facts.get('activity_count'))
        count = int(count) if count is not None and count == int(count) else _count(personal)
        if count is None and not re.search(r'[零一二两三四五六七八九十百千\d]+(?:次|场)', personal) and identity and training:
            count = 1
        if count is None:
            issues.append('需核对本人的培训次数')
        else:
            facts['activity_count'] = str(count)
            level = linked_facts.get('theory_level') or ('MINISTERIAL' if re.search(r'部级|部委组织', contextual) else scope)
            higher = level in {'PROVINCIAL', 'MINISTERIAL'}
            facts['theory_level'] = level or 'ORDINARY'
            negated_excellent = bool(re.search(r'(?:未|没有|不曾|并非|不是|非)(?:获得|获评|评为|被评为|获|荣获|授予|认定为)?优秀学员', personal))
            excellent = not negated_excellent and (linked_facts.get('excellent_trainee') in (True, 'YES', 'true', 'True') or bool(re.search(r'优秀学员', personal) or re.search(r'优秀学员(?:获奖)?名单', common)))
            facts['excellent_trainee'] = 'YES' if excellent else 'NO'
            score, rule = str(min(Decimal(3), Decimal(count) * (2 if higher else 1) + int(excellent))), 'PDF5_IDEOLOGY_TRAINING'

    elif category_code == "RESEARCH":
        kind = _research_type(contextual)
        if kind:
            facts["research_type"] = kind
        if kind == "COMPETITION":
            if scope:
                facts["award_level"] = scope
            table = RESEARCH_AWARDS.get(scope)
            index = {"SPECIAL": 0, "FIRST": 0, "SECOND": 1, "THIRD": 2, "EXCELLENT": 3}.get(grade)
            if scope == "INTERNATIONAL" and "高水平国际" not in contextual:
                issues.append("国际科研竞赛需核对是否属高水平国际项目")
            elif table and index is not None and index < len(table):
                score, rule = table[index], "PDF6_RESEARCH_COMPETITION"
            else:
                issues.append("需核对竞赛级别及获奖等第")
        elif kind in {"PAPER", "MONOGRAPH", "PATENT"}:
            rank = str(linked_facts.get("author_rank") or _personal_author_rank(personal, student_name))
            rank = rank if rank.isascii() and rank.isdigit() and int(rank) > 0 else ""
            if rank:
                facts["author_rank"] = rank
            affiliated = linked_facts.get('affiliation_confirmed') is True or bool(re.search(r"第一(?:署名|作者)?单位[:：]?(?:西安交通大学|Xi['’]?anJiaotongUniversity)", contextual, re.IGNORECASE))
            if not affiliated:
                issues.append("需核对第一署名单位是否为西安交通大学")
            if kind == "PAPER":
                publication = _single(value for value, pattern in {
                    "INTERNATIONAL_RECOGNIZED": r"学校认定的?国际期刊|校认定国际期刊",
                    "CORE_OR_INTL_CONFERENCE": r"国内核心期刊|国际会议论文",
                    "GENERAL_OR_DOMESTIC_CONFERENCE": r"国内一般期刊|国内会议论文",
                }.items() if re.search(pattern, contextual))
                if publication:
                    facts["publication_level"] = publication
                n = int(rank) if rank else None
                if affiliated and n and publication:
                    table = {"INTERNATIONAL_RECOGNIZED": ("10", "8", "6", "2"), "CORE_OR_INTL_CONFERENCE": ("6", "5", "4", "1"), "GENERAL_OR_DOMESTIC_CONFERENCE": ("3", "2", "1")}[publication]
                    if n <= len(table) or publication != "GENERAL_OR_DOMESTIC_CONFERENCE":
                        score, rule = table[min(n, len(table))-1], "PDF6_7_PAPER"
                        if "入围优秀论文" in personal and re.search(r"国际.*会议|国内.*会议", contextual):
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
                elif affiliated and "参编" in personal:
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
        sport = bool(re.search(r"体育|运动会|田径|篮球|足球|排球|乒乓|羽毛球|跳绳|绕绳|游泳|夜跑|长跑|短跑|跳高|跳远|健身操", contextual))
        art = bool(re.search(r"文艺|艺术|美术|书法|摄影|绘画|歌唱|舞蹈|器乐|音乐|设计赛", contextual))
        kind = "SPORT" if sport and not art else "ART" if art and not sport else ""
        if kind:
            facts["competition_kind"] = kind
        # A bound 一/二/三等奖 already establishes the prize system. It is a
        # different fact from a ranked PLACE_n and must keep its original enum.
        prize_band = grade in {'FIRST', 'SECOND', 'THIRD'}
        if prize_band and grade in {'FIRST', 'SECOND', 'THIRD'}:
            table, index = ART_AWARDS.get(scope), {'FIRST': 0, 'SECOND': 1, 'THIRD': 2}[grade]
            if table:
                facts['award_system'] = 'PRIZE'
                score, rule = table[index], 'PDF7_8_PRIZE_BAND'
        elif kind == "ART":
            table, index = ART_AWARDS.get(scope), {"FIRST": 0, "SECOND": 1, "THIRD": 2, "EXCELLENT": 3}.get(grade)
            if table and index is not None and index < len(table):
                score, rule = table[index], "PDF7_ART_COMPETITION"
        elif kind == "SPORT":
            place = int(grade.removeprefix("PLACE_")) if re.fullmatch(r"PLACE_[1-8]", grade) else None
            if place:
                facts["award_grade"] = f"PLACE_{place}"
                facts['award_system'] = 'RANK'
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
        tier = linked_facts.get("position_tier") or _position(personal)
        tier = tier if tier in POST_SCORES else ""
        tenure = linked_facts.get("tenure_months") or _tenure(personal) or _tenure(common)
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
            if not tier:
                issues.append('需核对本人岗位层级')
            if not assessment:
                issues.append('需核对本人任职考评结果')
            if tenure == 'PARTIAL' or (tenure.isdigit() and int(tenure) < 12):
                issues.append('任职不足一年，需由人工按工作时间酌情核定')
            elif not tenure:
                issues.append('需核对本人任职满一年的任期依据')

    elif category_code == "SERVICE":
        volunteer = linked_facts.get('practice_type') == 'VOLUNTEER' or "志愿" in contextual
        practice = linked_facts.get('practice_type') == 'SCHOOL_SOCIAL_PRACTICE' or "社会实践" in contextual
        placement = linked_facts.get('practice_type') == 'PLACEMENT' or bool(re.search(r"挂职|政府见习|国际组织见习|国际组织实习", contextual))
        if sum((volunteer, practice, placement)) != 1:
            issues.append("需核对本小项的服务类型和相应证明")
        elif volunteer:
            facts["practice_type"] = "VOLUNTEER"
            hours_fact = _number_fact(linked_facts.get('volunteer_hours'))
            raw_hours = re.findall(r"(?<![A-Za-z\d.])([+\-−]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+\-]?\d+)?)小时", personal)
            parsed_hours = [_number_fact(value.replace('−', '-')) for value in raw_hours]
            invalid_hours = any(value is None for value in parsed_hours) or (linked_facts.get('volunteer_hours') not in (None, '') and hours_fact is None)
            values = set() if invalid_hours else ({hours_fact} if hours_fact is not None else set(parsed_hours))
            if len(values) == 1:
                hours = next(iter(values));facts["volunteer_hours"] = str(hours)
                score, rule = str(min(Decimal(1), hours/32)), "PDF5_VOLUNTEER"
            else:
                issues.append("需核对本学年可计入的志愿服务工时")
        elif practice:
            facts["practice_type"] = "SCHOOL_SOCIAL_PRACTICE"
            school_practice = scope == "SCHOOL" or linked_facts.get("practice_type") == "SCHOOL_SOCIAL_PRACTICE" or ((located or not linked.get('is_roster')) and "校级" in contextual)
            if school_practice and assessment in {"EXCELLENT", "GOOD", "PASS"} and re.search(r"团队|队员|成员", contextual):
                score, rule = "1", "PDF5_SOCIAL_PRACTICE"
            elif assessment == "FAIL":
                score, rule = "0", "PDF5_SOCIAL_PRACTICE_FAIL"
            else:
                issues.append("社会实践需确认校级、团队成员身份及考核合格")
        elif placement:
            facts["practice_type"] = "PLACEMENT"
            placement_level = linked_facts.get('placement_level') or ('PROVINCIAL_OR_ABOVE' if re.search(r"省级|部级|国际组织", contextual) else ('CITY_OR_COUNTY' if re.search(r"市级|县级", contextual) else ''))
            if placement_level:
                facts['placement_level'] = placement_level
            if placement_level in {'PROVINCIAL_OR_ABOVE', 'PROVINCIAL', 'MINISTERIAL', 'INTERNATIONAL'}:
                score, rule = "2", "PDF5_PLACEMENT"
            elif placement_level in {'CITY_OR_COUNTY', 'CITY', 'COUNTY'}:
                score, rule = "1", "PDF5_PLACEMENT"
            else:
                issues.append("需核对挂职或政府见习的组织级别")
        explicit_personal_honor = bool(re.search(r'(?:荣获|获评|评为|授予|获得|认定为).{0,12}先进个人', personal)) and not re.search(r'未(?:获|获得|获评|被评为)|没有(?:获得|获评)', personal)
        extra_honor = linked_facts.get("advanced_individual") is True or explicit_personal_honor
        if linked_facts.get('advanced_team') is True:
            issues.append('材料含团队额外荣誉，办法未列独立固定金额，需人工核定')
        if not extra_honor and re.search(r"先进个人|受到表彰|获得.*表彰|荣获.*表彰", personal):
            issues.append("材料含额外荣誉或表彰，需核对是否属于本人及对应额外分")
        if extra_honor and score is not None:
            if volunteer:
                issues.append("志愿工时与额外荣誉需分别核定，当前年度志愿子项汇总上限1分")
            elif assessment == 'FAIL':
                issues.append('考核不合格与额外荣誉同时出现，需核对本小项对应关系')
            else:
                score = str(Decimal(score)+1)

    elif category_code == "REWARD":
        if re.search(r"精神文明|见义勇为|勇斗歹徒|舍己救人|拾金不昧|文明学生|道德模范", contextual) and re.search(r"表彰|荣誉|称号", contextual):
            facts['recognition_kind'] = 'MORAL_HONOR'
            levels = {level for level, pattern in {"NATIONAL": r"国家级|全国", "PROVINCIAL": r"省级|省人民", "CITY": r"市级|市人民", "SCHOOL": r"校级|学校表彰", "COLLEGE": r"院级|书院表彰|学院表彰"}.items() if re.search(pattern, contextual)}
            level = linked_facts.get("recognition_level") or _single(levels)
            if level:
                facts["recognition_level"] = level
                facts['recognition_kind'] = 'MORAL_HONOR'
                score, rule = {"NATIONAL": "5", "PROVINCIAL": "4", "CITY": "3", "SCHOOL": "2", "COLLEGE": "1"}[level], "PDF9_HONOR"
        elif re.search(r'好人好事|为学校赢得(?:了)?声誉|为学校争得.{0,8}荣誉|做出突出贡献', personal) and re.search(r'认定|表彰|表扬|获评|授予', contextual):
            count = _count(personal)
            count = 1 if count is None else count
            facts['recognition_kind'], facts['activity_count'] = 'GOOD_DEED', str(count)
            score, rule = str(min(5, count)), 'PDF9_GOOD_DEED'
        if score is None:
            issues.append("需核对荣誉或表彰行为及表彰机构级别")

    result = dict(suggestion)
    payload.update(facts)
    result["payload_json"] = payload
    if not identity:
        issues.append("未能将计分依据明确关联到本人，请人工核对本人记录")
    if linked.get('status') == 'AMBIGUOUS':
        issues.append('本人结果存在冲突，请人工核对本人对应的计分依据')
    if score is not None and not issues:
        result["requested_score"] = _amount(score)
        result["explanation"] = f"依据已关联本人的材料事实及评定办法，按{rule}建议{_amount(score)}分，请核对原件。"
        return result, {"status": "RULE_APPLIED", "version": POLICY_VERSION, "rule": rule, "facts": facts, "requires_confirmation": False,
                        'value': result['requested_score'], 'retained_value': _amount(revision.get('requested_score', 0)), 'missing_facts': [], 'reason_codes': [],
                        'message': f'已按本人的材料和计分规则建议{result["requested_score"]}分，请核对。'}, []
    result["requested_score"] = _amount(revision.get("requested_score", 0))
    issues = list(dict.fromkeys(issues or ["材料尚不足以确定政策计分，请核对原件"]))
    result["explanation"] = "；".join(issues) + "。分值保留当前稿，请核对。"
    missing = _missing_facts(issues, facts)
    return result, {"status": "NEEDS_REVIEW", "version": POLICY_VERSION, "rule": rule, "facts": facts, "requires_confirmation": True,
                    'value': None, 'retained_value': result['requested_score'], 'missing_facts': missing, 'reason_codes': [item['code'] for item in missing],
                    'message': '自动计分依据不足，可直接填写分值。'}, issues
