"""Durable OCR/AI work queue, independent of the current browser page."""
from __future__ import annotations
import os
import threading
import time
import re
import logging
import traceback
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import OperationalError
from . import db
from .limits import OCR_POLICY_VERSION
from .migrations import active_year, assert_current_year, ensure_group
from .security import current_user

router = APIRouter(prefix='/api', tags=['background recognition'])
ACTIVE = {'QUEUED', 'RUNNING', 'PAUSED'}
OCR_FINGERPRINT_KIND = 'OCR_FILES_V1'
WAITING_FOR_OCR = '等待OCR完成（原OCR任务继续执行）'
OCR_START_STAGE = '检查材料与PDF文字层，可能需要一点时间'
_workers = []
OWNER_CONCURRENCY = {'OCR': 3, 'AI': 3}
GLOBAL_CONCURRENCY = {'OCR': 12, 'AI': 12}
PRESENCE_TTL_SECONDS = 45
CONTROL_PREFIX = 'task_control:'


class JobOwnershipLost(Exception):
    pass


class JobPauseRequested(Exception):
    pass


def _control(conn, owner_id):
    row = db.one(conn, 'system_settings', CONTROL_PREFIX + owner_id)
    value = (row or {}).get('value_json')
    return dict(value) if isinstance(value, dict) else {}


def _save_control(conn, owner_id, value):
    key = CONTROL_PREFIX + owner_id
    existing = db.one(conn, 'system_settings', key)
    previous = (existing or {}).get('value_json') or {}
    revision = previous.get('revision', 0) if isinstance(previous, dict) else 0
    value['revision'] = (revision if type(revision) is int else 0) + 1
    if existing:
        db.change(conn, 'system_settings', key, value_json=value, updated_at=db.now())
    else:
        db.add(conn, 'system_settings', id=key, value_json=value)


def _live_tabs(control):
    now = time.time()
    return {key: value for key, value in (control.get('tabs') or {}).items()
            if isinstance(value, dict) and isinstance(value.get('seen_at'), (int, float))
            and now - value['seen_at'] < PRESENCE_TTL_SECONDS}


def pause_owner(conn, owner_id, reason='MANUAL'):
    """Keep in-flight ownership until the provider returns; block every next request."""
    control = _control(conn, owner_id)
    control.update(paused=True, reason=reason, paused_at=db.now())
    _save_control(conn, owner_id, control)
    for job in db.rows(conn, 'background_jobs', owner_id=owner_id):
        if job['state'] not in ('QUEUED', 'RUNNING', 'PAUSED'):
            continue
        meta = {**(job.get('result_json') or {}), 'pause_requested': True}
        if job['state'] == 'RUNNING':
            db.change(conn, 'background_jobs', job['id'], stage='正在暂停，保留本次已发请求结果', result_json=meta)
        else:
            db.change(conn, 'background_jobs', job['id'], state='PAUSED', stage='已暂停，点击继续处理',
                      worker_token=None, result_json=meta)
    return control


def resume_owner(conn, owner_id):
    control = _control(conn, owner_id)
    control.update(paused=False, reason='', resumed_at=db.now())
    _save_control(conn, owner_id, control)
    for job in db.rows(conn, 'background_jobs', owner_id=owner_id):
        if job['state'] not in ACTIVE:
            continue
        meta = dict(job.get('result_json') or {})
        meta.pop('pause_requested', None)
        if job['state'] == 'PAUSED':
            db.change(conn, 'background_jobs', job['id'], state='QUEUED', stage='继续处理，复用已完成结果',
                      result_json=meta, worker_token=None, finished_at=None)
        else:
            db.change(conn, 'background_jobs', job['id'], result_json=meta)
    return control


def _job_paused(conn, job):
    return bool((job.get('result_json') or {}).get('pause_requested') or _control(conn, job['owner_id']).get('paused'))


def raise_if_pause_requested(job_id, worker_token):
    with db.read() as conn:
        job = db.one(conn, 'background_jobs', job_id)
        if not job or job['state'] != 'RUNNING' or job.get('worker_token') != worker_token:
            raise JobOwnershipLost()
        if _job_paused(conn, job):
            raise JobPauseRequested()


def _expire_presence(conn):
    for setting in db.rows(conn, 'system_settings'):
        if not setting['id'].startswith(CONTROL_PREFIX):
            continue
        value = setting.get('value_json') or {}
        if value.get('tracking') and not value.get('paused') and not _live_tabs(value):
            pause_owner(conn, setting['id'][len(CONTROL_PREFIX):], 'CONNECTION_LOST')


def _control_view(control):
    return {'paused': bool(control.get('paused')), 'reason': control.get('reason') or '',
            'revision': control.get('revision', 0),
            'presence_ttl_seconds': PRESENCE_TTL_SECONDS}


def _student_control_user(user):
    if user['base_identity'] != 'STUDENT':
        raise HTTPException(403, '仅学生管理本人上传和识别任务')


def _active_control_user(conn, user):
    current = db.one(conn, 'users', user['id'])
    if not current or current['status'] != 'ACTIVE':
        raise HTTPException(401, '账号已停用或删除，请重新登录')


def _tab_id(body):
    value = body.get('tab_id')
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', value):
        raise HTTPException(400, '页面标识无效')
    return value


@router.get('/analysis-control')
def get_control(user=Depends(current_user)):
    _student_control_user(user)
    with db.read() as conn:
        return _control_view(_control(conn, user['id']))


@router.post('/analysis-control')
def change_control(body: dict, user=Depends(current_user)):
    _student_control_user(user)
    with db.write() as conn:
        _active_control_user(conn, user)
        if body.get('action') == 'pause':
            value = pause_owner(conn, user['id'], 'LOGOUT' if body.get('reason') == 'LOGOUT' else 'MANUAL')
        elif body.get('action') == 'resume':
            value = resume_owner(conn, user['id'])
        else:
            raise HTTPException(400, '请选择暂停或继续')
        return _control_view(value)


@router.post('/analysis-control/presence')
def presence(body: dict, user=Depends(current_user)):
    _student_control_user(user)
    tab = _tab_id(body)
    with db.write() as conn:
        _active_control_user(conn, user)
        value = _control(conn, user['id'])
        tabs = _live_tabs(value)
        document = str(body.get('document_id') or '')[:100]
        tabs[tab] = {'seen_at': time.time(), 'document_id': document}
        value.update(tracking=True, tabs=tabs)
        _save_control(conn, user['id'], value)
        if (body.get('reload') is True and value.get('reason') == 'PAGE_CLOSED'
                and value.get('last_left_tab') == tab and time.time() - value.get('left_at', 0) < 15):
            value = resume_owner(conn, user['id'])
        return _control_view(value)


