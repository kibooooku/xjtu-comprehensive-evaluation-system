"""Durable OCR/AI work queue, independent of the current browser page."""
from __future__ import annotations
import os
import threading
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException
from . import db
from .migrations import active_year, assert_current_year, ensure_group
from .security import current_user

router = APIRouter(prefix='/api', tags=['background recognition'])
ACTIVE = {'QUEUED', 'RUNNING'}
_workers = []


def job_view(job):
    if not job:
        return None
    return {k: v for k, v in job.items() if k not in ('worker_token',)}


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


def enqueue(application_id, user, kind='AI', force=False):
    from .materials import revision_fingerprint
    if kind not in ('OCR', 'AI'):
        raise HTTPException(400, '任务类型无效')
    with db.write() as conn:
        context, revision = _context(conn, application_id, user)
        fingerprint = revision_fingerprint(conn, revision)
        previous = sorted(db.rows(conn, 'background_jobs', application_id=application_id, kind=kind), key=lambda j: j['created_at'])
        for job in reversed(previous):
            if job['input_revision_id'] == revision['id'] and job['input_fingerprint'] == fingerprint and job['state'] in ACTIVE:
                return job_view(job)
        if not force:
            for job in reversed(previous):
                output = job.get('result_json') or {}
                if job['state'] == 'SUCCEEDED' and job['input_revision_id'] == revision['id'] and fingerprint in (job['input_fingerprint'], output.get('output_fingerprint')):
                    return job_view(job)
            prior_analyses = sorted(db.rows(conn, 'ai_analyses', application_id=application_id, status='SUCCEEDED'), key=lambda a: a['created_at']) if kind == 'AI' else []
            analysis_id = revision.get('ai_analysis_id') or (prior_analyses[-1]['id'] if prior_analyses else None)
            if kind == 'AI' and analysis_id:
                analysis_inputs = db.rows(conn, 'analysis_inputs', analysis_id=analysis_id)
                old_files = {i['file_id'] for i in analysis_inputs}
                current_files = {i['file_id'] for i in db.rows(conn, 'revision_files', revision_id=revision['id'])}
                if current_files <= old_files:
                    raise HTTPException(409, '该小项已经分析完成；可单独重新分析获取建议')
        pages = [p for f in db.rows(conn, 'revision_files', revision_id=revision['id']) for p in db.rows(conn, 'file_pages', file_id=f['file_id'])]
        return job_view(db.add(conn, 'background_jobs', application_id=application_id, owner_id=user['id'], kind=kind,
                              state='QUEUED', stage='排队等待OCR' if kind == 'OCR' else '排队等待AI分析',
                              progress=0, total=max(len(pages) * 2, 1), input_revision_id=revision['id'],
                              input_fingerprint=fingerprint, result_json={'auto_apply': not force and _pristine(conn, revision)},
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
    from .materials import revision_fingerprint
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
    return bool(revision and revision_fingerprint(conn, revision) == job['input_fingerprint'])


def process_job(job_id):
    """Process one already-claimed job; callable directly by deterministic tests."""
    from . import materials
    with db.read() as conn:
        job = db.one(conn, 'background_jobs', job_id)
        user = db.one(conn, 'users', job['owner_id']) if job else None
        valid = bool(job and user and user['status'] == 'ACTIVE' and _snapshot_valid(conn, job))
    if not valid:
        with db.write() as conn:
            if job:
                db.change(conn, 'background_jobs', job_id, state='STALE', stage='材料或学年已变更', finished_at=db.now())
        return

    def progress(stage, done, total):
        with db.write() as conn:
            db.change(conn, 'background_jobs', job_id, stage=str(stage)[:150], progress=int(done), total=max(int(total), 1), heartbeat_at=db.now())

    try:
        result = materials.run_ocr(job['application_id'], user, progress) if job['kind'] == 'OCR' else materials.run_ai(job['application_id'], user, progress)
        with db.read() as conn:
            valid = _snapshot_valid(conn, job)
        succeeded = result.get('status') == 'SUCCEEDED'
        output = {**(job.get('result_json') or {}), **result}
        if not valid:
            state, stage = 'STALE', '内容已修改，结果仅供参考'
        elif not succeeded:
            state, stage = 'FAILED', result.get('message') or '分析未完成，可重试或手工填写'
        else:
            state, stage = 'SUCCEEDED', 'OCR完成' if job['kind'] == 'OCR' else 'AI分析完毕'
            if job['kind'] == 'OCR':
                materials.apply_ocr_evidence(job['application_id'], user, result, expected_fingerprint=job['input_fingerprint'])
                with db.write() as conn:
                    revision = db.one(conn, 'application_revisions', job['input_revision_id'])
                    new_fingerprint = materials.revision_fingerprint(conn, revision)
                    output['output_fingerprint'] = new_fingerprint
                    # Only this exact system-generated crop update may advance queued AI snapshots.
                    for queued in db.rows(conn, 'background_jobs', application_id=job['application_id'], kind='AI', state='QUEUED'):
                        if queued['input_revision_id'] == job['input_revision_id'] and queued['input_fingerprint'] == job['input_fingerprint']:
                            db.change(conn, 'background_jobs', queued['id'], input_fingerprint=new_fingerprint)
            elif output.get('auto_apply') and not job['force']:
                analysis_id = result.get('analysis_id') or (result.get('analysis') or {}).get('id')
                materials.apply_analysis(job['application_id'], {'analysis_id': analysis_id}, user)
                output['automatically_applied'] = True
                with db.read() as conn:
                    revision = db.one(conn, 'application_revisions', job['input_revision_id'])
                    output['output_fingerprint'] = materials.revision_fingerprint(conn, revision)
            else:
                output['automatically_applied'] = False
                stage = '分析完毕，建议待确认（保留人工内容）'
        with db.write() as conn:
            latest = db.one(conn, 'background_jobs', job_id)
            db.change(conn, 'background_jobs', job_id, state=state, stage=stage, result_json=output,
                      progress=latest['total'] if state == 'SUCCEEDED' else latest['progress'],
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
        os.kill(pid, 0)
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
                pending = sorted(db.rows(conn, 'background_jobs', state='QUEUED'), key=lambda j: j['created_at'])
                job = pending[0] if pending else None
                if job:
                    db.change(conn, 'background_jobs', job['id'], state='RUNNING', started_at=db.now(),
                              heartbeat_at=db.now(), worker_token=self.token, attempts=job['attempts'] + 1)
            if job:
                process_job(job['id'])
            else:
                self.stop_event.wait(.35)


def stop_all():
    for worker in list(_workers):
        worker.stop()
