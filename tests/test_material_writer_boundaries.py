"""Slow document CPU/PNG work cannot monopolize SQLite's single writer."""
import io
import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import HTTPException
from PIL import Image, ImageDraw
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from app import db, jobs, materials, pdf_text
from test_materials import material_app, create_claim, upload, png
from test_job_concurrency import cloud_answer, model_answer


@pytest.fixture
def prepared(material_app, monkeypatch):
    _, login = material_app
    client = login('student')
    claim, _, _ = create_claim(client)
    upload(client, claim['id'])
    config = {'enabled': True, 'secret_id': 'synthetic', 'secret_key': 'synthetic', 'deepseek_key': 'synthetic',
              'deepseek_model': 'offline', 'general_limit': 100, 'seal_limit': 100, 'max_analysis_pages': 12}
    monkeypatch.setattr(materials, 'configuration', lambda: config)
    monkeypatch.setattr(materials, 'tencent_ocr', cloud_answer)
    monkeypatch.setattr(materials, 'deepseek_suggestion', model_answer)
    with db.read() as conn:
        user = db.one(conn, 'users', username='student')
    local = threading.local()
    original_write = db.write

    @contextmanager
    def write():
        with original_write() as conn:
            local.writing = True
            try:
                yield conn
            finally:
                local.writing = False

    monkeypatch.setattr(db, 'write', write)

    def low_timeout(connection, *args):
        connection.execute('PRAGMA busy_timeout=150')

    engine = db.engine
    event.listen(engine, 'checkout', low_timeout)
    yield client, claim, user, local
    event.remove(engine, 'checkout', low_timeout)


def test_fifty_page_evidence_read_does_not_block_another_items_ocr_ai_or_writer(prepared, monkeypatch):
    client, short, user, local = prepared
    long, _, _ = create_claim(client)
    pages = []
    for index in range(50):
        image = Image.new('RGB', (300, 400), 'white')
        ImageDraw.Draw(image).text((15, 15), f'offline page {index}', fill='black')
        pages.append(image)
    stream = io.BytesIO()
    pages[0].save(stream, 'PDF', save_all=True, append_images=pages[1:])
    response = client.post(f"/api/applications/{long['id']}/files", files={'file': ('fifty-pages.pdf', stream.getvalue(), 'application/pdf')})
    assert response.status_code == 200

    def extract(path, records):
        return {page['id']: {'usable': True, 'text': '迎新夜跑活动，2026年10月26日', 'regions': [
            {'text': '迎新夜跑活动，2026年10月26日', 'polygon_json': [[5, 5], [250, 5], [250, 25], [5, 25]], 'extra_json': {}},
            *[{'text': f'合成材料第{page["page_index"]}页行{i}', 'polygon_json': [[5, 40+i*10], [250, 40+i*10], [250, 48+i*10], [5, 48+i*10]], 'extra_json': {}} for i in range(50)]]} for page in records}

    monkeypatch.setattr(pdf_text, 'extract_pages_text', extract)
    original = materials._evidence_suggestions
    entered, release = threading.Event(), threading.Event()

    def slow_evidence(conn, records, *args, **kwargs):
        assert not getattr(local, 'writing', False), 'full-document evidence must run outside BEGIN IMMEDIATE'
        if len(records) == 50:
            entered.set()
            assert release.wait(12)
        return original(conn, records, *args, **kwargs)

    monkeypatch.setattr(materials, '_evidence_suggestions', slow_evidence)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(materials.run_ocr, long['id'], user)
        try:
            assert entered.wait(8)
            ocr = materials.run_ocr(short['id'], user)
            ai = materials.run_ai(short['id'], user, ocr_result=ocr)
            assert ai['status'] == 'SUCCEEDED'
            with db.write() as conn:
                db.add(conn, 'system_settings', id='writer-remains-available', value_json={'ok': True})
        finally:
            release.set()
        result = future.result(timeout=15)
    assert result['status'] == 'SUCCEEDED' and result['native_text_page_count'] == 50


def test_fresh_evidence_reuses_locating_and_renders_outside_writer(prepared, monkeypatch):
    _, claim, user, local = prepared
    result = materials.run_ocr(claim['id'], user)
    monkeypatch.setattr(materials, '_evidence_suggestions', lambda *args: pytest.fail('fresh evidence must not scan the whole document twice'))
    original = materials._render_crop_assets
    rendered = []

    def render(*args, **kwargs):
        assert not getattr(local, 'writing', False), 'PNG encoding must happen outside the writer'
        rendered.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(materials, '_render_crop_assets', render)
    applied = materials.apply_ocr_evidence(claim['id'], user, result, expected_ocr_fingerprint=result['ocr_fingerprint'])
    assert applied['applied'] and rendered
    with db.read() as conn:
        assert db.rows(conn, 'evidence_crops', revision_id=result['input_revision_id'])