@router.post('/analysis-control/leave')
def leave(body: dict, user=Depends(current_user)):
    _student_control_user(user)
    tab = _tab_id(body)
    with db.write() as conn:
        _active_control_user(conn, user)
        value = _control(conn, user['id'])
        tabs = _live_tabs(value)
        document = str(body.get('document_id') or '')[:100]
        current = tabs.get(tab)
        if current and current.get('document_id') != document:
            return _control_view(value)  # A refreshed document already replaced this page.
        tabs.pop(tab, None)
        value.update(tracking=True, tabs=tabs, last_left_tab=tab, left_at=time.time())
        _save_control(conn, user['id'], value)
        if not tabs:
            value = pause_owner(conn, user['id'], 'PAGE_CLOSED')
        return _control_view(value)


def _owns_job(conn, job_id, worker_token):
    current = db.one(conn, 'background_jobs', job_id)
    return bool(current and current['state'] == 'RUNNING' and current.get('worker_token') == worker_token)


def _policy_current(job):
    return (job.get('result_json') or {}).get('policy_version') == OCR_POLICY_VERSION


def _mark_policy_expired(conn, job):
    message = '识别策略已更新，请按当前规则重新分析；历史结果保留'
    return db.change(conn, 'background_jobs', job['id'], state='STALE', stage=message,
                     error_message=message, finished_at=db.now(), worker_token=None,
                     result_json={**(job.get('result_json') or {}), 'policy_expired': True,
                                  'expected_policy_version': OCR_POLICY_VERSION})


def _planned_total(conn, revision):
    from .materials import _page_limit
    # An upper bound before native text extraction; the material runner reports
    # actual planned/completed cloud tasks, which replace this initial estimate.
    total = 0
    max_pages = _page_limit()
    for attachment in db.rows(conn, 'revision_files', revision_id=revision['id']):
        count = len(db.rows(conn, 'file_pages', file_id=attachment['file_id']))
        if count <= max_pages:
            total += count + min(count, 2)
        else:
            # Long documents keep a bounded first/last-page text + seal plan.
            # Existing native text can reduce the real plan to seals only.
            total += min(count, 2) * 2
    return total


def job_view(job):
    if not job:
        return None
    result = {k: v for k, v in job.items() if k not in ('worker_token',)}
    dependency = (job.get('result_json') or {}).get('wait_for_ocr_job_id')
    result.update(wait_for_ocr_job_id=dependency,
                  waiting_for_ocr=bool(dependency and job['state'] == 'QUEUED' and job['stage'] == WAITING_FOR_OCR),
                  requires_manual=bool((job.get('result_json') or {}).get('status') == 'MANUAL_REQUIRED' or (job.get('result_json') or {}).get('manual_required_files')),
                  partial=bool((job.get('result_json') or {}).get('partial')),
                  policy_version=(job.get('result_json') or {}).get('policy_version'))
    return result


def latest_jobs(conn, application_id):
    found = sorted(db.rows(conn, 'background_jobs', application_id=application_id), key=lambda j: j['created_at'], reverse=True)
    return {kind: next((job_view(j) for j in found if j['kind'] == kind), None) for kind in ('OCR', 'AI')}


def _pristine(conn, revision):
    if revision['student_modified_score']:
        return False
    payload = revision.get('payload_json') or {}
    if payload.get('content_manually_edited') or payload.get('evidence_manually_edited') or payload.get('_score_confirmation'):
        return False
    clean = {k: v for k, v in payload.items() if k not in ('ai_explanation', 'ai_match_uncertain', 'policy_revalidation') and v not in ('', None)}
    if not revision.get('ai_analysis_id'):
        return not revision['title'].strip() and db.money(revision['requested_score']) == 0 and not clean
    analysis = db.one(conn, 'ai_analyses', revision['ai_analysis_id'])
    if not analysis or (analysis.get('result_json') or {}).get('policy_version') != OCR_POLICY_VERSION:
        return False
    suggestion = ((analysis or {}).get('result_json') or {}).get('suggestion') or {}
    expected = {k: v for k, v in (suggestion.get('payload_json') or {}).items() if v not in ('', None)}
    return bool(suggestion) and revision['title'] == suggestion.get('title') and db.money(revision['requested_score']) == db.money(suggestion.get('requested_score', -1)) and clean == expected


def _context(conn, application_id, user):
    from .workflow import app_context, editable_revision
    context = app_context(conn, application_id)
    revision = editable_revision(conn, context['application'], user)
    assert_current_year(conn, context['year']['id'])
    _, item = ensure_group(conn, context['application'])
    if not item['confirmed_at'] or item['deleted_at']:
        raise HTTPException(409, '请先在上传窗口确认本批材料，未确认材料不会启动识别')
    if not db.rows(conn, 'revision_files', revision_id=revision['id']):
        raise HTTPException(400, '请先上传证明材料')
    return context, revision


def _normalize_queued_ocr(conn, job):
    """Upgrade persisted V2 queue records; never replace/cancel a live OCR task.

    A legacy queued task has not started reading material. Its current working
    revision can safely supply the file-only snapshot; the old content snapshot
    stays in metadata for legacy AI dependency matching.
    """
    if job['kind'] != 'OCR' or job['state'] != 'QUEUED':
        return job
    meta = dict(job.get('result_json') or {})
    if meta.get('fingerprint_kind') == OCR_FINGERPRINT_KIND and _policy_current(job):
        return job if meta.get('auto_analyze') else db.change(conn, 'background_jobs', job['id'], result_json={**meta, 'auto_analyze': True})
    application = db.one(conn, 'applications', job['application_id'])
    revision = db.one(conn, 'application_revisions', job['input_revision_id'])
    if not application or not revision or application['working_revision_id'] != revision['id']:
        return job
    from .materials import ocr_input_fingerprint
    meta.update(fingerprint_kind=OCR_FINGERPRINT_KIND, policy_version=OCR_POLICY_VERSION,
                auto_analyze=True,
                previous_policy_version=meta.get('policy_version'),
                legacy_content_input_fingerprint=job['input_fingerprint'],
                content_input_fingerprint=meta.get('content_input_fingerprint') or job['input_fingerprint'])
    return db.change(conn, 'background_jobs', job['id'], input_fingerprint=ocr_input_fingerprint(conn, revision),
                     result_json=meta, total=_planned_total(conn, revision))


