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
    with TestClient(create_app(database_url='sqlite:///' + (tmp_path / 'jobs-test.db').as_posix(), storage_dir=tmp_path, start_worker=False)) as client:
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
            before = materials.revision_fingerprint(conn, revision)
            db.change(conn, 'application_revisions', revision['id'], payload_json={'system_evidence_test': True})
            after = materials.revision_fingerprint(conn, db.one(conn, 'application_revisions', revision['id']))
            kwargs['on_applied'](conn, before, after)
            return {'fingerprint': after}

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
    monkeypatch.setattr(materials, 'run_ocr', lambda *args: {'status': 'SUCCEEDED'}, raising=False)
    monkeypatch.setattr(materials, 'apply_ocr_evidence', lambda *args, **kwargs: {}, raising=False)
    monkeypatch.setattr(materials, 'run_ai', lambda *args, **kwargs: {'status': 'SUCCEEDED', 'suggestion': {'title': '新的参考', 'requested_score': '.3'}}, raising=False)
    ocr = next(j for j in system.get('/api/analysis-jobs').json()['jobs'] if j['kind'] == 'OCR')
    jobs.process_job(ocr['id'])  # AI must explicitly wait for its original OCR.
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


def test_policy_change_preserves_approved_history_without_reanalysis(system, monkeypatch):
    _, app = child(system)
    assert system.put(f"/api/applications/{app['id']}", json={'title': '审核通过的迎新活动', 'requested_score': '.3'}).status_code == 200
    with db.write() as conn:
        old_job = db.rows(conn, 'background_jobs', application_id=app['id'], kind='OCR')[0]
        old_job = db.change(conn, 'background_jobs', old_job['id'], state='SUCCEEDED', finished_at=db.now(),
                            result_json={'status': 'SUCCEEDED', 'policy_version': 'LEGACY_500_PAGES', 'run_ids': []})
    assert system.post(f"/api/applications/{app['id']}/submit", json={}).status_code == 200
    login(system, 'reviewer1')
    approved = system.post(f"/api/applications/{app['id']}/review", json={'action': 'APPROVE'})
    assert approved.status_code == 200 and approved.json()['state'] == 'APPROVED'
    with db.read() as conn:
        revision_before = db.one(conn, 'application_revisions', app['revision']['id'])
    monkeypatch.setattr(materials, 'run_ocr', lambda *args: pytest.fail('approved history cannot start OCR'))
    monkeypatch.setattr(materials, 'run_ai', lambda *args, **kwargs: pytest.fail('approved history cannot start AI'))
    login(system, 'student')
    assert system.post(f"/api/applications/{app['id']}/jobs", json={'kind': 'AI', 'force': True}).status_code == 409
    assert system.post(f"/api/applications/{app['id']}/jobs", json={'kind': 'OCR', 'force': True}).status_code == 409
    jobs.process_job(old_job['id'])
    with db.read() as conn:
        assert db.one(conn, 'application_revisions', app['revision']['id']) == revision_before
        assert db.one(conn, 'background_jobs', old_job['id']) == old_job
        assert len(db.rows(conn, 'background_jobs', application_id=app['id'])) == 1


def test_real_long_pdf_text_layer_flows_through_queue_with_endpoint_seals_only(system, tmp_path, monkeypatch):
    import pypdfium2 as pdfium
    from test_pdf_text import make_pdf
    source = make_pdf(tmp_path / 'native-one-page.pdf')
    last_source = make_pdf(tmp_path / 'native-last-page.pdf', rotation=90)
    destination = tmp_path / 'native-thirteen-pages.pdf'
    original, last, document = pdfium.PdfDocument(source), pdfium.PdfDocument(last_source), pdfium.PdfDocument.new()
    try:
        document.import_pages(original, pages=[0] * 12)
        document.import_pages(last, pages=[0])
        document.save(destination)
    finally:
        document.close()
        original.close()
        last.close()
    config = {'enabled': True, 'secret_id': 'not-a-real-key', 'secret_key': 'not-a-real-key',
              'deepseek_key': 'not-a-real-key', 'deepseek_model': 'test-model',
              'max_analysis_pages': 12, 'general_limit': 100, 'seal_limit': 100}
    monkeypatch.setattr(materials, 'configuration', lambda: config)
    cloud_calls = []

    def endpoint_seal(action, content, options):
        assert action == 'SealOCR', 'native text must replace all General calls'
        cloud_calls.append(action)
        return {'RequestId': 'test-seal-' + str(len(cloud_calls)), 'SealInfos': []}

    monkeypatch.setattr(materials, 'tencent_ocr', endpoint_seal)
    prompts = []

    def model(context, options):
        prompts.append(context)
        return {'title': 'Volunteer event', 'requested_score': '.3', 'payload_json': {}, 'explanation': '本地文字层模拟模型建议'}

    monkeypatch.setattr(materials, 'deepseek_suggestion', model)
    boot = login(system, 'student')
    parent = system.post('/api/submissions', json={'category_id': boot['categories'][0]['id']}).json()
    item = system.post(f"/api/submissions/{parent['id']}/items", json={}).json()
    uploaded = system.post(f"/api/applications/{item['id']}/files", files={'file': ('native.pdf', destination.read_bytes(), 'application/pdf')})
    assert uploaded.status_code == 200, uploaded.text
    confirmed = system.post(f"/api/submissions/{parent['id']}/confirm-upload", json={'item_ids': [item['id']]})
    assert confirmed.status_code == 200, confirmed.text
    ocr = confirmed.json()['jobs'][0]
    queued = system.post(f"/api/applications/{item['id']}/jobs", json={'kind': 'AI'}).json()['job']
    jobs.process_job(ocr['id'])
    source_result = system.get('/api/analysis-jobs/' + ocr['id']).json()
    assert source_result['state'] == 'SUCCEEDED', source_result
    assert source_result['result_json']['native_text_page_count'] == 13
    assert source_result['result_json']['planned_tasks'] == source_result['total'] == source_result['progress'] == 2
    jobs.process_job(queued['id'])
    completed = system.get('/api/analysis-jobs/' + queued['id']).json()
    assert completed['state'] == 'SUCCEEDED', completed
    assert completed['result_json']['native_text_page_count'] == 13 and len(prompts) == 1
    assert 'Volunteer event' in str(prompts[0]['untrusted_materials'])
    assert system.get(f"/api/applications/{item['id']}").json()['title'] == 'Volunteer event'
    with db.read() as conn:
        runs = db.rows(conn, 'recognition_runs')
        assert len([run for run in runs if run['provider'] == 'LOCAL_PDF']) == 13
        clouds = [run for run in runs if run['provider'] == 'TENCENT']
        assert len(clouds) == len(cloud_calls) == 2 and all(run['api_name'] == 'SealOCR' for run in clouds)
        assert {db.one(conn, 'file_pages', run['page_id'])['page_index'] for run in clouds} == {1, 13}
        analysis = db.one(conn, 'ai_analyses', completed['result_json']['analysis_id'])
        assert analysis['result_json']['policy_version'] == jobs.OCR_POLICY_VERSION


