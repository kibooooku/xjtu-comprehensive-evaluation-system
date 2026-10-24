"""V2 durable jobs, explicit upload confirmation, and active-year boundaries."""
import io
import sqlite3
import threading
import time
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import delete

from app import db, jobs, materials
from app.main import create_app
from app.migrations import initialize_v2


@pytest.fixture
def system(tmp_path):
    with TestClient(create_app(storage_dir=tmp_path, start_worker=False)) as client:
        yield client


def login(client, username):
    client.headers.pop('X-CSRF-Token', None)
    response = client.post('/api/login', json={'username': username, 'password': 'Demo@2026'})
    assert response.status_code == 200, response.text
    client.headers['X-CSRF-Token'] = response.json()['csrf_token']
    return client.get('/api/bootstrap').json()


def child(client, confirmed=True):
    boot = login(client, 'student')
    parent = client.post('/api/submissions', json={'category_id': boot['categories'][0]['id']}).json()
    result = client.post(f"/api/submissions/{parent['id']}/items", json={'staged': True})
    assert result.status_code == 200, result.text
    app = result.json()
    content = io.BytesIO()
    Image.new('RGB', (400, 600), (len(parent['items']) * 20 % 200, 80, 140)).save(content, 'PNG')
    uploaded = client.post(f"/api/applications/{app['id']}/files", files={'file': ('proof.png', content.getvalue(), 'image/png')})
    assert uploaded.status_code == 200, uploaded.text
    if confirmed:
        result = client.post(f"/api/submissions/{parent['id']}/confirm-upload", json={'item_ids': [app['id']]})
        assert result.status_code == 200, result.text
    return parent, app


def test_staged_material_has_no_ocr_job_until_confirmation(system):
    parent, app = child(system, confirmed=False)
    assert system.get('/api/analysis-jobs').json()['jobs'] == []
    hidden = system.get(f"/api/submissions/{parent['id']}").json()
    assert hidden['item_count'] == 0
    assert system.post(f"/api/applications/{app['id']}/jobs", json={'kind': 'OCR'}).status_code == 409
    confirmed = system.post(f"/api/submissions/{parent['id']}/confirm-upload", json={'item_ids': [app['id']]}).json()
    joblist = system.get('/api/analysis-jobs').json()['jobs']
    assert len(joblist) == 1 and joblist[0]['kind'] == 'OCR' and joblist[0]['state'] == 'QUEUED'
    repeat = system.post(f"/api/applications/{app['id']}/jobs", json={'kind': 'OCR'}).json()
    assert repeat['job']['id'] == joblist[0]['id']


def test_worker_continues_while_browser_navigates_and_reconnects(system, monkeypatch):
    parent, app = child(system)
    entered, release = threading.Event(), threading.Event()

    def run_ocr(application_id, user, progress=None):
        entered.set()
        assert release.wait(10)
        progress('文字与印章完成', 2, 2)
        return {'status': 'SUCCEEDED', 'suggested_crops': []}

    monkeypatch.setattr(materials, 'run_ocr', run_ocr, raising=False)
    monkeypatch.setattr(materials, 'apply_ocr_evidence', lambda *args, **kwargs: {}, raising=False)
    worker = jobs.JobWorker()
    worker.start()
    try:
        assert entered.wait(5)
        assert system.get('/api/bootstrap').status_code == 200
        assert system.get('/').status_code == 200
        login(system, 'student')  # A fresh page/session can rediscover the same running job.
        status = system.get('/api/analysis-jobs').json()['jobs'][0]
        assert status['state'] == 'RUNNING'
        release.set()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            status = system.get('/api/analysis-jobs').json()['jobs'][0]
            if status['state'] not in ('QUEUED', 'RUNNING'):
                break
            time.sleep(.03)
        assert status['state'] == 'SUCCEEDED'
        assert status['progress'] == status['total']
    finally:
        release.set()
        worker.stop()