def test_ocr_sources_are_rechecked_after_rendering_before_crops_are_inserted(prepared, monkeypatch):
    client, claim, user, local = prepared
    result = materials.run_ocr(claim['id'], user)
    entered, release = threading.Event(), threading.Event()
    original = materials._prepare_evidence_assets

    def prepare(*args, **kwargs):
        assets = original(*args, **kwargs)
        assert not getattr(local, 'writing', False)
        entered.set()
        assert release.wait(8)
        return assets

    monkeypatch.setattr(materials, '_prepare_evidence_assets', prepare)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(materials.apply_ocr_evidence, claim['id'], user, result, expected_ocr_fingerprint=result['ocr_fingerprint'])
        try:
            assert entered.wait(4)
            uploaded = client.post(f"/api/applications/{claim['id']}/files", files={'file': ('added.png', png(color='blue'), 'image/png')})
            assert uploaded.status_code == 200
        finally:
            release.set()
        with pytest.raises(HTTPException) as caught:
            future.result(timeout=6)
    assert caught.value.status_code == 409
    with db.read() as conn:
        assert db.rows(conn, 'evidence_crops', revision_id=result['input_revision_id']) == []


def test_ai_application_rechecks_human_edit_after_outside_writer_render(prepared, monkeypatch):
    client, claim, user, local = prepared
    result = materials.run_ocr(claim['id'], user)
    ai = materials.run_ai(claim['id'], user, ocr_result=result)
    assert ai['status'] == 'SUCCEEDED'
    entered, release = threading.Event(), threading.Event()
    original = materials._prepare_evidence_assets

    def prepare(*args, **kwargs):
        assert not getattr(local, 'writing', False)
        assets = original(*args, **kwargs)
        entered.set()
        assert release.wait(8)
        return assets

    monkeypatch.setattr(materials, '_prepare_evidence_assets', prepare)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(materials.apply_analysis, claim['id'], {'analysis_id': ai['analysis_id']}, user)
        try:
            assert entered.wait(4)
            assert client.put(f"/api/applications/{claim['id']}", json={'title': '学生保留的手工名称', 'requested_score': '.73'}).status_code == 200
        finally:
            release.set()
        with pytest.raises(HTTPException) as caught:
            future.result(timeout=6)
    assert caught.value.status_code == 409
    detail = client.get(f"/api/applications/{claim['id']}").json()
    assert detail['title'] == '学生保留的手工名称' and detail['requested_score'] == '0.73' and detail['crops'] == []


def test_successful_cloud_response_survives_failed_database_commit_without_second_cloud_call(prepared, monkeypatch):
    _, claim, _, _ = prepared
    with db.read() as conn:
        page = db.rows(conn, 'file_pages')[0]
    calls = []
    monkeypatch.setattr(materials, 'tencent_ocr', lambda action, *args: calls.append(action) or cloud_answer(action))
    save = materials._save_regions
    failed_once = [False]

    def broken_commit(*args):
        if not failed_once[0]:
            failed_once[0] = True
            raise OperationalError('synthetic-save', {}, Exception('database is locked'))
        return save(*args)

    monkeypatch.setattr(materials, '_save_regions', broken_commit)
    with pytest.raises(OperationalError):
        materials._ocr_page(page, 'GeneralAccurateOCR', materials.configuration())
    with db.read() as conn:
        reserved = db.rows(conn, 'recognition_runs')[0]
        assert reserved['status'] == 'RUNNING'
    recovered = materials._ocr_page(page, 'GeneralAccurateOCR', materials.configuration())
    assert recovered['id'] == reserved['id'] and recovered['status'] == 'SUCCEEDED'
    assert calls == ['GeneralAccurateOCR']
    with db.read() as conn:
        assert materials._usage(conn, 'GeneralAccurateOCR') == 1
        assert len(db.rows(conn, 'recognition_regions', run_id=recovered['id'])) == 3


def test_explicit_retry_reclaims_known_orphan_preserving_original_attempt_count(prepared, monkeypatch):
    _, claim, user, _ = prepared
    with db.write() as conn:
        page = db.rows(conn, 'file_pages')[0]
        revision = db.one(conn, 'application_revisions', claim['revision']['id'])
        job = db.add(conn, 'background_jobs', application_id=claim['id'], owner_id=user['id'], kind='OCR', state='FAILED',
                     input_revision_id=revision['id'], input_fingerprint='synthetic', started_at='2026-01-01T00:00:00+00:00', finished_at='9999-01-01T00:00:00+00:00')
        digest = hashlib.sha256((page['render_sha256'] + 'GeneralAccurateOCR' + 'v2-words').encode()).hexdigest()
        orphan = db.add(conn, 'recognition_runs', page_id=page['id'], provider='TENCENT', api_name='GeneralAccurateOCR', parameters_hash=digest,
                        status='RUNNING', result_json={'worker_pid': os.getpid(), 'worker_job_id': job['id'], 'worker_token': 'old-token'}, started_at=materials.now())
    calls = []
    monkeypatch.setattr(materials, 'tencent_ocr', lambda action, *args: calls.append(action) or cloud_answer(action))
    recovered = materials._ocr_page(page, 'GeneralAccurateOCR', materials.configuration())
    assert recovered['status'] == 'SUCCEEDED' and calls == ['GeneralAccurateOCR']
    with db.read() as conn:
        assert db.one(conn, 'recognition_runs', orphan['id'])['status'] == 'FAILED'
        assert materials._usage(conn, 'GeneralAccurateOCR') == 2


