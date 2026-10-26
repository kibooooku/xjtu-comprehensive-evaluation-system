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
        self.extra = {}
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
                      'policy_version': jobs.OCR_POLICY_VERSION,
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
        return {**result, **self.extra}

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
@pytest.mark.parametrize('old_policy', [False, True])
def test_failed_dependency_never_implicitly_retries_ocr(queued_item, monkeypatch, ocr_state, old_policy):
    client, _, child, ocr = queued_item
    pipeline = Pipeline(monkeypatch)
    ai = bulk(client)
    with db.write() as conn:
        db.change(conn, 'background_jobs', ocr['id'], state=ocr_state, stage='OCR未完成', finished_at=db.now(),
                  result_json={**ocr['result_json'], 'policy_version': 'LEGACY_500_PAGES' if old_policy else jobs.OCR_POLICY_VERSION})
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
        db.change(conn, 'background_jobs', ai['id'], result_json={'auto_apply': True, 'policy_version': jobs.OCR_POLICY_VERSION})
    pipeline.release.set()
    worker = jobs.JobWorker()
    worker.start()
    try:
        assert wait_finished(client, ai['id'])['state'] == 'SUCCEEDED'
        migrated = client.get('/api/analysis-jobs/' + ocr['id']).json()
        assert migrated['result_json']['fingerprint_kind'] == jobs.OCR_FINGERPRINT_KIND
        assert migrated['result_json']['policy_version'] == jobs.OCR_POLICY_VERSION
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
        db.change(conn, 'background_jobs', ai['id'], result_json={'auto_apply': False, 'policy_version': jobs.OCR_POLICY_VERSION})
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


def test_manual_only_ocr_finishes_and_ai_neither_deadlocks_nor_calls_a_provider(queued_item, monkeypatch):
    client, _, child, ocr = queued_item
    pipeline = Pipeline(monkeypatch)
    pipeline.status = 'MANUAL_REQUIRED'
    skipped = [{'file_id': 'long-scan', 'file_name': '扫描材料.pdf', 'page_count': 56, 'max_pages': 12, 'code': 'FILE_TOO_LONG'}]
    pipeline.extra = {'planned_tasks': 2, 'completed_tasks': 0, 'manual_required_files': skipped,
                      'skipped_files': skipped, 'partial': True, 'message': '未取得文字且免费额度不足，需手动填写、计分和框选'}
    monkeypatch.setattr(materials, 'apply_ocr_evidence', lambda *args, **kwargs: pytest.fail('manual-only result must not rewrite old evidence'))
    ai = bulk(client)
    pipeline.release.set()
    jobs.process_job(ocr['id'])
    jobs.process_job(ai['id'])
    completed = client.get('/api/analysis-jobs/' + ai['id']).json()
    source = client.get('/api/analysis-jobs/' + ocr['id']).json()
    assert source['state'] == completed['state'] == 'SUCCEEDED'
    assert source['progress'] == 0 and source['total'] == source['result_json']['planned_tasks'] == 2
    assert completed['requires_manual'] is True and completed['result_json']['status'] == 'MANUAL_REQUIRED'
    assert completed['result_json']['automatically_applied'] is False and 'AI未调用' in completed['stage']
    assert pipeline.ocr_calls == [child['id']] and pipeline.ai_inputs == []
    assert bulk(client)['id'] == ai['id']  # Repeat clicks do not rescan manual material.


def test_partial_ocr_keeps_actual_work_counts_and_carries_missing_material_to_ai(queued_item, monkeypatch):
    client, _, child, ocr = queued_item
    pipeline = Pipeline(monkeypatch)
    skipped = [{'file_id': 'scan', 'file_name': '超长扫描.pdf', 'page_count': 20, 'max_pages': 12, 'code': 'FILE_TOO_LONG'}]
    pipeline.extra = {'planned_tasks': 5, 'completed_tasks': 2, 'partial': True,
                      'manual_required_files': skipped, 'skipped_files': skipped}
    ai = bulk(client)
    pipeline.release.set()
    jobs.process_job(ocr['id'])
    jobs.process_job(ai['id'])
    source = client.get('/api/analysis-jobs/' + ocr['id']).json()
    completed = client.get('/api/analysis-jobs/' + ai['id']).json()
    assert source['state'] == completed['state'] == 'SUCCEEDED'
    assert (source['progress'], source['total']) == (2, 5)
    assert '部分' in source['stage'] and '手动补充' in completed['stage']
    assert completed['result_json']['manual_required_files'] == skipped
    assert completed['partial'] is True and completed['requires_manual'] is True
    assert completed['result_json']['warnings'] == pipeline.ai_inputs[0]['warnings']
    assert pipeline.ocr_calls == [child['id']] and len(pipeline.ai_inputs) == 1


