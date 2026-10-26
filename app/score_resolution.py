"""Explicit scoring state for a draft; an uncomputed amount is never a zero award."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re


def _valid_amount(value):
    try:
        amount = Decimal(str(value))
        return str(amount.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)) if amount.is_finite() and amount >= 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _amount(value):
    return _valid_amount(value) or '0.00'


def _missing(values):
    result = []
    for value in values or []:
        if isinstance(value, dict) and value.get('code'):
            result.append({'code': str(value['code'])[:80], 'label': str(value.get('label') or value['code'])[:200]})
        elif isinstance(value, str):
            result.append({'code': value[:80], 'label': value[:200]})
    return result


def from_policy(policy, retained_value, proposed_value=None):
    """Only the deterministic policy layer can label an automatic score resolved."""
    policy = policy or {}
    proposed = _valid_amount(proposed_value)
    applied = policy.get('status') == 'RULE_APPLIED' and not policy.get('requires_confirmation') and proposed is not None
    missing = [] if applied else _missing(policy.get('missing_facts'))
    if policy.get('status') == 'RULE_APPLIED' and proposed is None:
        missing = [{'code': 'COMPUTED_AMOUNT', 'label': '有效的自动计分结果'}]
    if not applied and not missing:
        missing = [{'code': 'SCORING_BASIS', 'label': '可对应本人的完整计分依据'}]
    message = policy.get('message')
    if policy.get('status') == 'RULE_APPLIED' and proposed is None:
        message = '未取得有效的自动分值，请直接填写分值。'
    if not message:
        message = '已生成建议分值。' if applied else '自动计分依据不足，可直接填写分值。'
    message = re.sub(r'[，,]?\s*无需(?:填写)?理由[。.]?', '', str(message)).strip()
    return {'status': 'RULE_APPLIED' if applied else 'NEEDS_REVIEW',
            'value': proposed if applied else None,
            'retained_value': _amount(retained_value), 'missing_facts': missing,
            'message': str(message)[:1000], 'rule': policy.get('rule') or '', 'source': 'POLICY'}


def current(revision, analysis=None, *, material_changed=False):
    """Resolve the current revision, giving student decisions and frozen records priority."""
    value = _amount(revision.get('requested_score', 0))
    payload = revision.get('payload_json') or {}
    confirmation = payload.get('_score_confirmation') or {}
    explicitly_confirmed = isinstance(confirmation, dict) and confirmation.get('confirmed') is True and _valid_amount(confirmation.get('value')) == value
    frozen = bool(revision.get('content_frozen_at'))
    if frozen or revision.get('student_modified_score') or explicitly_confirmed:
        return {'status': 'MANUAL', 'value': value, 'retained_value': value, 'missing_facts': [],
                'message': '已提交或审核的分值。' if frozen else '',
                'rule': '', 'source': 'FROZEN' if frozen else 'STUDENT'}
    if material_changed or payload.get('_score_resolution_stale'):
        return {'status': 'NEEDS_REVIEW', 'value': None, 'retained_value': value,
                'missing_facts': [{'code': 'UPDATED_BASIS', 'label': '材料或计分依据更新后的分值'}],
                'message': '材料已更新，可重新分析或直接填写分值。', 'rule': '', 'source': 'STALE'}
    corrected = payload.get('policy_revalidation') or {}
    if corrected.get('status') == 'RULE_APPLIED':
        return from_policy(corrected, value, value)
    if not analysis or analysis.get('status') != 'SUCCEEDED':
        if Decimal(value) > 0:
            return {'status': 'MANUAL', 'value': value, 'retained_value': value, 'missing_facts': [],
                    'message': '当前已填写分值，请核对。', 'rule': '', 'source': 'EXISTING_DRAFT'}
        return {'status': 'UNANALYZED', 'value': None, 'retained_value': value, 'missing_facts': [],
                'message': '可分析材料或直接填写分值。', 'rule': '', 'source': 'DRAFT'}
    result = analysis.get('result_json') or {}
    suggestion = result.get('suggestion') or {}
    policy = result.get('score_policy') or {}
    resolution = from_policy(policy, value, suggestion.get('requested_score', analysis.get('suggested_score')))
    if resolution['status'] == 'RULE_APPLIED' and resolution['value'] != value:
        return {'status': 'NEEDS_REVIEW', 'value': None, 'retained_value': value,
                'missing_facts': [{'code': 'CURRENT_AMOUNT', 'label': '当前填写分值与识别建议的对应关系'}],
                'message': '当前填写与识别建议不同，可直接保存或提交。', 'rule': policy.get('rule') or '', 'source': 'DRAFT'}
    return resolution
