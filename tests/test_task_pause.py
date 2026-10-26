"""User-controlled work lifetime, with synthetic providers and temporary storage."""
import threading
import time

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import OperationalError

from app import db, jobs, materials
from test_job_concurrency import queue, cloud_answer, model_answer, wait_until


def control(queue, owner='student', action='pause'):
    response = queue.clients[owner].post('/api/analysis-control', json={'action': action})
    assert response.status_code == 200, response.text
    return response.json()


def test_pause_is_owner_scoped_deduplicated_and_requires_explicit_resume(queue):
    first = queue.item()
    second = queue.item('student2')
    assert control(queue)['paused']
    assert queue.records('OCR', 'PAUSED')[0]['application_id'] == first.id
    with db.write() as conn:
        claimed = jobs._claim_next(conn, 'synthetic-worker', 'OCR')
        assert claimed['application_id'] == second.id
    again = queue.clients['student'].post(f'/api/applications/{first.id}/jobs', json={'kind': 'OCR'})
    assert again.status_code == 200 and again.json()['job']['id'] == first.ocr['id']
    third = queue.item()
    assert third.ocr['state'] == 'PAUSED'
    presence = queue.clients['student'].post('/api/analysis-control/presence', json={'tab_id': 'test-tab-one', 'document_id': 'test-document'})
    assert presence.json()['paused'] is True
    assert control(queue, action='resume')['paused'] is False
    assert {j['application_id'] for j in queue.records('OCR', 'QUEUED')} == {first.id, third.id}


def test_last_tab_leaving_pauses_but_live_sibling_and_old_reload_document_do_not(queue):
    claim = queue.item()
    client = queue.clients['student']
    for tab in ('site-tab-a', 'site-tab-b'):
        assert client.post('/api/analysis-control/presence', json={'tab_id': tab, 'document_id': tab + '-old'}).status_code == 200
    first = client.post('/api/analysis-control/leave', json={'tab_id': 'site-tab-a', 'document_id': 'site-tab-a-old'})
    assert first.json()['paused'] is False
    assert client.post('/api/analysis-control/presence', json={'tab_id': 'site-tab-b', 'document_id': 'new-document', 'reload': True}).json()['paused'] is False
    stale = client.post('/api/analysis-control/leave', json={'tab_id': 'site-tab-b', 'document_id': 'site-tab-b-old'})
    assert stale.json()['paused'] is False
    last = client.post('/api/analysis-control/leave', json={'tab_id': 'site-tab-b', 'document_id': 'new-document'})
    assert last.json()['paused'] is True
    assert queue.records('OCR', 'PAUSED')[0]['application_id'] == claim.id
    refreshed = client.post('/api/analysis-control/presence', json={'tab_id': 'site-tab-b', 'document_id': 'third-document', 'reload': True})
    assert refreshed.json()['paused'] is False
    control(queue)
    assert client.post('/api/analysis-control/presence', json={'tab_id': 'site-tab-b', 'document_id': 'fourth-document', 'reload': True}).json()['paused'] is True


def test_disconnected_browser_times_out_only_registered_owner(queue):
    first = queue.item()
    second = queue.item('student2')
    client = queue.clients['student']
    client.post('/api/analysis-control/presence', json={'tab_id': 'offline-tab', 'document_id': 'document'})
    with db.write() as conn:
        value = jobs._control(conn, first.owner)
        value['tabs']['offline-tab']['seen_at'] = time.time() - jobs.PRESENCE_TTL_SECONDS - 1
        jobs._save_control(conn, first.owner, value)
        jobs._expire_presence(conn)
    assert queue.records('OCR', 'PAUSED')[0]['application_id'] == first.id
    assert queue.records('OCR', 'QUEUED')[0]['application_id'] == second.id
    assert client.get('/api/analysis-control').json()['reason'] == 'CONNECTION_LOST'


def test_server_logout_pauses_even_without_frontend_hook_and_control_requires_csrf(queue):
    first = queue.item()
    queue.item('student2')
    client = queue.clients['student']
    csrf = client.headers.pop('X-CSRF-Token')
    assert client.post('/api/analysis-control', json={'action': 'pause'}).status_code == 403
    client.headers['X-CSRF-Token'] = csrf
    assert client.post('/api/logout').status_code == 200
    assert queue.records('OCR', 'PAUSED')[0]['application_id'] == first.id
    assert len(queue.records('OCR', 'QUEUED')) == 1
    assert client.get('/api/analysis-control').status_code == 401


def test_ocr_pause_finishes_only_inflight_call_and_resume_reuses_its_cache(queue, monkeypatch):
    claim = queue.item(files=1)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def provider(action, image, config):
        calls.append(action)
        if action == 'GeneralAccurateOCR':
            entered.set()
            assert release.wait(10)
        return cloud_answer(action)
    monkeypatch.setattr(materials, 'tencent_ocr', provider)
    monkeypatch.setattr(materials, 'deepseek_suggestion', model_answer)
    worker = jobs.JobWorker()
    worker.start()
    try:
        assert entered.wait(6)
        control(queue)
        release.set()
        wait_until(lambda: queue.records('OCR', 'PAUSED'))
        assert calls == ['GeneralAccurateOCR']
        assert queue.records('AI') == []
        control(queue, action='resume')
        wait_until(lambda: queue.records('AI', 'SUCCEEDED'))
        assert calls == ['GeneralAccurateOCR', 'SealOCR']
        assert queue.clients['student'].get(f'/api/applications/{claim.id}').json()['requested_score'] == '0.30'
    finally:
        release.set()
        worker.stop()


