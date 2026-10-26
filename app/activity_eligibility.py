"""Current qualification uses only the visible activity, category and school year."""
from __future__ import annotations


def simplified_eligibility(versions, category_id, year_id):
    """Reuse broad recognition only when hidden historical scopes cannot conflict.

    Callers provide the latest non-removed version of each stored scope. Keeping
    this pure lets catalogue search and submission share a decision without an
    extra database query for every search result.
    """
    relevant = [version for version in versions
                if (not category_id or version['category_id'] == category_id)
                and (not version.get('academic_year_id') or version['academic_year_id'] == year_id)]
    scoped = [version for version in relevant
              if any(version.get(key) for key in ('edition_key', 'track_key', 'group_key'))]
    broad = [version for version in relevant if version not in scoped]
    if not broad:
        return {'decision': 'UNKNOWN', 'version_id': None, **({'scope_required': True} if scoped else {})}
    year_specific = any(version.get('academic_year_id') for version in broad)
    applicable = [version for version in broad if bool(version.get('academic_year_id')) == year_specific]
    decisions = {version['decision'] for version in applicable}
    if len(decisions) != 1 or any(version['decision'] not in decisions for version in scoped):
        return {'decision': 'UNKNOWN', 'version_id': None, 'conflict': True,
                'message': '活动存在不同范围认定，需辅导员确认本项有效性'}
    selected = max(applicable, key=lambda version: (version['effective_at'], version['version_no']))
    return {'decision': selected['decision'], 'version_id': selected['id'], 'reason': selected.get('reason')}