def _new_ocr(conn, application_id, user, revision, content_fingerprint, files_fingerprint, total, force=False):
    return db.add(conn, 'background_jobs', application_id=application_id, owner_id=user['id'], kind='OCR',
                  state='PAUSED' if _control(conn, user['id']).get('paused') else 'QUEUED', stage='排队等待OCR', progress=0, total=total,
                  input_revision_id=revision['id'], input_fingerprint=files_fingerprint,
                  result_json={'auto_apply': not force and _pristine(conn, revision),
                               'auto_analyze': True,
                               'policy_version': OCR_POLICY_VERSION,
                               'fingerprint_kind': OCR_FINGERPRINT_KIND,
                               'content_input_fingerprint': content_fingerprint}, force=bool(force), attempts=0)


def _matching_ocr(job, revision, content_fingerprint, files_fingerprint):
    if job['input_revision_id'] != revision['id'] or not _policy_current(job):
        return False
    meta = job.get('result_json') or {}
    if meta.get('fingerprint_kind') == OCR_FINGERPRINT_KIND:
        return job['input_fingerprint'] == files_fingerprint
    if meta.get('ocr_fingerprint'):
        return meta['ocr_fingerprint'] == files_fingerprint
    return content_fingerprint in (job['input_fingerprint'], meta.get('output_fingerprint'))


def _new_ai(conn, application_id, user, revision, fingerprint, files_fingerprint, total, dependency, force=False, automatic=False):
    return db.add(conn, 'background_jobs', application_id=application_id, owner_id=user['id'], kind='AI',
                  state='PAUSED' if _control(conn, user['id']).get('paused') else 'QUEUED', stage=WAITING_FOR_OCR if dependency['state'] in ACTIVE else '排队等待AI分析（OCR已完成）',
                  progress=0, total=total, input_revision_id=revision['id'], input_fingerprint=fingerprint,
                  result_json={'auto_apply': not force and _pristine(conn, revision), 'automatic': automatic,
                               'policy_version': OCR_POLICY_VERSION, 'wait_for_ocr_job_id': dependency['id'],
                               'ocr_input_fingerprint': files_fingerprint}, force=bool(force), attempts=0)


def _schedule_automatic_ai(conn, ocr_job):
    """Persist the follow-up in the OCR completion transaction, exactly once."""
    result = ocr_job.get('result_json') or {}
    if not result.get('auto_analyze') or ocr_job['state'] != 'SUCCEEDED' or result.get('status') != 'SUCCEEDED' or not result.get('processed_page_ids'):
        return None  # Empty/failed OCR never calls the model or retries cloud OCR.
    if not _snapshot_valid(conn, ocr_job):
        return None
    from .materials import revision_fingerprint, ocr_input_fingerprint
    user = db.one(conn, 'users', ocr_job['owner_id'])
    if not user or user['status'] != 'ACTIVE':
        return None
    revision = db.one(conn, 'application_revisions', ocr_job['input_revision_id'])
    fingerprint = revision_fingerprint(conn, revision)
    current_files = {link['file_id'] for link in db.rows(conn, 'revision_files', revision_id=revision['id'])}
    # Automatic queue recovery is not a request to rerun an old direct analysis.
    # Preserve completed historical suggestions when their material is unchanged.
    for analysis in db.rows(conn, 'ai_analyses', application_id=ocr_job['application_id'], status='SUCCEEDED'):
        inputs = {item['file_id'] for item in db.rows(conn, 'analysis_inputs', analysis_id=analysis['id'])}
        if current_files <= inputs or (analysis['input_revision_id'] == revision['id'] and analysis['input_fingerprint'] == fingerprint):
            return None
    previous = sorted(db.rows(conn, 'background_jobs', application_id=ocr_job['application_id'], kind='AI'), key=lambda job: job['created_at'])
    for candidate in reversed(previous):
        if not _policy_current(candidate) or candidate['input_revision_id'] != revision['id']:
            continue
        output = candidate.get('result_json') or {}
        if candidate['state'] in ACTIVE and candidate['input_fingerprint'] == fingerprint:
            return candidate
        if candidate['state'] == 'SUCCEEDED' and fingerprint in (candidate['input_fingerprint'], output.get('output_fingerprint')):
            return candidate
        if output.get('wait_for_ocr_job_id') == ocr_job['id'] and candidate['attempts'] > 0:
            # A failed model attempt is retried explicitly, never repeatedly by
            # queue recovery or a repeated upload-confirm request.
            return candidate
        if candidate['state'] == 'QUEUED' and output.get('wait_for_ocr_job_id') == ocr_job['id']:
            db.change(conn, 'background_jobs', candidate['id'], state='STALE', stage='内容已修改，改为当前稿自动分析', finished_at=db.now())
    return _new_ai(conn, ocr_job['application_id'], user, revision, fingerprint,
                   ocr_input_fingerprint(conn, revision), _planned_total(conn, revision), ocr_job, automatic=True)


def _dependency_for_enqueue(conn, application_id, revision, content_fingerprint, files_fingerprint):
    all_ocr = sorted(db.rows(conn, 'background_jobs', application_id=application_id, kind='OCR'), key=lambda j: j['created_at'])
    all_ocr = [_normalize_queued_ocr(conn, j) for j in all_ocr]
    matching = [j for j in all_ocr if _matching_ocr(j, revision, content_fingerprint, files_fingerprint)]
    active = [j for j in matching if j['state'] in ACTIVE]
    if active:
        return active[-1]
    if matching and matching[-1]['state'] == 'SUCCEEDED':
        return matching[-1]
    # AI never turns a failed/stale OCR dependency into an implicit cloud retry.
    if matching and matching[-1]['state'] in ('FAILED', 'STALE') and not (matching[-1].get('result_json') or {}).get('policy_expired'):
        raise HTTPException(409, '该小项OCR未成功，请先重试OCR；成功后再开始AI分析')
    if any(j['state'] in ACTIVE for j in all_ocr):
        raise HTTPException(409, '该小项OCR正在处理其他材料版本，请等待其结束后重试OCR，不会中断原任务')
    if all_ocr and all_ocr[-1]['state'] in ('FAILED', 'STALE') and not (all_ocr[-1].get('result_json') or {}).get('policy_expired'):
        raise HTTPException(409, '该小项此前OCR未成功，请先明确重试OCR，不会自动重新调用识别服务')
    return None