def test_ocr_system_crops_advance_matching_queued_ai_snapshot(system, monkeypatch):
    _, app = child(system)
    ai = system.post(f"/api/applications/{app['id']}/jobs", json={'kind': 'AI'}).json()['job']
    ocr = next(j for j in system.get('/api/analysis-jobs').json()['jobs'] if j['kind'] == 'OCR')
    monkeypatch.setattr(materials, 'run_ocr', lambda *a: {'status': 'SUCCEEDED'}, raising=False)

    def add_generated_crop(*args, **kwargs):
        with db.write() as conn:
            revision = db.one(conn, 'application_revisions', app['revision']['id'])
            db.change(conn, 'application_revisions', revision['id'], payload_json={'system_evidence_test': True})

    monkeypatch.setattr(materials, 'apply_ocr_evidence', add_generated_crop, raising=False)
    jobs.process_job(ocr['id'])
    updated = system.get('/api/analysis-jobs/' + ai['id']).json()
    assert updated['input_fingerprint'] != ai['input_fingerprint']


def test_queued_result_never_overwrites_later_manual_content(system, monkeypatch):
    _, app = child(system)
    queued = system.post(f"/api/applications/{app['id']}/jobs", json={'kind': 'AI'}).json()['job']
    changed = system.put(f"/api/applications/{app['id']}", json={'title': '我手动核定的具体活动', 'requested_score': '0.73'})
    assert changed.status_code == 200
    called = []
    monkeypatch.setattr(materials, 'run_ai', lambda *args: called.append(True), raising=False)
    jobs.process_job(queued['id'])
    assert called == []
    assert system.get('/api/analysis-jobs/' + queued['id']).json()['state'] == 'STALE'
    assert system.get(f"/api/applications/{app['id']}").json()['requested_score'] == '0.73'


def test_force_analysis_preserves_human_draft_and_job_is_private(system, monkeypatch):
    _, app = child(system)
    system.put(f"/api/applications/{app['id']}", json={'title': '手动活动', 'requested_score': '0.90'})
    # An unanalysed human draft may receive first-time suggestions; it is never overwritten.
    first = system.post(f"/api/applications/{app['id']}/jobs", json={'kind': 'AI'})
    assert first.status_code == 200 and first.json()['job']['result_json']['auto_apply'] is False
    queued = system.post(f"/api/applications/{app['id']}/jobs", json={'kind': 'AI', 'force': True}).json()['job']
    monkeypatch.setattr(materials, 'run_ai', lambda *args: {'status': 'SUCCEEDED', 'suggestion': {'title': '新的参考', 'requested_score': '.3'}}, raising=False)
    jobs.process_job(queued['id'])
    assert system.get(f"/api/applications/{app['id']}").json()['requested_score'] == '0.90'
    assert system.get('/api/analysis-jobs/' + queued['id']).json()['result_json']['automatically_applied'] is False
    login(system, 'student2')
    assert system.get('/api/analysis-jobs/' + queued['id']).status_code == 404
    assert system.get('/api/analysis-jobs').json()['jobs'] == []


def test_setting_active_year_archives_writes_but_not_old_pending_review(system):
    boot = login(system, 'student')
    current = boot['active_year']['id']
    app = system.post('/api/applications', json={'category_id': boot['categories'][0]['id']}).json()
    with db.write() as conn:
        file = db.add(conn, 'file_objects', uploaded_by=boot['user']['id'], original_name='proof.png', mime_type='image/png', byte_size=1, sha256='b'*64, storage_uri='not-read')
    assert system.put(f"/api/applications/{app['id']}", json={'title': '旧年活动', 'requested_score': '.3', 'file_ids': [file['id']]}).status_code == 200
    assert system.post(f"/api/applications/{app['id']}/submit", json={}).status_code == 200
    assert system.put('/api/admin/statistics-year', json={'year_id': current}).status_code == 403
    login(system, 'admin')
    new = system.post('/api/admin/academic-years', json={'code': '2027-2028'}).json()
    assert system.put('/api/admin/statistics-year', json={'year_id': new['id']}).status_code == 200
    login(system, 'student')
    assert system.get('/api/bootstrap').json()['active_year']['id'] == new['id']
    assert system.post('/api/applications', json={'year_id': current, 'category_id': boot['categories'][0]['id']}).status_code == 409
    assert system.get(f"/api/applications/{app['id']}").status_code == 200
    login(system, 'reviewer1')
    assert system.post(f"/api/applications/{app['id']}/review", json={'action': 'APPROVE'}).status_code == 200