def test_lower_seven_page_limit_queues_eight_page_scan_for_endpoint_only_work(system, tmp_path, monkeypatch):
    import pypdfium2 as pdfium
    from test_pdf_text import make_pdf
    source = make_pdf(tmp_path / 'scan-one-page.pdf', image=True, text=False)
    last_source = make_pdf(tmp_path / 'scan-last-page.pdf', image=True, text=False, rotation=90)
    destination = tmp_path / 'scan-eight-pages.pdf'
    original, last, document = pdfium.PdfDocument(source), pdfium.PdfDocument(last_source), pdfium.PdfDocument.new()
    try:
        document.import_pages(original, pages=[0] * 7)
        document.import_pages(last, pages=[0])
        document.save(destination)
    finally:
        document.close()
        original.close()
        last.close()
    config = {'enabled': True, 'secret_id': 'not-a-real-key', 'secret_key': 'not-a-real-key',
              'deepseek_key': 'not-a-real-key', 'deepseek_model': 'test-model',
              'max_analysis_pages': 7, 'general_limit': 100, 'seal_limit': 100}
    monkeypatch.setattr(materials, 'configuration', lambda: config)
    cloud_calls, model_calls = [], []

    def endpoint_ocr(action, content, options):
        cloud_calls.append(action)
        if action == 'SealOCR':
            return {'RequestId': 'mock-seal', 'SealInfos': []}
        assert action == 'GeneralAccurateOCR'
        return {'RequestId': 'mock-text', 'TextDetections': [
            {'DetectedText': '活动名称：迎新广场舞', 'Confidence': 99, 'ItemPolygon': {'X': 20, 'Y': 30, 'Width': 250, 'Height': 20}}]}

    def model(context, options):
        model_calls.append(context)
        return {'title': '迎新广场舞', 'requested_score': '.3', 'payload_json': {}, 'explanation': '端点文字模拟建议'}

    monkeypatch.setattr(materials, 'tencent_ocr', endpoint_ocr)
    monkeypatch.setattr(materials, 'deepseek_suggestion', model)
    boot = login(system, 'student')
    parent = system.post('/api/submissions', json={'category_id': boot['categories'][0]['id']}).json()
    item = system.post(f"/api/submissions/{parent['id']}/items", json={}).json()
    uploaded = system.post(f"/api/applications/{item['id']}/files", files={'file': ('scan.pdf', destination.read_bytes(), 'application/pdf')})
    assert uploaded.status_code == 200, uploaded.text
    confirmed = system.post(f"/api/submissions/{parent['id']}/confirm-upload", json={'item_ids': [item['id']]})
    assert confirmed.status_code == 200, confirmed.text
    queued = confirmed.json()['jobs'][0]
    assert queued['total'] == 4 and queued['progress'] == 0
    ai = system.post(f"/api/applications/{item['id']}/jobs", json={'kind': 'AI'}).json()['job']
    jobs.process_job(queued['id'])
    jobs.process_job(ai['id'])
    completed = system.get('/api/analysis-jobs/' + queued['id']).json()
    completed_ai = system.get('/api/analysis-jobs/' + ai['id']).json()
    assert completed['state'] == completed_ai['state'] == 'SUCCEEDED'
    assert completed['result_json']['status'] == completed_ai['result_json']['status'] == 'SUCCEEDED'
    assert completed['total'] == completed['progress'] == completed['result_json']['planned_tasks'] == 4
    assert completed['partial'] is True and len(model_calls) == 1
    skipped = completed['result_json']['manual_required_files'][0]
    assert skipped['page_count'] == 8 and skipped['max_pages'] == 7
    assert skipped['page_numbers'] == list(range(2, 8))
    assert cloud_calls.count('GeneralAccurateOCR') == cloud_calls.count('SealOCR') == 2
    with db.read() as conn:
        clouds = [run for run in db.rows(conn, 'recognition_runs') if run['provider'] == 'TENCENT']
        assert len(clouds) == 4
        assert {db.one(conn, 'file_pages', run['page_id'])['page_index'] for run in clouds} == {1, 8}