def enqueue(application_id, user, kind='AI', force=False):
    from .materials import ocr_input_fingerprint, revision_fingerprint
    if kind not in ('OCR', 'AI'):
        raise HTTPException(400, '任务类型无效')
    with db.write() as conn:
        context, revision = _context(conn, application_id, user)
        content_fingerprint = revision_fingerprint(conn, revision)
        files_fingerprint = ocr_input_fingerprint(conn, revision)
        fingerprint = files_fingerprint if kind == 'OCR' else content_fingerprint
        previous = sorted(db.rows(conn, 'background_jobs', application_id=application_id, kind=kind), key=lambda j: j['created_at'])
        if kind == 'OCR':
            previous = [_normalize_queued_ocr(conn, j) for j in previous]
        for job in reversed(previous):
            if _policy_current(job) and job['input_revision_id'] == revision['id'] and job['input_fingerprint'] == fingerprint and job['state'] in ACTIVE:
                if kind == 'AI' and job['state'] == 'QUEUED':
                    job = _ensure_dependency(conn, job)
                return job_view(job)
        if not force:
            for job in reversed(previous):
                output = job.get('result_json') or {}
                if _policy_current(job) and job['state'] == 'SUCCEEDED' and job['input_revision_id'] == revision['id'] and fingerprint in (job['input_fingerprint'], output.get('output_fingerprint')):
                    return job_view(job)
            prior_analyses = sorted((analysis for analysis in db.rows(conn, 'ai_analyses', application_id=application_id, status='SUCCEEDED')
                                     if (analysis.get('result_json') or {}).get('policy_version') == OCR_POLICY_VERSION), key=lambda a: a['created_at']) if kind == 'AI' else []
            current_analysis = db.one(conn, 'ai_analyses', revision.get('ai_analysis_id'))
            if current_analysis and (current_analysis.get('result_json') or {}).get('policy_version') != OCR_POLICY_VERSION:
                current_analysis = None
            analysis_id = current_analysis['id'] if current_analysis else (prior_analyses[-1]['id'] if prior_analyses else None)
            if kind == 'AI' and analysis_id:
                analysis_inputs = db.rows(conn, 'analysis_inputs', analysis_id=analysis_id)
                old_files = {i['file_id'] for i in analysis_inputs}
                current_files = {i['file_id'] for i in db.rows(conn, 'revision_files', revision_id=revision['id'])}
                if current_files <= old_files:
                    raise HTTPException(409, '该小项已经分析完成；可单独重新分析获取建议')
        total = _planned_total(conn, revision)
        if kind == 'OCR':
            return job_view(_new_ocr(conn, application_id, user, revision, content_fingerprint, files_fingerprint, total, force))
        dependency = _dependency_for_enqueue(conn, application_id, revision, content_fingerprint, files_fingerprint)
        if dependency is None:
            dependency = _new_ocr(conn, application_id, user, revision, content_fingerprint, files_fingerprint, total)
        return job_view(_new_ai(conn, application_id, user, revision, fingerprint, files_fingerprint, total, dependency, force))


@router.post('/applications/{application_id}/jobs')
def create_job(application_id: str, payload: dict, user=Depends(current_user)):
    if not isinstance(payload.get('force', False), bool):
        raise HTTPException(400, '重新分析标记须为布尔值')
    job = enqueue(application_id, user, str(payload.get('kind', 'AI')).upper(), bool(payload.get('force', False)))
    return {'job': job}


@router.get('/analysis-jobs')
def list_jobs(active_only: bool = False, user=Depends(current_user)):
    with db.read() as conn:
        result = [job_view(j) for j in db.rows(conn, 'background_jobs', owner_id=user['id']) if not active_only or j['state'] in ACTIVE]
        control = _control_view(_control(conn, user['id']))
    return {'jobs': sorted(result, key=lambda j: j['created_at'], reverse=True)[:200], 'control': control}


@router.get('/analysis-jobs/{job_id}')
def get_job(job_id: str, user=Depends(current_user)):
    with db.read() as conn:
        job = db.one(conn, 'background_jobs', job_id)
        if not job or job['owner_id'] != user['id']:
            raise HTTPException(404, '任务不存在')
        return job_view(job)


@router.post('/analysis-jobs/bulk')
def bulk_jobs(payload: dict | None = None, user=Depends(current_user)):
    payload = payload or {}
    kind = str(payload.get('kind', 'AI')).upper()
    if kind not in ('OCR', 'AI'):
        raise HTTPException(400, '任务类型无效')
    ids = payload.get('application_ids')
    with db.read() as conn:
        if ids is None:
            ids = []
            for application in db.rows(conn, 'applications'):
                if application.get('archived_at'):
                    continue
                from .security import class_context
                context = class_context(conn, application['enrollment_id'])
                item = db.one(conn, 'category_items', application_id=application['id'])
                if context['user']['id'] == user['id'] and context['year']['id'] == active_year(conn)['id'] and item and item['confirmed_at'] and not item['deleted_at']:
                    if not payload.get('submission_id') or item['submission_id'] == payload['submission_id']:
                        ids.append(application['id'])
    if not isinstance(ids, list) or len(ids) > 500 or any(not isinstance(i, str) for i in ids):
        raise HTTPException(400, '每次最多处理500个小项')
    result, skipped = [], []
    for application_id in dict.fromkeys(ids):
        try:
            result.append(enqueue(application_id, user, kind, False))
        except HTTPException as error:
            skipped.append({'id': application_id, 'detail': error.detail})
    return {'jobs': result, 'skipped': skipped}


def _snapshot_valid(conn, job):
    from .materials import ocr_input_fingerprint, revision_fingerprint
    if not _policy_current(job):
        return False
    application = db.one(conn, 'applications', job['application_id'])
    if not application or application.get('archived_at') or application['working_revision_id'] != job['input_revision_id']:
        return False
    current = db.one(conn, 'submission_rounds', application['current_round_id']) if application.get('current_round_id') else None
    if current and current['state'] in ('PENDING_PEER', 'PENDING_COUNSELOR', 'APPROVED'):
        return False
    from .security import class_context
    if class_context(conn, application['enrollment_id'])['year']['id'] != active_year(conn)['id']:
        return False
    item = db.one(conn, 'category_items', application_id=application['id'])
    if not item or not item['confirmed_at'] or item['deleted_at']:
        return False
    revision = db.one(conn, 'application_revisions', job['input_revision_id'])
    if not revision:
        return False
    if job['kind'] == 'OCR' and (job.get('result_json') or {}).get('fingerprint_kind') == OCR_FINGERPRINT_KIND:
        return ocr_input_fingerprint(conn, revision) == job['input_fingerprint']
    return revision_fingerprint(conn, revision) == job['input_fingerprint']


