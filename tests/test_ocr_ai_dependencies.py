"""Actual worker threads + HTTP clicks; all provider entry points are replaced."""
import io
import os
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import delete

from app import db, jobs, materials
from app.main import create_app


def test_windows_liveness_probe_cannot_emit_console_interrupt(monkeypatch):
    if os.name != 'nt':
        pytest.skip('Windows console signal regression')
    def forbidden_kill(*args):
        raise AssertionError('process liveness must not send a Windows console event')
    monkeypatch.setattr(jobs.os, 'kill', forbidden_kill)
    assert jobs._process_alive(f'{os.getpid()}:worker') is True
    assert jobs._process_alive('99999999:terminated') is False


def test_windows_access_denied_counts_as_possibly_alive_without_kill(monkeypatch):
    if os.name != 'nt':
        pytest.skip('Windows process permissions')
    import ctypes
    kernel = SimpleNamespace(OpenProcess=lambda *args: 0, GetExitCodeProcess=lambda *args: False, CloseHandle=lambda *args: True)
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *args, **kwargs: kernel)
    monkeypatch.setattr(ctypes, 'get_last_error', lambda: 5)
    monkeypatch.setattr(jobs.os, 'kill', lambda *args: pytest.fail('must not send a Windows signal'))
    assert jobs._process_alive('123456:access-denied') is True


@pytest.fixture
def queued_item(tmp_path, monkeypatch):
    monkeypatch.setenv('CLOUD_ANALYSIS_ENABLED', 'false')
    with TestClient(create_app(database_url='sqlite:///' + (tmp_path / 'dependencies-test.db').as_posix(), storage_dir=tmp_path, start_worker=False)) as client:
        session = client.post('/api/login', json={'username': 'student', 'password': 'Demo@2026'})
        assert session.status_code == 200
        client.headers['X-CSRF-Token'] = session.json()['csrf_token']
        boot = client.get('/api/bootstrap').json()
        parent = client.post('/api/submissions', json={'category_id': boot['categories'][0]['id']}).json()
        child = client.post(f"/api/submissions/{parent['id']}/items", json={}).json()
        content = io.BytesIO()
        Image.new('RGB', (400, 600), 'white').save(content, 'PNG')
        upload = client.post(f"/api/applications/{child['id']}/files", files={'file': ('proof.png', content.getvalue(), 'image/png')})
        assert upload.status_code == 200
        confirmed = client.post(f"/api/submissions/{parent['id']}/confirm-upload", json={'item_ids': [child['id']]})
        assert confirmed.status_code == 200
        ocr = confirmed.json()['jobs'][0]
        yield client, parent, child, ocr
    jobs.stop_all()


class Pipeline:
    def __init__(self, monkeypatch):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.ai_entered = threading.Event()
        self.ocr_calls = []
        self.ai_inputs = []
        self.suggestions = []
        self.status = 'SUCCEEDED'
        monkeypatch.setattr(materials, 'run_ocr', self.ocr)
        monkeypatch.setattr(materials, 'run_ai', self.ai)
        monkeypatch.setattr(materials, 'apply_analysis', lambda *args, **kwargs: {})

    def ocr(self, application_id, user, progress=None):
        with db.read() as conn:
            application = db.one(conn, 'applications', application_id)
            revision = db.one(conn, 'application_revisions', application['working_revision_id'])
            files = db.rows(conn, 'revision_files', revision_id=revision['id'])
            page_ids = [page['id'] for file in files for page in db.rows(conn, 'file_pages', file_id=file['file_id'])]
            result = {'status': self.status, 'input_revision_id': revision['id'],
                      'input_fingerprint': materials.revision_fingerprint(conn, revision),
                      'ocr_fingerprint': materials.ocr_input_fingerprint(conn, revision),
                      'page_ids': page_ids, 'run_ids': [], 'suggested_crops': self.suggestions,
                      'warnings': ['印章未识别；AI不得自动重试印章']}
        self.ocr_calls.append(application_id)
        if progress:
            progress('文字识别继续中', 1, 18)
        self.entered.set()
        assert self.release.wait(10), 'test must release the blocked OCR'
        if progress:
            progress('OCR已完成', 18, 18)
        return result

    def ai(self, application_id, user, progress=None, *, ocr_result=None):
        assert ocr_result is not None, 'AI must consume its completed OCR dependency'
        with db.read() as conn:
            ocr_jobs = db.rows(conn, 'background_jobs', application_id=application_id, kind='OCR')
            assert any(job['state'] == 'SUCCEEDED' for job in ocr_jobs)
        self.ai_inputs.append(ocr_result)
        self.ai_entered.set()
        if progress:
            progress('AI分析已完成（复用OCR）', 1, 1)
        return {'status': 'SUCCEEDED', 'analysis_id': 'provider-free-test', 'suggestion': {'title': '迎新夜跑', 'requested_score': '0.30'}}


