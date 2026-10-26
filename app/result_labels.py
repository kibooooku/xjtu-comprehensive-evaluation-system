"""One editable award/assessment label, with legacy structured facts retained."""
LEVELS = {'INTERNATIONAL': '国际级', 'NATIONAL': '国家级', 'PROVINCIAL': '省级', 'SCHOOL': '校级',
          'UNIVERSITY': '校级', 'LOCAL_OR_INDUSTRY': '地市或行业级', 'COLLEGE': '院级'}
GRADES = {'SPECIAL': '特等奖', 'FIRST': '一等奖', 'SECOND': '二等奖', 'THIRD': '三等奖', 'EXCELLENT': '优秀奖',
          **{f'PLACE_{number}': f'第{number}名' for number in range(1, 9)}}
ASSESSMENTS = {'EXCELLENT': '优秀', 'GOOD': '良好', 'PASS': '合格', 'FAIL': '不合格'}


def result_label(payload, *, explicit=True, category_code=None):
    payload = payload or {}
    if explicit and 'result_label' in payload:
        return str(payload.get('result_label') or '')
    assessment = ASSESSMENTS.get(payload.get('assessment_result'), str(payload.get('assessment_result') or ''))
    if category_code == 'POST' and assessment:
        return assessment
    grade = GRADES.get(payload.get('award_grade'), str(payload.get('award_grade') or ''))
    level = LEVELS.get(payload.get('award_level'), str(payload.get('award_level') or ''))
    if grade:
        return level if grade in level else level + grade
    if assessment:
        return assessment
    return '优秀学员' if payload.get('excellent_trainee') in (True, 'YES', 'True', 'true') else ''