def _fail_dependency(conn, job, message, dependency=None):
    meta = {**(job.get('result_json') or {}), 'dependency_failed': True,
            'ocr_dependency_state': dependency['state'] if dependency else 'MISSING'}
    return db.change(conn, 'background_jobs', job['id'], state='FAILED', stage=message,
                     result_json=meta, error_message=message, finished_at=db.now(), worker_token=None)


def _ensure_dependency(conn, job):
    """Attach persisted legacy AI jobs without superseding a running OCR."""
    if job['kind'] != 'AI':
        return job
    if not _policy_current(job):
        return _mark_policy_expired(conn, job)
    from .materials import ocr_input_fingerprint, revision_fingerprint
    revision = db.one(conn, 'application_revisions', job['input_revision_id'])
    if not revision:
        return job
    meta = dict(job.get('result_json') or {})
    dependency = db.one(conn, 'background_jobs', meta.get('wait_for_ocr_job_id'))
    if dependency and not _policy_current(dependency):
        dependency = None
    current_full = revision_fingerprint(conn, revision)
    files_fingerprint = ocr_input_fingerprint(conn, revision)
    # A newer explicit OCR request for the same sources must also finish before
    # this queued AI job runs. Multiple workers never race it for the same item.
    active = [_normalize_queued_ocr(conn, j) for j in db.rows(conn, 'background_jobs', application_id=job['application_id'], kind='OCR') if j['state'] in ACTIVE]
    active = sorted((j for j in active if _matching_ocr(j, revision, current_full, files_fingerprint)), key=lambda j: j['created_at'])
    if active:
        dependency = active[-1]
    elif not dependency:
        matches = [j for j in db.rows(conn, 'background_jobs', application_id=job['application_id'], kind='OCR')
                   if _matching_ocr(j, revision, current_full, files_fingerprint)]
        dependency = max(matches, key=lambda j: j['created_at']) if matches else None
    if not dependency:
        if not _snapshot_valid(conn, job):
            return db.change(conn, 'background_jobs', job['id'], state='STALE', stage='内容已修改，原AI请求失效', finished_at=db.now())
        prior_ocr = sorted(db.rows(conn, 'background_jobs', application_id=job['application_id'], kind='OCR'), key=lambda j: j['created_at'])
        if prior_ocr and (prior_ocr[-1]['state'] in ACTIVE or (prior_ocr[-1]['state'] in ('FAILED', 'STALE') and not (prior_ocr[-1].get('result_json') or {}).get('policy_expired'))):
            return _fail_dependency(conn, job, '现有OCR未成功或材料版本不同，AI未开始；请先重试OCR', prior_ocr[-1])
        owner = db.one(conn, 'users', job['owner_id'])
        if not owner or owner['status'] != 'ACTIVE':
            return db.change(conn, 'background_jobs', job['id'], state='STALE', stage='账号已停用', finished_at=db.now())
        dependency = _new_ocr(conn, job['application_id'], owner, revision, current_full, files_fingerprint, _planned_total(conn, revision))
    if dependency:
        updated = {**meta, 'wait_for_ocr_job_id': dependency['id'],
                   'ocr_input_fingerprint': meta.get('ocr_input_fingerprint') or files_fingerprint}
        stage = WAITING_FOR_OCR if dependency['state'] in ACTIVE else job['stage']
        if updated != meta or stage != job['stage']:
            job = db.change(conn, 'background_jobs', job['id'], result_json=updated, stage=stage)
    return job


def _dependency_ready(conn, job):
    if job['kind'] != 'AI':
        return True
    meta = job.get('result_json') or {}
    dependency_id = meta.get('wait_for_ocr_job_id')
    if not dependency_id:
        _fail_dependency(conn, job, '缺少可复用的OCR任务，AI未开始；请先重试OCR')
        return False
    dependency = db.one(conn, 'background_jobs', dependency_id)
    if not dependency or dependency['kind'] != 'OCR' or dependency['application_id'] != job['application_id']:
        _fail_dependency(conn, job, '原OCR任务不存在，AI未开始；请先重试OCR', dependency)
        return False
    if dependency['state'] in ACTIVE:
        if job['stage'] != WAITING_FOR_OCR:
            db.change(conn, 'background_jobs', job['id'], stage=WAITING_FOR_OCR)
        return False
    if dependency['state'] != 'SUCCEEDED':
        _fail_dependency(conn, job, 'OCR未成功，AI未开始；请先重试OCR后重试AI', dependency)
        return False
    revision = db.one(conn, 'application_revisions', job['input_revision_id'])
    from .materials import ocr_input_fingerprint
    if not revision or not _matching_ocr(dependency, revision, job['input_fingerprint'], ocr_input_fingerprint(conn, revision)):
        _fail_dependency(conn, job, 'OCR材料版本已变更，AI未开始；请先重试OCR', dependency)
        return False
    return True


def _claim_next(conn, worker_token, kind=None):
    running = db.rows(conn, 'background_jobs', state='RUNNING')
    pending = sorted(db.rows(conn, 'background_jobs', state='QUEUED'), key=lambda j: (j['created_at'], j['id']))
    for candidate in pending:
        if kind and candidate['kind'] != kind:
            continue
        if _control(conn, candidate['owner_id']).get('paused'):
            db.change(conn, 'background_jobs', candidate['id'], state='PAUSED', stage='已暂停，点击继续处理')
            continue
        active_kind = [other for other in running if other['kind'] == candidate['kind']]
        if len(active_kind) >= GLOBAL_CONCURRENCY[candidate['kind']] or sum(other['owner_id'] == candidate['owner_id'] for other in active_kind) >= OWNER_CONCURRENCY[candidate['kind']]:
            continue
        from .materials import synchronous_analysis_active
        if synchronous_analysis_active(conn, candidate['owner_id']):
            continue
        job = _normalize_queued_ocr(conn, candidate)
        job = _ensure_dependency(conn, job)
        if job['state'] != 'QUEUED':
            continue
        if not _dependency_ready(conn, job):
            continue
        if any(other['application_id'] == job['application_id'] and other['kind'] == job['kind'] for other in running):
            continue
        user = db.one(conn, 'users', job['owner_id'])
        if not user or user['status'] != 'ACTIVE' or not _snapshot_valid(conn, job):
            db.change(conn, 'background_jobs', job['id'], state='STALE', stage='材料或学年已变更', finished_at=db.now())
            continue
        metadata = {**(job.get('result_json') or {}), 'active_file_ids': []} if job['kind'] == 'OCR' else job.get('result_json') or {}
        return db.change(conn, 'background_jobs', job['id'], state='RUNNING', result_json=metadata,
                         stage=OCR_START_STAGE if job['kind'] == 'OCR' else ('AI准备（OCR已完成）' if (job.get('result_json') or {}).get('wait_for_ocr_job_id') else job['stage']),
                         started_at=db.now(), heartbeat_at=db.now(), worker_token=worker_token,
                         attempts=job['attempts'] + 1)
    return None