def test_inflight_ai_result_is_saved_on_pause_then_applied_without_second_model_call(queue, monkeypatch):
    claim = queue.item()
    entered, release = threading.Event(), threading.Event()
    calls = []
    monkeypatch.setattr(materials, 'tencent_ocr', cloud_answer)
    def model(context, config):
        calls.append('model')
        entered.set()
        assert release.wait(10)
        return model_answer(context, config)
    monkeypatch.setattr(materials, 'deepseek_suggestion', model)
    worker = jobs.JobWorker()
    worker.start()
    try:
        assert entered.wait(8)
        control(queue)
        release.set()
        wait_until(lambda: queue.records('AI', 'PAUSED'))
        paused = queue.records('AI', 'PAUSED')[0]
        assert paused['result_json']['completed_result'] is True
        assert queue.clients['student'].get(f'/api/applications/{claim.id}').json()['title'] == ''
        control(queue, action='resume')
        wait_until(lambda: queue.records('AI', 'SUCCEEDED'))
        assert calls == ['model']
        assert queue.clients['student'].get(f'/api/applications/{claim.id}').json()['title'] == '迎新夜跑活动'
    finally:
        release.set()
        worker.stop()


def test_dispatcher_survives_transient_database_writer_and_processes_later_work(queue, monkeypatch):
    queue.item()
    monkeypatch.setattr(materials, 'tencent_ocr', cloud_answer)
    monkeypatch.setattr(materials, 'deepseek_suggestion', model_answer)
    original = jobs._claim_next
    calls = []
    def claim(conn, token, kind=None):
        calls.append(kind)
        if len(calls) == 1:
            raise OperationalError('BEGIN IMMEDIATE', {}, RuntimeError('database is locked'))
        return original(conn, token, kind)
    monkeypatch.setattr(jobs, '_claim_next', claim)
    worker = jobs.JobWorker()
    worker.start()
    try:
        wait_until(lambda: queue.records('AI', 'SUCCEEDED'))
        assert worker.thread.is_alive() and len(calls) > 1
    finally:
        worker.stop()


def test_database_busy_after_provider_return_pauses_and_resumes_without_repeating_provider(queue, monkeypatch):
    item = queue.item()
    monkeypatch.setattr(materials, 'tencent_ocr', cloud_answer)
    jobs.process_job(item.ocr['id'])
    ai = queue.records('AI', 'QUEUED')[0]
    with db.write() as conn:
        db.change(conn, 'background_jobs', ai['id'], result_json={**ai['result_json'], 'auto_apply': False})
    calls = []
    def analyze(*args, **kwargs):
        calls.append('model-result')
        return {'status': 'SUCCEEDED', 'analysis_id': 'synthetic-result', 'suggestion': {'title': '已返回的合成建议'}}
    monkeypatch.setattr(materials, 'run_ai', analyze)
    original = jobs._snapshot_valid
    raised = []
    def snapshot(conn, job):
        if job['kind'] == 'AI' and calls and not raised:
            raised.append(True)
            raise OperationalError('BEGIN IMMEDIATE', {}, RuntimeError('database is locked'))
        return original(conn, job)
    monkeypatch.setattr(jobs, '_snapshot_valid', snapshot)
    jobs.process_job(ai['id'])
    paused = queue.records('AI', 'PAUSED')[0]
    assert paused['result_json']['completed_result'] is True
    assert paused['result_json']['error_code'] == 'DatabaseBusy'
    control(queue, action='resume')
    jobs.process_job(ai['id'])
    assert len(queue.records('AI', 'SUCCEEDED')) == 1
    assert calls == ['model-result']


def test_control_rejects_bad_tabs_and_nonstudent_users(queue):
    client = queue.clients['student']
    assert client.post('/api/analysis-control/presence', json={'tab_id': '../../bad'}).status_code == 400
    assert client.post('/api/analysis-control', json={'action': 'delete'}).status_code == 400
    response = client.post('/api/login', json={'username': 'admin', 'password': 'Demo@2026'})
    assert response.status_code == 200
    client.headers['X-CSRF-Token'] = response.json()['csrf_token']
    assert client.get('/api/analysis-control').status_code == 403
    assert client.post('/api/analysis-control', json={'action': 'pause'}).status_code == 403


def test_unexpected_job_failure_records_safe_location_without_exception_message(queue, monkeypatch):
    claim = queue.item()
    secret = 'PRIVATE_PROVIDER_SENTINEL_DO_NOT_SAVE'
    def explode(*args, **kwargs):
        raise RuntimeError(secret)
    monkeypatch.setattr(materials, 'run_ocr', explode)
    jobs.process_job(claim.ocr['id'])
    failed = queue.records('OCR', 'FAILED')[0]
    diagnostic = failed['result_json']['diagnostics']
    assert diagnostic['exception_type'] == 'RuntimeError'
    assert diagnostic['locations'][-1]['file'] == 'test_task_pause.py'
    assert diagnostic['locations'][-1]['function'] == 'explode'
    assert secret not in str(failed)


def test_already_resolved_deleted_identity_cannot_recreate_control_metadata(queue):
    owner = queue.boots['student']['user']['id']
    with db.write() as conn:
        snapshot = db.one(conn, 'users', owner)
        db.change(conn, 'users', owner, status='DELETED')
    for handler, body in ((jobs.presence, {'tab_id': 'late-page-one'}),
                          (jobs.leave, {'tab_id': 'late-page-one'}),
                          (jobs.change_control, {'action': 'resume'})):
        with pytest.raises(HTTPException) as caught:
            handler(body, snapshot)
        assert caught.value.status_code == 401
    with db.read() as conn:
        assert db.one(conn, 'system_settings', jobs.CONTROL_PREFIX + owner) is None