def test_migration_groups_original_rounds_idempotently_and_backs_up(system):
    boot = login(system, 'student')
    app = system.post('/api/applications', json={'category_id': boot['categories'][0]['id']}).json()
    with db.write() as conn:
        conn.execute(delete(db.tables['category_items']))
        conn.execute(delete(db.tables['category_submissions']))
        conn.execute(delete(db.tables['system_settings']).where(db.tables['system_settings'].c.id == 'schema_version'))
    initialize_v2()
    initialize_v2()
    with db.read() as conn:
        items = db.rows(conn, 'category_items', application_id=app['id'])
        assert len(items) == 1
        assert db.one(conn, 'applications', app['id'])['working_revision_id'] == app['revision']['id']
        assert list(conn.exec_driver_sql('PRAGMA foreign_key_check')) == []
    backups = list((db.storage_root / 'backups').glob('pre-v2-*.db'))
    assert backups
    with sqlite3.connect(backups[0]) as connection:
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_job_validation_and_archived_pending_locked(system):
    _, app = child(system)
    assert system.post('/api/analysis-jobs/bulk', json={'application_ids': [{}]}).status_code == 400
    assert system.post('/api/analysis-jobs/bulk', json={'kind': 'WRONG'}).status_code == 400
    assert system.post(f"/api/applications/{app['id']}/jobs", json={'force': 'false'}).status_code == 400


def test_ocr_then_ai_automatically_fills_only_the_pristine_new_item(system, monkeypatch):
    _, app = child(system)
    config = {'enabled': True, 'secret_id': 'fake-id', 'secret_key': 'fake-key', 'deepseek_key': 'fake-key',
              'deepseek_model': 'test-model', 'max_analysis_pages': 12, 'general_limit': 100, 'seal_limit': 100}
    calls = []

    def fake_ocr(action, content, options):
        calls.append(action)
        if action == 'SealOCR':
            return {'RequestId': 'test-seal', 'SealInfos': [{'SealBody': '测试章', 'Location': {'X': 100, 'Y': 400, 'Width': 60, 'Height': 60}}]}
        return {'RequestId': 'test-general', 'TextDetections': [
            {'DetectedText': '活动名称：迎新广场舞', 'Confidence': 99, 'ItemPolygon': {'X': 20, 'Y': 30, 'Width': 250, 'Height': 20}},
            {'DetectedText': '2025年10月12日', 'Confidence': 99, 'ItemPolygon': {'X': 20, 'Y': 70, 'Width': 250, 'Height': 20}},
            {'DetectedText': '王欣然 DEMO202401004 软件2401', 'Confidence': 99, 'ItemPolygon': {'X': 20, 'Y': 220, 'Width': 350, 'Height': 20}}]}

    monkeypatch.setattr(materials, 'configuration', lambda: config)
    monkeypatch.setattr(materials, 'tencent_ocr', fake_ocr)
    monkeypatch.setattr(materials, 'deepseek_suggestion', lambda *args: {'title': '迎新广场舞（2次）', 'requested_score': '.60', 'payload_json': {}, 'explanation': '两次活动'})
    ai = system.post(f"/api/applications/{app['id']}/jobs", json={'kind': 'AI'}).json()['job']
    ocr = next(j for j in system.get('/api/analysis-jobs').json()['jobs'] if j['kind'] == 'OCR')
    jobs.process_job(ocr['id'])
    jobs.process_job(ai['id'])
    result = system.get('/api/analysis-jobs/' + ai['id']).json()
    assert result['state'] == 'SUCCEEDED', result
    assert result['result_json']['automatically_applied'] is True
    detail = system.get(f"/api/applications/{app['id']}").json()
    assert detail['title'] == '迎新广场舞（2次）' and detail['requested_score'] == '0.60'
    assert detail['student_modified_score'] is False
    participation = [c for c in detail['crops'] if c['evidence_type'] == 'PARTICIPATION']
    assert len(participation) == 1
    assert participation[0]['box'][2:] == [400, 150]
    assert participation[0]['page_number'] == 1
    assert calls.count('GeneralAccurateOCR') == 1 and calls.count('SealOCR') == 1  # AI reuses OCR cache.