def _advance_waiting_ai(conn, ocr_job, before_fingerprint, after_fingerprint):
    """Called inside the crop transaction: no f0/f1 enqueue race can exist."""
    for queued in db.rows(conn, 'background_jobs', application_id=ocr_job['application_id'], kind='AI'):
        if queued['state'] not in ('QUEUED', 'PAUSED'):
            continue
        if not _policy_current(queued) or queued['input_revision_id'] != ocr_job['input_revision_id'] or queued['input_fingerprint'] != before_fingerprint:
            continue
        meta = dict(queued.get('result_json') or {})
        if meta.get('wait_for_ocr_job_id') not in (None, ocr_job['id']):
            continue
        meta['wait_for_ocr_job_id'] = ocr_job['id']
        meta['ocr_input_fingerprint'] = ocr_job['input_fingerprint']
        db.change(conn, 'background_jobs', queued['id'], input_fingerprint=after_fingerprint,
                  result_json=meta, stage='已暂停，点击继续处理' if queued['state'] == 'PAUSED' else WAITING_FOR_OCR)


def _ocr_result_for_ai(conn, job):
    dependency_id = (job.get('result_json') or {}).get('wait_for_ocr_job_id')
    if not dependency_id:
        return None
    dependency = db.one(conn, 'background_jobs', dependency_id)
    if not dependency or dependency['state'] != 'SUCCEEDED':
        raise HTTPException(409, 'OCR尚未成功，AI不会重新调用OCR')
    result = dict(dependency.get('result_json') or {})
    # OCR owns source bytes; AI owns the latest user-requested content snapshot.
    # A title/score correction does not invalidate already recognised text.
    result.update(input_revision_id=job['input_revision_id'], input_fingerprint=job['input_fingerprint'])
    from .materials import ocr_input_fingerprint
    revision = db.one(conn, 'application_revisions', job['input_revision_id'])
    result['ocr_fingerprint'] = ocr_input_fingerprint(conn, revision)
    return result


def _is_database_busy(error):
    return isinstance(error, OperationalError) and any(word in str(error.orig).lower() for word in ('locked', 'busy'))


def process_job(job_id, worker_token=None, stop_event=None):
    recovery = {}
    try:
        _process_job(job_id, worker_token, stop_event, recovery)
    except OperationalError as error:
        if not _is_database_busy(error):
            raise
        # Preserve any already-returned provider result before asking for resume.
        # Retrying a database transaction must never repeat the cloud request.
        for attempt in range(30):
            try:
                with db.write() as conn:
                    current = db.one(conn, 'background_jobs', job_id)
                    if not current or current['state'] not in ('RUNNING', 'QUEUED'):
                        return
                    if current['state'] == 'RUNNING' and current.get('worker_token') != (worker_token or recovery.get('worker_token')):
                        return
                    pause_owner(conn, current['owner_id'], 'DATABASE_BUSY')
                    metadata = dict(current.get('result_json') or {})
                    if recovery.get('result') is not None:
                        metadata.update(recovery['result'], completed_result=True)
                    metadata.update(error_code='DatabaseBusy', error_source='DATABASE',
                                    diagnostics={'exception_type': type(error).__name__})
                    metadata.pop('active_file_ids', None)
                    db.change(conn, 'background_jobs', job_id, state='PAUSED', stage='数据库繁忙，结果已保留；请继续处理',
                              worker_token=None, result_json=metadata, error_message=None)
                return
            except OperationalError as retry_error:
                if not _is_database_busy(retry_error):
                    raise
                time.sleep(min(.1 * (attempt + 1), 1))
        logging.getLogger(__name__).error('Background result awaits database recovery; no cloud request was repeated')