def test_long_native_text_result_can_reach_ai_with_only_endpoint_seal_tasks(queued_item, monkeypatch):
    client, _, child, ocr = queued_item
    # A 56-page source snapshot. The mocked material runner represents local
    # PDF text extraction, not a cloud OCR response.
    with db.write() as conn:
        revision = db.one(conn, 'application_revisions', ocr['input_revision_id'])
        attachment = db.rows(conn, 'revision_files', revision_id=revision['id'])[0]
        template = db.rows(conn, 'file_pages', file_id=attachment['file_id'])[0]
        for index in range(2, 57):
            db.add(conn, 'file_pages', **{**{key: value for key, value in template.items() if key != 'id'}, 'page_index': index})
        db.change(conn, 'background_jobs', ocr['id'], input_fingerprint=materials.ocr_input_fingerprint(conn, revision), total=1)
    pipeline = Pipeline(monkeypatch)
    pipeline.extra = {'planned_tasks': 2, 'completed_tasks': 2, 'native_text_page_count': 56,
                      'processed_page_ids': ['local-text-pages'], 'run_ids': ['native-text-run']}
    ai = bulk(client)
    pipeline.release.set()
    monkeypatch.setattr(materials, 'apply_ocr_evidence', lambda *args, **kwargs: {})
    jobs.process_job(ocr['id'])
    jobs.process_job(ai['id'])
    assert client.get('/api/analysis-jobs/' + ai['id']).json()['state'] == 'SUCCEEDED'
    completed_ocr = client.get('/api/analysis-jobs/' + ocr['id']).json()
    assert completed_ocr['progress'] == completed_ocr['total'] == 2
    assert len(pipeline.ai_inputs) == 1
    assert pipeline.ai_inputs[0]['native_text_page_count'] == 56
    assert pipeline.ai_inputs[0]['run_ids'] == ['native-text-run']
    assert len(pipeline.ai_inputs[0]['page_ids']) == 56


def test_ai_worker_preserves_local_stop_source_without_inventing_provider_quota(queued_item, monkeypatch):
    client, _, _, ocr = queued_item
    pipeline = Pipeline(monkeypatch)
    stop = {'stop_reason': 'LocalBudgetExceeded', 'stop_code': 'LocalBudgetExceeded',
            'stop_source': 'LOCAL_LIMIT', 'stop_message': '本地调用上限，不代表腾讯云余额。',
            'stop_details': {'action': 'SealOCR', 'used': 100, 'limit': 100}}
    pipeline.extra = {**stop, 'stopped': True, 'partial': True}
    ai = bulk(client)
    pipeline.release.set()
    monkeypatch.setattr(materials, 'apply_ocr_evidence', lambda *args, **kwargs: {})
    jobs.process_job(ocr['id'])
    jobs.process_job(ai['id'])
    completed = client.get('/api/analysis-jobs/' + ai['id']).json()
    assert completed['state'] == 'SUCCEEDED'
    assert {key: completed['result_json'][key] for key in stop} == stop
    assert {key: pipeline.ai_inputs[0][key] for key in stop} == stop


def test_old_policy_success_is_history_not_a_current_cache_hit(queued_item, monkeypatch):
    client, _, child, ocr = queued_item
    old_ai = bulk(client)
    with db.write() as conn:
        revision = db.one(conn, 'application_revisions', ocr['input_revision_id'])
        rule = db.rows(conn, 'score_rule_versions')[0]
        analysis = db.add(conn, 'ai_analyses', application_id=child['id'], input_revision_id=revision['id'],
                          input_fingerprint=materials.revision_fingerprint(conn, revision), score_rule_version_id=rule['id'],
                          model_name='old-policy', prompt_version='test', status='SUCCEEDED',
                          result_json={'policy_version': 'LEGACY_500_PAGES', 'suggestion': {'title': '旧建议', 'requested_score': '.3'}})
        db.change(conn, 'application_revisions', revision['id'], ai_analysis_id=analysis['id'])
        for old in (ocr, old_ai):
            db.change(conn, 'background_jobs', old['id'], state='SUCCEEDED', finished_at=db.now(),
                      result_json={**old['result_json'], 'status': 'SUCCEEDED', 'policy_version': 'LEGACY_500_PAGES'})
    monkeypatch.setattr(materials, 'run_ocr', lambda *args: pytest.fail('enqueue must not call a cloud provider'))
    current = bulk(client)
    assert current['id'] != old_ai['id'] and current['wait_for_ocr_job_id'] != ocr['id']
    assert current['result_json']['policy_version'] == jobs.OCR_POLICY_VERSION
    assert current['result_json']['auto_apply'] is False  # Old AI-filled content remains reviewable.
    with db.read() as conn:
        assert db.one(conn, 'background_jobs', ocr['id'])['state'] == 'SUCCEEDED'
        assert db.one(conn, 'background_jobs', old_ai['id'])['state'] == 'SUCCEEDED'
        assert db.one(conn, 'application_revisions', revision['id'])['ai_analysis_id'] == analysis['id']