def test_interrupted_worker_recovers_from_database_without_recreating_job(system, monkeypatch):
    _, app = child(system)
    queued = system.get('/api/analysis-jobs').json()['jobs'][0]
    with db.write() as conn:
        db.change(conn, 'background_jobs', queued['id'], state='RUNNING', worker_token='99999999:terminated', attempts=1, heartbeat_at=db.now())
    monkeypatch.setattr(materials, 'run_ocr', lambda *args: {'status': 'SUCCEEDED'}, raising=False)
    monkeypatch.setattr(materials, 'apply_ocr_evidence', lambda *args, **kwargs: {}, raising=False)
    worker = jobs.JobWorker()
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = system.get('/api/analysis-jobs/' + queued['id']).json()
            if result['state'] == 'SUCCEEDED':
                break
            time.sleep(.03)
        assert result['state'] == 'SUCCEEDED'
        assert result['attempts'] == 2
        assert len(system.get('/api/analysis-jobs').json()['jobs']) == 1
    finally:
        worker.stop()


def test_legacy_ai_suggestion_remains_readable_without_new_provider_call(system):
    boot = login(system, 'student')
    app = system.post('/api/applications', json={'category_id': boot['categories'][0]['id']}).json()
    with db.write() as conn:
        from app.materials import revision_fingerprint
        revision = db.one(conn, 'application_revisions', app['revision']['id'])
        rule = db.rows(conn, 'score_rule_versions')[0]
        analysis = db.add(conn, 'ai_analyses', application_id=app['id'], input_revision_id=revision['id'],
                          input_fingerprint=revision_fingerprint(conn, revision), score_rule_version_id=rule['id'],
                          model_name='v1-record', prompt_version='moral-score-v1', status='SUCCEEDED',
                          result_json={'suggestion': {'title': '旧版迎新广场舞', 'requested_score': '0.60'}})
    detail = system.get(f"/api/applications/{app['id']}").json()
    assert detail['latest_analysis']['id'] == analysis['id']
    assert detail['latest_analysis']['result_json']['suggestion']['title'] == '旧版迎新广场舞'
    assert system.get('/api/analysis-jobs').json()['jobs'] == []


def test_legacy_evidence_uses_real_source_page_without_rewriting_frozen_data(system):
    _, app = child(system)
    detail = system.get(f"/api/applications/{app['id']}").json()
    page = detail['files'][0]['pages'][0]
    made = system.post(f"/api/applications/{app['id']}/crops", json={'page_id': page['id'], 'evidence_type': 'PARTICIPATION', 'box': [0, 0, 400, 150]})
    assert made.status_code == 200
    with db.write() as conn:
        db.change(conn, 'file_pages', page['id'], page_index=7)
        db.change(conn, 'evidence_crops', made.json()['id'], masks_json={'manual': [], 'automatic': []})
    result = system.get(f"/api/applications/{app['id']}").json()['crops'][0]
    assert result['page_number'] == 7 and result['file_name'] == 'proof.png'
    with db.read() as conn:
        assert 'evidence' not in db.one(conn, 'evidence_crops', result['id'])['masks_json']