def wait_finished(client, job_id, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get('/api/analysis-jobs/' + job_id)
        assert response.status_code == 200
        result = response.json()
        if result['state'] not in jobs.ACTIVE:
            return result
        time.sleep(.025)
    pytest.fail('job did not finish before the deterministic test deadline')


def bulk(client):
    response = client.post('/api/analysis-jobs/bulk', json={'kind': 'AI'})
    assert response.status_code == 200, response.text
    assert len(response.json()['jobs']) == 1, response.text
    return response.json()['jobs'][0]


def test_bulk_click_waits_for_existing_running_ocr_even_with_two_workers(queued_item, monkeypatch):
    client, _, child, ocr = queued_item
    pipeline = Pipeline(monkeypatch)
    first, second = jobs.JobWorker(), jobs.JobWorker()
    first.start()
    try:
        assert pipeline.entered.wait(5)
        queued = bulk(client)
        assert queued['wait_for_ocr_job_id'] == ocr['id'] and queued['waiting_for_ocr'] is True
        assert '等待OCR' in queued['stage']
        assert bulk(client)['id'] == queued['id']
        second.start()
        assert pipeline.ai_entered.wait(.4) is False
        status = client.get('/api/analysis-jobs/' + ocr['id']).json()
        assert status['state'] == 'RUNNING' and status['progress'] == 1 and status['total'] == 18
        waiting = client.get('/api/analysis-jobs/' + queued['id']).json()
        assert waiting['state'] == 'QUEUED' and waiting['attempts'] == 0
        pipeline.release.set()
        assert wait_finished(client, queued['id'])['state'] == 'SUCCEEDED'
        assert wait_finished(client, ocr['id'])['state'] == 'SUCCEEDED'
        assert pipeline.ocr_calls == [child['id']] and len(pipeline.ai_inputs) == 1
        assert '不得自动重试' in pipeline.ai_inputs[0]['warnings'][0]
    finally:
        pipeline.release.set()
        first.stop()
        if second.thread.is_alive():
            second.stop()


def test_title_score_edit_does_not_stop_ocr_or_reuse_an_old_ai_request(queued_item, monkeypatch):
    client, _, child, ocr = queued_item
    pipeline = Pipeline(monkeypatch)
    worker = jobs.JobWorker()
    worker.start()
    try:
        assert pipeline.entered.wait(5)
        old = bulk(client)
        edited = client.put(f"/api/applications/{child['id']}", json={'title': '手动核对的迎新夜跑', 'requested_score': '0.73'})
        assert edited.status_code == 200
        new = bulk(client)
        assert new['id'] != old['id'] and new['wait_for_ocr_job_id'] == ocr['id']
        running = client.get('/api/analysis-jobs/' + ocr['id']).json()
        assert running['state'] == 'RUNNING' and running['input_fingerprint'] == ocr['input_fingerprint']
        pipeline.release.set()
        assert wait_finished(client, ocr['id'])['state'] == 'SUCCEEDED'
        assert wait_finished(client, old['id'])['state'] == 'STALE'
        completed = wait_finished(client, new['id'])
        assert completed['state'] == 'SUCCEEDED' and completed['result_json']['automatically_applied'] is False
        detail = client.get(f"/api/applications/{child['id']}").json()
        assert detail['title'] == '手动核对的迎新夜跑' and detail['requested_score'] == '0.73'
        assert pipeline.ocr_calls == [child['id']] and len(pipeline.ai_inputs) == 1
    finally:
        pipeline.release.set()
        worker.stop()


def test_enqueue_after_crop_commit_reuses_atomically_advanced_ai_snapshot(queued_item, monkeypatch):
    client, _, child, ocr = queued_item
    pipeline = Pipeline(monkeypatch)
    page = client.get(f"/api/applications/{child['id']}").json()['files'][0]['pages'][0]
    pipeline.suggestions = [{'page_id': page['id'], 'evidence_type': 'PARTICIPATION', 'box': [0, 0, 400, 150]}]
    ai = bulk(client)
    original_apply = materials.apply_ocr_evidence
    crop_committed, release_finalization = threading.Event(), threading.Event()

    def stop_after_commit(*args, **kwargs):
        result = original_apply(*args, **kwargs)
        crop_committed.set()
        assert release_finalization.wait(10)
        return result

    monkeypatch.setattr(materials, 'apply_ocr_evidence', stop_after_commit)
    worker = jobs.JobWorker()
    worker.start()
    try:
        assert pipeline.entered.wait(5)
        pipeline.release.set()
        assert crop_committed.wait(5)
        with db.read() as conn:
            queued = db.one(conn, 'background_jobs', ai['id'])
            revision = db.one(conn, 'application_revisions', queued['input_revision_id'])
            assert queued['input_fingerprint'] == materials.revision_fingerprint(conn, revision)
            assert queued['input_fingerprint'] != ai['input_fingerprint']
        assert bulk(client)['id'] == ai['id']
        with db.read() as conn:
            assert len(db.rows(conn, 'background_jobs', application_id=child['id'], kind='AI', state='QUEUED')) == 1
            assert db.one(conn, 'background_jobs', ocr['id'])['state'] == 'RUNNING'
        release_finalization.set()
        assert wait_finished(client, ai['id'])['state'] == 'SUCCEEDED'
        assert len(pipeline.ocr_calls) == 1 and len(pipeline.ai_inputs) == 1
    finally:
        pipeline.release.set()
        release_finalization.set()
        worker.stop()


@pytest.mark.parametrize('ocr_state', ['FAILED', 'STALE'])
def test_failed_dependency_never_implicitly_retries_ocr(queued_item, monkeypatch, ocr_state):
    client, _, child, ocr = queued_item
    pipeline = Pipeline(monkeypatch)
    ai = bulk(client)
    with db.write() as conn:
        db.change(conn, 'background_jobs', ocr['id'], state=ocr_state, stage='OCR未完成', finished_at=db.now())
    jobs.process_job(ai['id'])
    failed = client.get('/api/analysis-jobs/' + ai['id']).json()
    assert failed['state'] == 'FAILED' and failed['result_json']['ocr_dependency_state'] == ocr_state
    assert '重试OCR' in failed['error_message']
    retry_ai = client.post(f"/api/applications/{child['id']}/jobs", json={'kind': 'AI', 'force': True})
    assert retry_ai.status_code == 409 and '重试OCR' in retry_ai.json()['detail']
    assert pipeline.ocr_calls == [] and pipeline.ai_inputs == []
    with db.read() as conn:
        assert len(db.rows(conn, 'background_jobs', kind='OCR')) == 1
    # Only this explicit OCR retry creates a new recognition job.
    retry_ocr = client.post(f"/api/applications/{child['id']}/jobs", json={'kind': 'OCR', 'force': True})
    assert retry_ocr.status_code == 200 and retry_ocr.json()['job']['id'] != ocr['id']
    resumed = bulk(client)
    assert resumed['wait_for_ocr_job_id'] == retry_ocr.json()['job']['id']
    pipeline.release.set()
    worker = jobs.JobWorker()
    worker.start()
    try:
        assert wait_finished(client, resumed['id'])['state'] == 'SUCCEEDED'
        assert pipeline.ocr_calls == [child['id']] and len(pipeline.ai_inputs) == 1
    finally:
        worker.stop()


def test_startup_normalizes_existing_legacy_ocr_and_waiting_ai_without_cloud_recreation(queued_item, monkeypatch):
    client, _, child, ocr = queued_item
    pipeline = Pipeline(monkeypatch)
    ai = bulk(client)
    with db.write() as conn:
        revision = db.one(conn, 'application_revisions', ocr['input_revision_id'])
        old_content_fp = materials.revision_fingerprint(conn, revision)
        db.change(conn, 'background_jobs', ocr['id'], input_fingerprint=old_content_fp, result_json={'auto_apply': True})
        db.change(conn, 'background_jobs', ai['id'], result_json={'auto_apply': True})
    pipeline.release.set()
    worker = jobs.JobWorker()
    worker.start()
    try:
        assert wait_finished(client, ai['id'])['state'] == 'SUCCEEDED'
        migrated = client.get('/api/analysis-jobs/' + ocr['id']).json()
        assert migrated['result_json']['fingerprint_kind'] == jobs.OCR_FINGERPRINT_KIND
        assert migrated['result_json']['legacy_content_input_fingerprint'] == old_content_fp
        assert migrated['input_fingerprint'] != old_content_fp
        assert client.get('/api/analysis-jobs/' + ai['id']).json()['wait_for_ocr_job_id'] == ocr['id']
        assert len(client.get('/api/analysis-jobs').json()['jobs']) == 2
        assert pipeline.ocr_calls == [child['id']] and len(pipeline.ai_inputs) == 1
    finally:
        worker.stop()


def test_legacy_ai_without_ocr_job_gets_explicit_dependency_before_claim(queued_item, monkeypatch):
    client, _, child, original_ocr = queued_item
    pipeline = Pipeline(monkeypatch)
    ai = bulk(client)
    with db.write() as conn:
        db.change(conn, 'background_jobs', ai['id'], result_json={'auto_apply': False})
        conn.execute(delete(db.tables['background_jobs']).where(db.tables['background_jobs'].c.id == original_ocr['id']))
    pipeline.release.set()
    worker = jobs.JobWorker()
    worker.start()
    try:
        result = wait_finished(client, ai['id'])
        assert result['state'] == 'SUCCEEDED'
        assert result['wait_for_ocr_job_id'] and result['wait_for_ocr_job_id'] != original_ocr['id']
        with db.read() as conn:
            assert len(db.rows(conn, 'background_jobs', application_id=child['id'], kind='OCR')) == 1
        assert pipeline.ocr_calls == [child['id']] and len(pipeline.ai_inputs) == 1
    finally:
        worker.stop()
