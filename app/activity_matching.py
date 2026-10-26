"""Search public activity metadata locally; matching is never qualification approval."""
from __future__ import annotations

from collections import defaultdict
from difflib import SequenceMatcher
import re
import unicodedata

from fastapi import APIRouter, Depends, HTTPException, Query

from . import db
from .migrations import active_year
from .security import current_user, require
from .activity_eligibility import simplified_eligibility


router = APIRouter(prefix='/api', tags=['activity matching'])
_GENERIC = ('西安交通大学', '全国大学生', '大学生', '高校', '活动证明', '证明材料', '获奖证明',
            '参赛证明', '证明', '材料', '附件', '获奖名单', '名单', '学年度', '学年', '年度',
            '国家级', '省级', '校级', '一等奖', '二等奖', '三等奖', '特等奖', '优秀奖',
            '竞赛', '比赛', '大赛', '活动', '项目', '考核', '考评')
_STATUS_ORDER = {'VALID': 0, 'INVALID': 1, 'UNKNOWN': 2}


def _normalize(value):
    return re.sub(r'[^a-z0-9\u4e00-\u9fff]', '', unicodedata.normalize('NFKC', str(value or '')).lower())


def _content(value):
    text = unicodedata.normalize('NFKC', str(value or '')).lower()
    text = re.sub(r'\d{4}(?:\s*[-~至]\s*\d{4})?\s*(?:学年度|学年|年度|年)?|第[一二三四五六七八九十百\d]+届', '', text)
    text = _normalize(text)
    for word in _GENERIC:
        text = text.replace(word, '')
    return text


def _grams(value):
    return {value[index:index+size] for size in (2, 3) for index in range(max(0, len(value)-size+1))}


def _rank(query, name, aliases, prepared=None):
    query_normal, query_content, query_grams = prepared if prepared is not None else (_normalize(query), _content(query), None)
    if not query_normal:
        return 0.0, 'RELATED'
    if query_grams is None:
        query_grams = _grams(query_content)
    best, kind = 0.0, 'RELATED'
    for field, text in [('EXACT_NAME', name), *(('EXACT_ALIAS', alias) for alias in aliases)]:
        normal, content = _normalize(text), _content(text)
        if not normal:
            continue
        # Exact short user queries are useful in search. A short/generic token
        # somewhere in a long proof cannot authorize an automatic association.
        exact = query_normal == normal or (normal in query_normal and len(content) >= 3)
        if exact:
            score = 1.0 if field == 'EXACT_NAME' else .97
            if score > best:
                best, kind = score, field
            continue
        if len(query_content) >= 2 and query_content in content:
            score = .80 + .10 * min(1, len(query_content)/max(len(content), 1))
        else:
            name_grams = _grams(content)
            overlap = name_grams & query_grams
            if not overlap:
                continue
            coverage = len(overlap)/max(len(name_grams), 1)
            union = len(name_grams | query_grams)
            ratio = SequenceMatcher(None, content, query_content[:6000], autojunk=False).ratio()
            score = .62 * coverage + .22 * len(overlap)/max(union, 1) + .16 * ratio
        if score > best:
            best, kind = min(.94, score), 'RELATED'
    return round(best, 4), kind


def _catalog(conn):
    aliases = defaultdict(list)
    for alias in db.rows(conn, 'activity_aliases', status='ACTIVE'):
        aliases[alias['activity_id']].append(alias['alias'])
    latest = {}
    for version in db.rows(conn, 'activity_eligibility_versions'):
        key = (version['activity_id'], version['category_id'], version['scope_key'])
        previous = latest.get(key)
        if not previous or (version['version_no'], version['effective_at']) > (previous['version_no'], previous['effective_at']):
            latest[key] = version
    versions = defaultdict(list)
    for version in latest.values():
        if version['decision'] != 'REMOVED':
            versions[version['activity_id']].append(version)
    return aliases, versions


def _relevant(versions, category_id, year_id):
    return [version for version in versions if (not category_id or version['category_id'] == category_id)
            and (not version.get('academic_year_id') or version['academic_year_id'] == year_id)]