@pytest.mark.skipif(os.name != 'nt', reason='Windows extended path prefix regression')
def test_atomic_cache_creation_prefix_does_not_fail_safe_path_check(prepared, monkeypatch):
    original = Path.resolve

    def resolve(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if path.name == 'synthetic.json':
            return Path('\\\\?\\' + str(result))
        return result

    monkeypatch.setattr(Path, 'resolve', resolve)
    assert materials.asset_path('ocr-response-cache/synthetic.json') == db.storage_root / 'ocr-response-cache/synthetic.json'
    with pytest.raises(HTTPException) as caught:
        materials.asset_path('../outside-storage.png')
    assert caught.value.status_code == 404


def test_same_background_job_reuses_model_response_after_commit_failure_but_new_force_job_does_not(prepared, monkeypatch):
    _, claim, user, _ = prepared
    ocr = materials.run_ocr(claim['id'], user)
    token = f'{os.getpid()}:synthetic-model-cache'

    def job():
        with db.write() as conn:
            return db.add(conn, 'background_jobs', application_id=claim['id'], owner_id=user['id'], kind='AI', state='RUNNING',
                          input_revision_id=ocr['input_revision_id'], input_fingerprint=ocr['input_fingerprint'], worker_token=token)

    original_change, calls, failed_once = materials.change, [], [False]

    def change(conn, table, identifier, **values):
        if table == 'ai_analyses' and values.get('status') == 'SUCCEEDED' and not failed_once[0]:
            failed_once[0] = True
            raise OperationalError('synthetic-ai-save', {}, Exception('database is locked'))
        return original_change(conn, table, identifier, **values)

    monkeypatch.setattr(materials, 'change', change)
    monkeypatch.setattr(materials, 'deepseek_suggestion', lambda context, config: calls.append(True) or model_answer(context, config))
    first = job()
    with materials.ocr_job_scope(first['id'], token):
        with pytest.raises(OperationalError):
            materials.run_ai(claim['id'], user, ocr_result=ocr)
        recovered = materials.run_ai(claim['id'], user, ocr_result=ocr)
    assert recovered['status'] == 'SUCCEEDED' and len(calls) == 1
    forced = job()
    with materials.ocr_job_scope(forced['id'], token):
        assert materials.run_ai(claim['id'], user, ocr_result=ocr)['status'] == 'SUCCEEDED'
    assert len(calls) == 2


def test_legacy_sync_ocr_saves_inflight_response_then_stops_next_page_on_owner_pause(prepared, monkeypatch):
    client, claim, user, _ = prepared
    calls = []

    def provider(action, *args):
        calls.append(action)
        with db.write() as conn:
            jobs.pause_owner(conn, user['id'], 'MANUAL')
        return cloud_answer(action)

    monkeypatch.setattr(materials, 'tencent_ocr', provider)
    with pytest.raises(HTTPException) as caught:
        materials.run_ocr(claim['id'], user)
    assert caught.value.status_code == 409 and calls == ['GeneralAccurateOCR']
    with db.read() as conn:
        assert len(db.rows(conn, 'recognition_runs', status='SUCCEEDED')) == 1
    assert client.post(f"/api/applications/{claim['id']}/analyze", json={}).status_code == 409
    assert calls == ['GeneralAccurateOCR']


def test_legacy_sync_ai_checks_owner_pause_immediately_before_model(prepared, monkeypatch):
    _, claim, user, _ = prepared
    ocr = materials.run_ocr(claim['id'], user)
    original = materials._ai_material_excerpt

    def excerpt(*args, **kwargs):
        value = original(*args, **kwargs)
        with db.write() as conn:
            jobs.pause_owner(conn, user['id'], 'MANUAL')
        return value

    monkeypatch.setattr(materials, '_ai_material_excerpt', excerpt)
    monkeypatch.setattr(materials, 'deepseek_suggestion', lambda *args: pytest.fail('paused owner must not send a model request'))
    with pytest.raises(HTTPException) as caught:
        materials.run_ai(claim['id'], user, ocr_result=ocr)
    assert caught.value.status_code == 409