def test_old_policy_waiting_ai_expires_without_rebasing_manual_content(queued_item, monkeypatch):
    client, _, child, ocr = queued_item
    pipeline = Pipeline(monkeypatch)
    old_ai = bulk(client)
    with db.write() as conn:
        db.change(conn, 'background_jobs', old_ai['id'], result_json={**old_ai['result_json'], 'policy_version': 'LEGACY_500_PAGES'})
    edited = client.put(f"/api/applications/{child['id']}", json={'title': '手填的具体活动', 'requested_score': '.88'})
    assert edited.status_code == 200
    jobs.process_job(old_ai['id'])
    expired = client.get('/api/analysis-jobs/' + old_ai['id']).json()
    assert expired['state'] == 'STALE' and expired['result_json']['policy_expired'] is True
    assert expired['input_fingerprint'] == old_ai['input_fingerprint']
    assert pipeline.ocr_calls == [] and pipeline.ai_inputs == []
    current = bulk(client)
    assert current['id'] != old_ai['id'] and current['wait_for_ocr_job_id'] == ocr['id']
    assert current['result_json']['auto_apply'] is False
    assert client.get(f"/api/applications/{child['id']}").json()['requested_score'] == '0.88'


def test_explicit_policy_expiration_allows_new_click_but_real_failure_still_does_not(queued_item, monkeypatch):
    client, _, child, ocr = queued_item
    with db.write() as conn:
        db.change(conn, 'background_jobs', ocr['id'], state='STALE',
                  result_json={**ocr['result_json'], 'policy_version': 'OLD', 'policy_expired': True})
    monkeypatch.setattr(materials, 'run_ocr', lambda *args: pytest.fail('a click only queues recognition'))
    new_ai = bulk(client)
    assert new_ai['wait_for_ocr_job_id'] != ocr['id']
    with db.read() as conn:
        source = db.one(conn, 'background_jobs', new_ai['wait_for_ocr_job_id'])
        assert source['state'] == 'QUEUED' and source['result_json']['policy_version'] == jobs.OCR_POLICY_VERSION


def test_old_long_manual_success_does_not_skip_new_endpoint_policy_on_explicit_click(queued_item, monkeypatch):
    client, _, child, old_ocr = queued_item
    old_ai = bulk(client)
    with db.write() as conn:
        revision = db.one(conn, 'application_revisions', old_ocr['input_revision_id'])
        attachment = db.rows(conn, 'revision_files', revision_id=revision['id'])[0]
        template = db.rows(conn, 'file_pages', file_id=attachment['file_id'])[0]
        for index in range(2, 57):
            db.add(conn, 'file_pages', **{**{key: value for key, value in template.items() if key != 'id'}, 'page_index': index})
        for job in (old_ocr, old_ai):
            db.change(conn, 'background_jobs', job['id'], state='SUCCEEDED', total=0, progress=0, finished_at=db.now(),
                      result_json={'status': 'MANUAL_REQUIRED', 'planned_tasks': 0, 'completed_tasks': 0,
                                   'policy_version': 'TENCENT_FREE_FILE12_SEAL_ENDPOINTS_TEXTLAYER_V1'})
        old_records = {job['id']: db.one(conn, 'background_jobs', job['id']) for job in (old_ocr, old_ai)}
    monkeypatch.setattr(materials, 'run_ocr', lambda *args: pytest.fail('no recognition before the explicit task is processed'))
    current = bulk(client)
    assert current['id'] != old_ai['id'] and current['wait_for_ocr_job_id'] != old_ocr['id']
    with db.read() as conn:
        source = db.one(conn, 'background_jobs', current['wait_for_ocr_job_id'])
        assert source['total'] == 4 and source['state'] == 'QUEUED'
        assert source['result_json']['policy_version'] == jobs.OCR_POLICY_VERSION
        assert all(db.one(conn, 'background_jobs', job_id) == record for job_id, record in old_records.items())
