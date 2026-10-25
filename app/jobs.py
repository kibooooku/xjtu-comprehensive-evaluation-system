"""Durable OCR/AI work queue, independent of the current browser page."""
from __future__ import annotations
import os
import threading
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException
from . import db
from .limits import OCR_POLICY_VERSION
from .migrations import active_year, assert_current_year, ensure_group
from .security import current_user

router = APIRouter(prefix='/api', tags=['background recognition'])
ACTIVE = {'QUEUED', 'RUNNING'}
OCR_FINGERPRINT_KIND = 'OCR_FILES_V1'
WAITING_FOR_OCR = '等待OCR完成（原OCR任务继续执行）'
OCR_START_STAGE = '检查材料与PDF文字层，可能需要一点时间'
_workers = []


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
    if payload.get('content_manually_edited') or payload.get('evidence_manually_edited'):
        return False
    if not revision.get('ai_analysis_id'):
        return not revision['title'].strip() and db.money(revision['requested_score']) == 0 and not payload
    analysis = db.one(conn, 'ai_analyses', revision['ai_analysis_id'])
    if not analysis or (analysis.get('result_json') or {}).get('policy_version') != OCR_POLICY_VERSION:
        return False
    suggestion = ((analysis or {}).get('result_json') or {}).get('suggestion') or {}
    clean = {k: v for k, v in payload.items() if k not in ('ai_explanation', 'ai_match_uncertain') and v not in ('', None)}
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
        return job
    application = db.one(conn, 'applications', job['application_id'])
    revision = db.one(conn, 'application_revisions', job['input_revision_id'])
    if not application or not revision or application['working_revision_id'] != revision['id']:
        return job
    from .materials import ocr_input_fingerprint
    meta.update(fingerprint_kind=OCR_FINGERPRINT_KIND, policy_version=OCR_POLICY_VERSION,
                previous_policy_version=meta.get('policy_version'),
                legacy_content_input_fingerprint=job['input_fingerprint'],
                content_input_fingerprint=meta.get('content_input_fingerprint') or job['input_fingerprint'])
    return db.change(conn, 'background_jobs', job['id'], input_fingerprint=ocr_input_fingerprint(conn, revision),
                     result_json=meta, total=_planned_total(conn, revision))