def _view(conn, activity, aliases, versions, category_id, year_id, score=0.0, kind='RELATED'):
    relevant = _relevant(versions, category_id, year_id)
    eligibility = simplified_eligibility(versions, category_id, year_id)
    if activity['status'] != 'ACTIVE':
        eligibility = {'decision': 'UNKNOWN', 'version_id': None}
    selected = next((version for version in relevant if version['id'] == eligibility['version_id']), None)
    scope = {key: selected.get(key) for key in ('category_id', 'academic_year_id', 'edition_key', 'track_key', 'group_key')} if selected else {}
    decision = eligibility['decision'] if activity['status'] == 'ACTIVE' else 'UNKNOWN'
    status = {'VALID': '已认定有效', 'INVALID': '已认定无效', 'UNKNOWN': '待认定'}[decision]
    if activity['status'] != 'ACTIVE':
        status, scope_label = '已停用', '保留原匹配记录'
    elif selected:
        scope_label = '仅限所选学年' if selected.get('academic_year_id') else '不限学年'
        if any(selected.get(key) for key in ('edition_key', 'track_key', 'group_key')):
            scope_label += ' · 限定活动范围'
    elif relevant:
        status, scope_label = '需核对适用范围', '存在限定范围认定'
    else:
        scope_label = '当前学年或类别暂无认定'
    return {'id': activity['id'], 'canonical_name': activity['canonical_name'], 'name': activity['canonical_name'],
            'aliases': sorted(set(aliases)), 'decision': decision, 'status_label': status, 'scope_label': scope_label,
            'eligibility': {'decision': decision, 'version_id': eligibility.get('version_id'), 'scope': scope},
            'match_score': score, 'match_kind': kind, 'exact_match': kind in ('EXACT_NAME', 'EXACT_ALIAS')}


def _search(conn, query, category_id, year_id, *, current_activity_id=None):
    aliases, versions = _catalog(conn)
    found = []
    content = _content(query)
    prepared = (_normalize(query), content, _grams(content))
    for activity in db.rows(conn, 'activities', status='ACTIVE'):
        if not _relevant(versions[activity['id']], category_id, year_id) and activity['id'] != current_activity_id:
            continue
        score, kind = _rank(query, activity['canonical_name'], aliases[activity['id']], prepared)
        if query.strip() and score < .22 and activity['id'] != current_activity_id:
            continue
        found.append((activity, score, kind))
    # Expensive scope views only for selected hits, not every library row.
    found.sort(key=lambda item: (-item[1], _normalize(item[0]['canonical_name']), item[0]['id']))
    return found, aliases, versions


def retrieve_activity_candidates(conn, texts, category_id, year_id, limit=30, current_activity_id=None):
    """Rank the entire recognized library; return candidates, never an approval."""
    if isinstance(texts, str):
        query = texts[:48000]
    else:
        query = '\n'.join(str(item.get('text') or '') if isinstance(item, dict) else str(item) for item in texts or [])[:48000]
    found, aliases, versions = _search(conn, query, category_id, year_id, current_activity_id=current_activity_id)
    selected = found[:max(1, min(int(limit), 100))]
    if current_activity_id and not any(item[0]['id'] == current_activity_id for item in selected):
        current = next((item for item in found if item[0]['id'] == current_activity_id), None)
        if current:
            selected = [*selected[:-1], current]
    return [_view(conn, activity, aliases[activity['id']], versions[activity['id']], category_id, year_id, score, kind)
            for activity, score, kind in selected]


def _scope(conn, category_id, year_id):
    year = db.one(conn, 'academic_years', year_id) if year_id else active_year(conn)
    if not year:
        raise HTTPException(404, '学年不存在')
    if category_id and not db.one(conn, 'score_categories', category_id):
        raise HTTPException(404, '加分类别不存在')
    return category_id, year['id']


@router.get('/activities/search')
def search_activities(q: str = Query(default='', max_length=250), year_id: str | None = None,
                      category_id: str | None = None, limit: int = Query(default=20, ge=1, le=100),
                      offset: int = Query(default=0, ge=0), user=Depends(current_user)):
    with db.read() as conn:
        require(conn, user, 'activity.read')
        category_id, year_id = _scope(conn, category_id, year_id)
        found, aliases, versions = _search(conn, q, category_id, year_id)
        if not q.strip():
            # The empty-query browser is stable, and shows both positive and
            # negative recognition rather than hiding invalid activities.
            items = [_view(conn, activity, aliases[activity['id']], versions[activity['id']], category_id, year_id)
                     for activity, _, _ in found]
            items.sort(key=lambda item: (_STATUS_ORDER[item['decision']], _normalize(item['canonical_name']), item['id']))
            selected = items[offset:offset+limit]
        else:
            selected = [_view(conn, activity, aliases[activity['id']], versions[activity['id']], category_id, year_id, score, kind)
                        for activity, score, kind in found[offset:offset+limit]]
        return {'items': selected, 'total': len(found), 'limit': limit, 'offset': offset, 'query': q}


@router.get('/activity-matches/{activity_id}')
def matched_activity(activity_id: str, year_id: str | None = None, category_id: str | None = None,
                     user=Depends(current_user)):
    with db.read() as conn:
        require(conn, user, 'activity.read')
        category_id, year_id = _scope(conn, category_id, year_id)
        activity = db.one(conn, 'activities', activity_id)
        if not activity:
            raise HTTPException(404, '活动不存在')
        aliases, versions = _catalog(conn)
        return _view(conn, activity, aliases[activity_id], versions[activity_id], category_id, year_id)