def _process_job(job_id, worker_token=None, stop_event=None, recovery=None):
    """Process one already-claimed job; callable directly by deterministic tests."""
    from . import materials
    with db.write() as conn:
        job = db.one(conn, 'background_jobs', job_id)
        if not job or job['state'] not in ACTIVE or job['state'] == 'PAUSED':
            return
        job = _normalize_queued_ocr(conn, job)
        job = _ensure_dependency(conn, job)
        if job['state'] not in ACTIVE:
            return
        # Check a changed AI draft before deferring to OCR. It must never be
        # brought back to life after the user has edited or deleted it.
        if job['kind'] == 'AI' and not _snapshot_valid(conn, job):
            db.change(conn, 'background_jobs', job_id, state='STALE', stage='内容已修改，原AI请求失效', finished_at=db.now())
            return
        if not _dependency_ready(conn, job):
            if job['state'] == 'RUNNING':
                latest = db.one(conn, 'background_jobs', job_id)
                if latest['state'] == 'RUNNING':
                    db.change(conn, 'background_jobs', job_id, state='QUEUED', worker_token=None)
            return
        user = db.one(conn, 'users', job['owner_id']) if job else None
        valid = bool(job and user and user['status'] == 'ACTIVE' and _snapshot_valid(conn, job))
        if not valid:
            db.change(conn, 'background_jobs', job_id, state='STALE', stage='材料或学年已变更', finished_at=db.now())
            return
        if job['state'] == 'RUNNING':
            if not worker_token or job.get('worker_token') != worker_token:
                return
        else:
            worker_token = worker_token or f'{os.getpid()}:direct:{db.uid()}'
            job = db.change(conn, 'background_jobs', job_id, state='RUNNING', started_at=db.now(),
                            stage=OCR_START_STAGE if job['kind'] == 'OCR' else job['stage'],
                            heartbeat_at=db.now(), worker_token=worker_token, attempts=job['attempts'] + 1)
        ocr_result = _ocr_result_for_ai(conn, job) if job['kind'] == 'AI' else None
        if recovery is not None:
            recovery['worker_token'] = worker_token

    def progress(stage, done, total):
        with db.write() as conn:
            if not _owns_job(conn, job_id, worker_token):
                raise JobOwnershipLost()
            if _job_paused(conn, db.one(conn, 'background_jobs', job_id)):
                raise JobPauseRequested()
            db.change(conn, 'background_jobs', job_id, stage=str(stage)[:150], progress=int(done), total=max(int(total), 1), heartbeat_at=db.now())

    try:
        raise_if_pause_requested(job_id, worker_token)
        if stop_event and stop_event.is_set():
            raise materials.RecognitionInterrupted()
        manual_dependency = bool(job['kind'] == 'AI' and ocr_result is not None and ocr_result.get('status') == 'MANUAL_REQUIRED')
        if (job.get('result_json') or {}).get('completed_result'):
            result = dict(job['result_json'])
            result.pop('completed_result', None)
        elif job['kind'] == 'OCR':
            with materials.ocr_job_scope(job_id, worker_token, stop_event):
                result = materials.run_ocr(job['application_id'], user, progress)
        elif manual_dependency:
            # No usable text is an explicit manual workflow, not an invitation
            # to rescan sources or send an empty prompt to the model.
            result = {**ocr_result, 'status': 'MANUAL_REQUIRED', 'automatically_applied': False,
                      'message': ocr_result.get('message') or '材料缺少可用文字，请手动填写、计分和框选证明；AI未调用'}
        elif ocr_result is not None:
            with materials.ocr_job_scope(job_id, worker_token, stop_event):
                result = materials.run_ai(job['application_id'], user, progress, ocr_result=ocr_result)
        else:
            with materials.ocr_job_scope(job_id, worker_token, stop_event):
                result = materials.run_ai(job['application_id'], user, progress)
        if recovery is not None:
            recovery['result'] = result
        if job['kind'] == 'AI' and ocr_result is not None:
            # Model adapters must not erase partial-recognition notices.
            for field in ('manual_required_files', 'skipped_files', 'partial', 'planned_tasks', 'completed_tasks',
                          'native_text_page_count', 'file_results', 'processed_page_ids', 'per_file_warnings', 'stopped', 'stop_reason',
                          'stop_code', 'stop_source', 'stop_message', 'stop_details'):
                if field in ocr_result:
                    result[field] = ocr_result[field]
            result['warnings'] = list(dict.fromkeys([*(ocr_result.get('warnings') or []), *(result.get('warnings') or [])]))
        with db.read() as conn:
            if not _owns_job(conn, job_id, worker_token):
                return
            valid = _snapshot_valid(conn, job)
            paused = _job_paused(conn, db.one(conn, 'background_jobs', job_id))
        if paused:
            with db.write() as conn:
                if not _owns_job(conn, job_id, worker_token):
                    return
                meta = {**(job.get('result_json') or {}), **result, 'completed_result': True}
                meta.pop('active_file_ids', None)
                db.change(conn, 'background_jobs', job_id, state='PAUSED', stage='已暂停，已返回结果保留',
                          result_json=meta, worker_token=None, error_message=None)
            return
        with db.write() as conn:
            if not _owns_job(conn, job_id, worker_token):
                return
            prepared = {**(job.get('result_json') or {}), **result, 'completed_result': True}
            db.change(conn, 'background_jobs', job_id, result_json=prepared)
        manual = result.get('status') == 'MANUAL_REQUIRED' and (job['kind'] == 'OCR' or manual_dependency)
        succeeded = result.get('status') == 'SUCCEEDED' or manual
        output = {**(job.get('result_json') or {}), **result}
        if not valid:
            state, stage = 'STALE', '内容已修改，结果仅供参考'
        elif not succeeded:
            state, stage = 'FAILED', result.get('message') or '分析未完成，可重试或手工填写'
        else:
            state, stage = 'SUCCEEDED', 'OCR完成' if job['kind'] == 'OCR' else 'AI分析完毕'
            if manual:
                stage = '需手动填写和框选（AI未调用）' if job['kind'] == 'AI' else '材料需手动处理，请填写和框选'
            elif output.get('partial') or output.get('manual_required_files'):
                stage = '部分材料处理完成，请补充人工核对' if job['kind'] == 'OCR' else 'AI分析完成，部分材料需手动补充'
            if job['kind'] == 'OCR' and not manual and output.get('native_text_page_count', 0) and output.get('planned_tasks') == 0:
                stage = '本地PDF文字提取完成（未调用云OCR）' + ('，印章等信息请手动核对' if output.get('partial') or output.get('manual_required_files') else '')
            if job['kind'] == 'OCR' and not manual:
                def on_applied(conn, before_fp, after_fp):
                    if not _owns_job(conn, job_id, worker_token):
                        raise JobOwnershipLost()
                    if _job_paused(conn, db.one(conn, 'background_jobs', job_id)):
                        raise JobPauseRequested()
                    _advance_waiting_ai(conn, job, before_fp, after_fp)
                material_mode = (job.get('result_json') or {}).get('fingerprint_kind') == OCR_FINGERPRINT_KIND
                with materials.ocr_job_scope(job_id, worker_token, stop_event):
                    applied = materials.apply_ocr_evidence(job['application_id'], user, result,
                                                     expected_fingerprint=None if material_mode else job['input_fingerprint'],
                                                     expected_ocr_fingerprint=job['input_fingerprint'] if material_mode else None,
                                                     on_applied=on_applied)
                with db.write() as conn:
                    revision = db.one(conn, 'application_revisions', job['input_revision_id'])
                    new_fingerprint = (applied or {}).get('fingerprint') or materials.revision_fingerprint(conn, revision)
                    output['output_fingerprint'] = new_fingerprint
            elif not manual and output.get('auto_apply') and not job['force']:
                analysis_id = result.get('analysis_id') or (result.get('analysis') or {}).get('id')
                with materials.ocr_job_scope(job_id, worker_token, stop_event):
                    materials.apply_analysis(job['application_id'], {'analysis_id': analysis_id,
                                            '_background_job_id': job_id, '_worker_token': worker_token}, user)
                output['automatically_applied'] = True
                if (output.get('score_resolution') or {}).get('status') == 'NEEDS_REVIEW':
                    stage = '条目已提取，分值待核定'
                with db.read() as conn:
                    revision = db.one(conn, 'application_revisions', job['input_revision_id'])
                    output['output_fingerprint'] = materials.revision_fingerprint(conn, revision)
            else:
                output['automatically_applied'] = False
                if not manual:
                    stage = '分析完毕，建议待确认（保留人工内容）' + ('，部分材料需手动补充' if output.get('partial') or output.get('manual_required_files') else '')
        with db.write() as conn:
            if not _owns_job(conn, job_id, worker_token):
                return
            latest = db.one(conn, 'background_jobs', job_id)
            done, total = latest['progress'], latest['total']
            if job['kind'] == 'OCR' and 'planned_tasks' in output:
                # A completed queue item may contain skipped work or a quota
                # stop. Preserve the real plan rather than displaying 100%.
                total = max(int(output['planned_tasks']), 0)
                done = max(0, int(output.get('completed_tasks', done)))
            elif state == 'SUCCEEDED' and not manual and not output.get('partial'):
                done = total
            output.pop('active_file_ids', None)
            finished = db.change(conn, 'background_jobs', job_id, state=state, stage=stage, result_json=output,
                      progress=done, total=total,
                      error_message=None if succeeded else str(result.get('message') or stage)[:500], finished_at=db.now())
            if job['kind'] == 'OCR' and state == 'SUCCEEDED':
                _schedule_automatic_ai(conn, finished)
    except JobOwnershipLost:
        return
    except JobPauseRequested:
        with db.write() as conn:
            if not _owns_job(conn, job_id, worker_token):
                return
            latest = db.one(conn, 'background_jobs', job_id)
            metadata = {key: value for key, value in (latest.get('result_json') or {}).items() if key != 'active_file_ids'}
            metadata['pause_requested'] = True
            db.change(conn, 'background_jobs', job_id, state='PAUSED', stage='已暂停，点击继续处理',
                      worker_token=None, result_json=metadata, error_message=None,
                      attempts=max(0, latest['attempts'] - 1))
    except materials.RecognitionInterrupted:
        with db.write() as conn:
            if not _owns_job(conn, job_id, worker_token):
                return
            latest = db.one(conn, 'background_jobs', job_id)
            metadata = {key: value for key, value in (latest.get('result_json') or {}).items() if key != 'active_file_ids'}
            db.change(conn, 'background_jobs', job_id, state='QUEUED', stage='服务正在停止，重启后从已缓存结果继续',
                      worker_token=None, result_json=metadata, attempts=max(0, latest['attempts'] - 1))
    except HTTPException as error:
        with db.write() as conn:
            if not _owns_job(conn, job_id, worker_token):
                return
            db.change(conn, 'background_jobs', job_id, state='STALE' if error.status_code == 409 else 'FAILED',
                      stage='内容已变更' if error.status_code == 409 else '需要人工核对', error_message=str(error.detail)[:500], finished_at=db.now())
    except OperationalError:
        raise
    except Exception as error:
        # Do not write exception reprs: provider exceptions can contain credentials/paths.
        with db.write() as conn:
            if not _owns_job(conn, job_id, worker_token):
                return
            latest = db.one(conn, 'background_jobs', job_id)
            frames = traceback.extract_tb(error.__traceback__)[-6:]
            diagnostic = {'exception_type': type(error).__name__,
                          'locations': [{'file': Path(frame.filename).name, 'function': frame.name, 'line': frame.lineno}
                                        for frame in frames]}
            db.change(conn, 'background_jobs', job_id, state='FAILED', stage='任务异常，可重试或手工填写',
                      error_message='任务未能完成，已保留原文件和填写内容', finished_at=db.now(),
                      result_json={**(latest.get('result_json') or {}), 'error_code': 'TaskError', 'diagnostics': diagnostic})