def _new_ocr(conn, application_id, user, revision, content_fingerprint, files_fingerprint, total, force=False):
    return db.add(conn, 'background_jobs', application_id=application_id, owner_id=user['id'], kind='OCR',
                  state='QUEUED', stage='排队等待OCR', progress=0, total=total,
                  input_revision_id=revision['id'], input_fingerprint=files_fingerprint,
                  result_json={'auto_apply': not force and _pristine(conn, revision),
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
        return job_view(db.add(conn, 'background_jobs', application_id=application_id, owner_id=user['id'], kind=kind,
                              state='QUEUED', stage=WAITING_FOR_OCR if dependency['state'] in ACTIVE else '排队等待AI分析（OCR已完成）',
                              progress=0, total=total, input_revision_id=revision['id'],
                              input_fingerprint=fingerprint, result_json={'auto_apply': not force and _pristine(conn, revision),
                                                                         'policy_version': OCR_POLICY_VERSION,
                                                                         'wait_for_ocr_job_id': dependency['id'],
                                                                         'ocr_input_fingerprint': files_fingerprint},
                              force=bool(force), attempts=0))


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
    return {'jobs': sorted(result, key=lambda j: j['created_at'], reverse=True)[:200]}


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


def _claim_next(conn, worker_token):
    pending = sorted(db.rows(conn, 'background_jobs', state='QUEUED'), key=lambda j: (j['created_at'], j['id']))
    for candidate in pending:
        job = _normalize_queued_ocr(conn, candidate)
        job = _ensure_dependency(conn, job)
        if job['state'] != 'QUEUED':
            continue
        if not _dependency_ready(conn, job):
            continue
        if job['kind'] == 'OCR' and any(other['id'] != job['id'] for other in db.rows(conn, 'background_jobs', application_id=job['application_id'], kind='OCR', state='RUNNING')):
            continue
        user = db.one(conn, 'users', job['owner_id'])
        if not user or user['status'] != 'ACTIVE' or not _snapshot_valid(conn, job):
            db.change(conn, 'background_jobs', job['id'], state='STALE', stage='材料或学年已变更', finished_at=db.now())
            continue
        return db.change(conn, 'background_jobs', job['id'], state='RUNNING',
                         stage=OCR_START_STAGE if job['kind'] == 'OCR' else ('AI准备（OCR已完成）' if (job.get('result_json') or {}).get('wait_for_ocr_job_id') else job['stage']),
                         started_at=db.now(), heartbeat_at=db.now(), worker_token=worker_token,
                         attempts=job['attempts'] + 1)
    return None


def _advance_waiting_ai(conn, ocr_job, before_fingerprint, after_fingerprint):
    """Called inside the crop transaction: no f0/f1 enqueue race can exist."""
    for queued in db.rows(conn, 'background_jobs', application_id=ocr_job['application_id'], kind='AI', state='QUEUED'):
        if not _policy_current(queued) or queued['input_revision_id'] != ocr_job['input_revision_id'] or queued['input_fingerprint'] != before_fingerprint:
            continue
        meta = dict(queued.get('result_json') or {})
        if meta.get('wait_for_ocr_job_id') not in (None, ocr_job['id']):
            continue
        meta['wait_for_ocr_job_id'] = ocr_job['id']
        meta['ocr_input_fingerprint'] = ocr_job['input_fingerprint']
        db.change(conn, 'background_jobs', queued['id'], input_fingerprint=after_fingerprint,
                  result_json=meta, stage=WAITING_FOR_OCR)


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


def process_job(job_id, worker_token=None):
    """Process one already-claimed job; callable directly by deterministic tests."""
    from . import materials
    with db.write() as conn:
        job = db.one(conn, 'background_jobs', job_id)
        if not job or job['state'] not in ACTIVE:
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

    def progress(stage, done, total):
        with db.write() as conn:
            db.change(conn, 'background_jobs', job_id, stage=str(stage)[:150], progress=int(done), total=max(int(total), 1), heartbeat_at=db.now())

    try:
        manual_dependency = bool(job['kind'] == 'AI' and ocr_result is not None and ocr_result.get('status') == 'MANUAL_REQUIRED')
        if job['kind'] == 'OCR':
            result = materials.run_ocr(job['application_id'], user, progress)
        elif manual_dependency:
            # No usable text is an explicit manual workflow, not an invitation
            # to rescan sources or send an empty prompt to the model.
            result = {**ocr_result, 'status': 'MANUAL_REQUIRED', 'automatically_applied': False,
                      'message': ocr_result.get('message') or '材料缺少可用文字，请手动填写、计分和框选证明；AI未调用'}
        elif ocr_result is not None:
            result = materials.run_ai(job['application_id'], user, progress, ocr_result=ocr_result)
        else:
            result = materials.run_ai(job['application_id'], user, progress)
        if job['kind'] == 'AI' and ocr_result is not None:
            # Model adapters must not erase partial-recognition notices.
            for field in ('manual_required_files', 'skipped_files', 'partial', 'planned_tasks', 'completed_tasks',
                          'native_text_page_count', 'file_results', 'processed_page_ids', 'per_file_warnings', 'stopped', 'stop_reason'):
                if field in ocr_result:
                    result[field] = ocr_result[field]
            result['warnings'] = list(dict.fromkeys([*(ocr_result.get('warnings') or []), *(result.get('warnings') or [])]))
        with db.read() as conn:
            valid = _snapshot_valid(conn, job)
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
                    _advance_waiting_ai(conn, job, before_fp, after_fp)
                material_mode = (job.get('result_json') or {}).get('fingerprint_kind') == OCR_FINGERPRINT_KIND
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
                materials.apply_analysis(job['application_id'], {'analysis_id': analysis_id}, user)
                output['automatically_applied'] = True
                with db.read() as conn:
                    revision = db.one(conn, 'application_revisions', job['input_revision_id'])
                    output['output_fingerprint'] = materials.revision_fingerprint(conn, revision)
            else:
                output['automatically_applied'] = False
                if not manual:
                    stage = '分析完毕，建议待确认（保留人工内容）' + ('，部分材料需手动补充' if output.get('partial') or output.get('manual_required_files') else '')
        with db.write() as conn:
            latest = db.one(conn, 'background_jobs', job_id)
            done, total = latest['progress'], latest['total']
            if job['kind'] == 'OCR' and 'planned_tasks' in output:
                # A completed queue item may contain skipped work or a quota
                # stop. Preserve the real plan rather than displaying 100%.
                total = max(int(output['planned_tasks']), 0)
                done = max(0, int(output.get('completed_tasks', done)))
            elif state == 'SUCCEEDED' and not manual and not output.get('partial'):
                done = total
            db.change(conn, 'background_jobs', job_id, state=state, stage=stage, result_json=output,
                      progress=done, total=total,
                      error_message=None if succeeded else str(result.get('message') or stage)[:500], finished_at=db.now())
    except HTTPException as error:
        with db.write() as conn:
            db.change(conn, 'background_jobs', job_id, state='STALE' if error.status_code == 409 else 'FAILED',
                      stage='内容已变更' if error.status_code == 409 else '需要人工核对', error_message=str(error.detail)[:500], finished_at=db.now())
    except Exception:
        # Do not write exception reprs: provider exceptions can contain credentials/paths.
        with db.write() as conn:
            db.change(conn, 'background_jobs', job_id, state='FAILED', stage='任务异常，可重试或手工填写',
                      error_message='任务未能完成，已保留原文件和填写内容', finished_at=db.now())


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
        self.thread = threading.Thread(target=self.run, name='ocr-ai-worker', daemon=True)

    def start(self):
        with db.write() as conn:
            stale_before = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
            for job in db.rows(conn, 'background_jobs', state='RUNNING'):
                if not _process_alive(job['worker_token']) or (job.get('heartbeat_at') or '') < stale_before:
                    retry = job['attempts'] < 2
                    db.change(conn, 'background_jobs', job['id'], state='QUEUED' if retry else 'FAILED',
                              stage='恢复中断的任务' if retry else '服务中断，请手动重试', worker_token=None)
            for job in db.rows(conn, 'background_jobs', kind='OCR', state='QUEUED'):
                _normalize_queued_ocr(conn, job)
            for job in db.rows(conn, 'background_jobs', kind='AI', state='QUEUED'):
                _ensure_dependency(conn, job)
        _workers.append(self)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=100)
        if self in _workers:
            _workers.remove(self)

    def run(self):
        while not self.stop_event.is_set() and self.engine is db.engine:
            with db.write() as conn:
                job = _claim_next(conn, self.token)
            if job:
                process_job(job['id'], worker_token=self.token)
            else:
                self.stop_event.wait(.35)


def stop_all():
    for worker in list(_workers):
        worker.stop()