def _process_alive(token):
    try:
        pid = int(str(token).split(':', 1)[0])
        if pid <= 0:
            return False
        if pid == os.getpid():
            return True
        if os.name == 'nt':
            # On Windows signal 0 is CTRL_C_EVENT, not a harmless POSIX probe.
            # Never use os.kill for liveness: it can interrupt live OCR workers.
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
            kernel.GetExitCodeProcess.restype = wintypes.BOOL
            kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
            handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return ctypes.get_last_error() == 5  # Access denied is not proof of death.
            try:
                code = wintypes.DWORD()
                if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return ctypes.get_last_error() == 5
                return code.value == 259
            finally:
                kernel.CloseHandle(handle)
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except (ValueError, TypeError, OSError):
        return False


class JobWorker:
    def __init__(self):
        self.stop_event = threading.Event()
        self.token = f'{os.getpid()}:{db.uid()}'
        self.engine = db.engine
        self.executors = {kind: ThreadPoolExecutor(max_workers=limit, thread_name_prefix=f'{kind.lower()}-job')
                          for kind, limit in GLOBAL_CONCURRENCY.items()}
        self.futures = {kind: set() for kind in GLOBAL_CONCURRENCY}
        self.thread = threading.Thread(target=self.run, name='ocr-ai-worker', daemon=True)

    def start(self):
        with db.write() as conn:
            for job in db.rows(conn, 'background_jobs', state='RUNNING'):
                # Long PDFium work/file-slot waits may have an old heartbeat.
                # Never steal a live process's leases based on time alone.
                if not _process_alive(job['worker_token']):
                    retry = job['attempts'] < 2
                    db.change(conn, 'background_jobs', job['id'], state='QUEUED' if retry else 'FAILED',
                              stage='恢复中断的任务' if retry else '服务中断，请手动重试', worker_token=None,
                              result_json={key: value for key, value in (job.get('result_json') or {}).items() if key != 'active_file_ids'})
            for job in db.rows(conn, 'background_jobs', kind='OCR', state='QUEUED'):
                _normalize_queued_ocr(conn, job)
            for job in db.rows(conn, 'background_jobs', kind='AI', state='QUEUED'):
                _ensure_dependency(conn, job)
            # Finish a durable hand-off from a previous interrupted process.
            for job in db.rows(conn, 'background_jobs', kind='OCR', state='SUCCEEDED'):
                _schedule_automatic_ai(conn, job)
        _workers.append(self)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=100)
        for executor in self.executors.values():
            executor.shutdown(wait=True)
        if self in _workers:
            _workers.remove(self)

    def run(self):
        next_presence_check = 0
        while not self.stop_event.is_set() and self.engine is db.engine:
            dispatched = False
            try:
                if time.monotonic() >= next_presence_check:
                    with db.write() as conn:
                        _expire_presence(conn)
                    next_presence_check = time.monotonic() + 1
                for kind in ('OCR', 'AI'):
                    self.futures[kind] = {future for future in self.futures[kind] if not future.done()}
                    while len(self.futures[kind]) < GLOBAL_CONCURRENCY[kind] and not self.stop_event.is_set():
                        with db.write() as conn:
                            job = _claim_next(conn, self.token, kind)
                        if not job:
                            break
                        self.futures[kind].add(self.executors[kind].submit(process_job, job['id'], self.token, self.stop_event))
                        dispatched = True
            except OperationalError as error:
                if not _is_database_busy(error):
                    raise
                # A transient writer must not kill the only queue dispatcher.
                self.stop_event.wait(.25)
                continue
            self.stop_event.wait(.04 if dispatched else .15)


def stop_all():
    for worker in list(_workers):
        worker.stop()
