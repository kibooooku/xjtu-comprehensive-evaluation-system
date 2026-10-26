"""Real queue/file threads with synthetic documents and zero network calls."""
import hashlib
import io
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import db, jobs, materials, pdf_text
from app.integrations import ProviderError
from app.main import create_app


def wait_until(check, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(.025)
    pytest.fail('synthetic background work did not reach the expected state')


@pytest.fixture
def queue(tmp_path, monkeypatch):
    config = {'enabled': True, 'secret_id': 'synthetic', 'secret_key': 'synthetic',
              'deepseek_key': 'synthetic', 'deepseek_model': 'offline-test',
              'general_limit': 1000, 'seal_limit': 1000, 'max_analysis_pages': 12}
    monkeypatch.setattr(materials, 'configuration', lambda: config)
    monkeypatch.setattr(materials, 'tencent_ocr', lambda *args: pytest.fail('unmocked OCR is forbidden'))
    monkeypatch.setattr(materials, 'deepseek_suggestion', lambda *args: pytest.fail('unmocked model is forbidden'))
    app = create_app(database_url='sqlite:///' + (tmp_path / 'parallel.db').as_posix(), storage_dir=tmp_path / 'assets', start_worker=False)
    clients = {}
    boots = {}
    with TestClient(app):
        for name in ('student', 'student2'):
            client = TestClient(app)
            session = client.post('/api/login', json={'username': name, 'password': 'Demo@2026'})
            client.headers['X-CSRF-Token'] = session.json()['csrf_token']
            clients[name] = client
            boots[name] = client.get('/api/bootstrap').json()
        sequence = 0

        def item(owner='student', files=1, *, manual=False, pdf=None):
            nonlocal sequence
            client = clients[owner]
            category = next(c for c in boots[owner]['categories'] if c['code'] == 'COLLECTIVE')
            parent = client.post('/api/submissions', json={'category_id': category['id']}).json()
            child = client.post(f"/api/submissions/{parent['id']}/items", json={'staged': True}).json()
            for index in range(files):
                sequence += 1
                data = io.BytesIO()
                Image.new('RGB', (160, 240), (sequence % 250, 180, 220)).save(data, 'PNG')
                content = pdf if pdf is not None else data.getvalue()
                extension = 'pdf' if pdf is not None else 'png'
                response = client.post(f"/api/applications/{child['id']}/files", files={'file': (f'proof-{sequence}.{extension}', content, 'application/pdf' if pdf else 'image/png')})
                assert response.status_code == 200, response.text
            if manual:
                changed = client.put(f"/api/applications/{child['id']}", json={'title': '手工核对的迎新夜跑活动', 'requested_score': '.73'})
                assert changed.status_code == 200, changed.text
            confirmed = client.post(f"/api/submissions/{parent['id']}/confirm-upload", json={'item_ids': [child['id']]})
            assert confirmed.status_code == 200, confirmed.text
            return SimpleNamespace(id=child['id'], parent=parent['id'], owner=boots[owner]['user']['id'], ocr=confirmed.json()['jobs'][0])

        def records(kind=None, state=None):
            with db.read() as conn:
                return db.rows(conn, 'background_jobs', **{key: value for key, value in {'kind': kind, 'state': state}.items() if value})

        yield SimpleNamespace(item=item, clients=clients, boots=boots, records=records, config=config)
        jobs.stop_all()
        for client in clients.values():
            client.close()


def cloud_answer(action, image=None, config=None):
    if action == 'SealOCR':
        return {'RequestId': 'synthetic-seal', 'SealInfos': []}
    return {'RequestId': 'synthetic-text', 'TextDetections': [
        {'DetectedText': text, 'Confidence': 100, 'ItemPolygon': {'X': 5, 'Y': top, 'Width': 145, 'Height': 20}}
        for text, top in [('活动名称：迎新夜跑活动，2026年10月24日', 5),
                          ('王欣然 DEMO202401004 软件2401', 70),
                          ('赵一诺 DEMO202401005 软件2401', 100)]]}


def model_answer(context, config):
    return {'title': '迎新夜跑活动', 'requested_score': '.3', 'payload_json': {}, 'explanation': '合成离线模型结果'}


def test_one_item_four_files_runs_three_then_queues_fourth_and_auto_fills(queue, monkeypatch):
    claim = queue.item(files=4)
    entered, release = threading.Event(), threading.Event()
    lock, calls = threading.Lock(), []

    def provider(action, image, config):
        if action == 'GeneralAccurateOCR':
            with lock:
                calls.append(hashlib.sha256(image).hexdigest())
                if len(calls) == 3:
                    entered.set()
            assert release.wait(10)
        return cloud_answer(action)

    monkeypatch.setattr(materials, 'tencent_ocr', provider)
    monkeypatch.setattr(materials, 'deepseek_suggestion', model_answer)
    worker = jobs.JobWorker()
    worker.start()
    try:
        assert entered.wait(6)
        time.sleep(.15)
        assert len(calls) == 3
        running = queue.records('OCR', 'RUNNING')
        assert len(running) == 1 and len(running[0]['result_json']['active_file_ids']) == 3
        assert queue.records('AI') == []  # No manual analysis click or early model work.
        release.set()
        wait_until(lambda: len(queue.records('AI', 'SUCCEEDED')) == 1)
        assert len(calls) == 4
        detail = queue.clients['student'].get(f'/api/applications/{claim.id}').json()
        assert detail['title'] == '迎新夜跑活动' and detail['requested_score'] == '0.30'
        assert queue.records('AI')[0]['result_json']['automatically_applied'] is True
        repeated = queue.clients['student'].post(f'/api/submissions/{claim.parent}/confirm-upload', json={'item_ids': [claim.id]})
        assert repeated.status_code == 200
        assert len(queue.records('OCR')) == len(queue.records('AI')) == 1
    finally:
        release.set()
        worker.stop()


def test_mixed_items_share_three_file_slots_per_user_without_blocking_other_users(queue, monkeypatch):
    for name in ('student', 'student2'):
        for _ in range(4):
            queue.item(name, files=2)
    with db.read() as conn:
        owners = {hashlib.sha256(materials.asset_path(page['image_uri']).read_bytes()).hexdigest(): db.one(conn, 'file_objects', page['file_id'])['uploaded_by']
                  for page in db.rows(conn, 'file_pages')}
    release, six_entered = threading.Event(), threading.Event()
    entered, lock = {}, threading.Lock()

    def provider(action, image, config):
        if action == 'GeneralAccurateOCR':
            owner = owners[hashlib.sha256(image).hexdigest()]
            with lock:
                entered[owner] = entered.get(owner, 0) + 1
                if len(entered) == 2 and all(n == 3 for n in entered.values()):
                    six_entered.set()
            assert release.wait(12)
        return cloud_answer(action)

    monkeypatch.setattr(materials, 'tencent_ocr', provider)
    monkeypatch.setattr(materials, 'deepseek_suggestion', model_answer)
    first, second = jobs.JobWorker(), jobs.JobWorker()
    first.start()
    second.start()  # Independent schedulers still share the durable leases/caps.
    try:
        assert six_entered.wait(8)
        time.sleep(.15)
        assert sorted(entered.values()) == [3, 3]
        assert len(queue.records('OCR', 'RUNNING')) == 6
        assert len(queue.records('OCR', 'QUEUED')) == 2
        counts = {}
        for record in queue.records('OCR', 'RUNNING'):
            counts[record['owner_id']] = counts.get(record['owner_id'], 0) + len(record['result_json']['active_file_ids'])
        assert sorted(counts.values()) == [3, 3]
        release.set()
        wait_until(lambda: len(queue.records('AI', 'SUCCEEDED')) == 8, timeout=18)
    finally:
        release.set()
        first.stop()
        second.stop()


def test_ai_three_per_user_is_independent_of_ongoing_ocr_and_all_items_start_automatically(queue, monkeypatch):
    for name in ('student', 'student2'):
        for _ in range(4):
            queue.item(name)
    release, six_models = threading.Event(), threading.Event()
    counts, peak, calls, lock = {}, {}, {}, threading.Lock()

    def model(context, config):
        owner = context['student']['name']
        with lock:
            counts[owner] = counts.get(owner, 0) + 1
            calls[owner] = calls.get(owner, 0) + 1
            peak[owner] = max(peak.get(owner, 0), counts[owner])
            if len(counts) == 2 and all(n == 3 for n in counts.values()):
                six_models.set()
        try:
            assert release.wait(14)
            return model_answer(context, config)
        finally:
            with lock:
                counts[owner] -= 1

    monkeypatch.setattr(materials, 'tencent_ocr', cloud_answer)
    monkeypatch.setattr(materials, 'deepseek_suggestion', model)
    worker = jobs.JobWorker()
    worker.start()
    try:
        assert six_models.wait(9)
        wait_until(lambda: len(queue.records('OCR', 'SUCCEEDED')) == 8)
        assert sorted(peak.values()) == [3, 3]
        assert len(queue.records('AI', 'RUNNING')) == 6
        assert len(queue.records('AI', 'QUEUED')) == 2
        # A new OCR file completes while all six model requests remain blocked.
        newer = queue.item('student')
        wait_until(lambda: any(j['application_id'] == newer.id for j in queue.records('OCR', 'SUCCEEDED')))
        assert len(queue.records('AI', 'RUNNING')) == 6 and len(queue.records('AI', 'QUEUED')) == 3
        release.set()
        wait_until(lambda: len(queue.records('AI', 'SUCCEEDED')) == 9, timeout=15)
        assert sorted(calls.values()) == [4, 5] and sorted(peak.values()) == [3, 3]
    finally:
        release.set()
        worker.stop()


def test_failed_ocr_has_no_model_and_explicit_ocr_retry_auto_suggests_without_overwriting_human(queue, monkeypatch):
    claim = queue.item(manual=True)
    fail, calls = [True], []

    def provider(action, image, config):
        if fail[0]:
            raise ProviderError('NetworkError', '合成网络中断')
        return cloud_answer(action)

    def model(context, config):
        calls.append(context)
        return model_answer(context, config)

    monkeypatch.setattr(materials, 'tencent_ocr', provider)
    monkeypatch.setattr(materials, 'deepseek_suggestion', model)
    worker = jobs.JobWorker()
    worker.start()
    try:
        wait_until(lambda: len(queue.records('OCR', 'SUCCEEDED')) == 1)
        assert queue.records('OCR')[0]['result_json']['status'] == 'MANUAL_REQUIRED'
        assert queue.records('AI') == [] and calls == []
        fail[0] = False
        retried = queue.clients['student'].post(f'/api/applications/{claim.id}/jobs', json={'kind': 'OCR', 'force': True})
        assert retried.status_code == 200
        wait_until(lambda: len(queue.records('AI', 'SUCCEEDED')) == 1)
        assert len(calls) == 1
        finished = queue.records('AI')[0]
        assert finished['result_json']['auto_apply'] is False and finished['result_json']['automatically_applied'] is False
        detail = queue.clients['student'].get(f'/api/applications/{claim.id}').json()
        assert detail['title'] == '手工核对的迎新夜跑活动' and detail['requested_score'] == '0.73'
    finally:
        worker.stop()


def test_parallel_cache_dedup_and_atomic_last_free_credit(queue, monkeypatch):
    first, second = queue.item(), queue.item()
    with db.read() as conn:
        pages = db.rows(conn, 'file_pages')
    entered, release = threading.Event(), threading.Event()
    calls = []

    def provider(action, image, config):
        calls.append(action)
        entered.set()
        assert release.wait(8)
        return cloud_answer(action)

    monkeypatch.setattr(materials, 'tencent_ocr', provider)
    config = {**queue.config, 'general_limit': 1}
    with ThreadPoolExecutor(max_workers=3) as pool:
        one = pool.submit(materials._ocr_page, pages[0], 'GeneralAccurateOCR', config)
        assert entered.wait(4)
        duplicate = pool.submit(materials._ocr_page, pages[0], 'GeneralAccurateOCR', config)
        over_budget = pool.submit(materials._ocr_page, pages[1], 'GeneralAccurateOCR', config)
        with pytest.raises(ProviderError, match='上限'):
            over_budget.result(timeout=4)
        release.set()
        assert one.result(timeout=5)['id'] == duplicate.result(timeout=5)['id']
    assert calls == ['GeneralAccurateOCR']
    with db.read() as conn:
        assert materials._usage(conn, 'GeneralAccurateOCR') == 1
        assert len(db.rows(conn, 'recognition_runs', status='SUCCEEDED')) == 1


def test_graceful_shutdown_stops_next_file_calls_and_recovers_from_cached_pages(queue, monkeypatch):
    queue.item(files=4)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def provider(action, image, config):
        calls.append(action)
        if len(calls) == 3:
            entered.set()
        assert release.wait(10)
        return cloud_answer(action)

    monkeypatch.setattr(materials, 'tencent_ocr', provider)
    monkeypatch.setattr(materials, 'deepseek_suggestion', model_answer)
    worker = jobs.JobWorker()
    worker.start()
    assert entered.wait(6)
    stopped = threading.Thread(target=worker.stop)
    stopped.start()
    wait_until(worker.stop_event.is_set)
    release.set()
    stopped.join(timeout=7)
    assert not stopped.is_alive()
    assert calls == ['GeneralAccurateOCR'] * 3  # No seal or fourth-file call after stop.
    assert len(queue.records('OCR', 'QUEUED')) == 1 and queue.records('AI') == []
    assert queue.records('OCR')[0]['attempts'] == 0
    monkeypatch.setattr(materials, 'tencent_ocr', cloud_answer)
    recovered = jobs.JobWorker()
    recovered.start()
    try:
        wait_until(lambda: len(queue.records('AI', 'SUCCEEDED')) == 1)
        with db.read() as conn:
            assert materials._usage(conn, 'GeneralAccurateOCR') == 4  # Three existing successes reused.
    finally:
        recovered.stop()


def test_native_text_only_needs_endpoint_seal_and_automatically_starts_ai(queue, monkeypatch):
    image = Image.new('RGB', (160, 240), 'white')
    data = io.BytesIO()
    image.save(data, 'PDF')
    claim = queue.item(pdf=data.getvalue())

    def extract(path, pages):
        return {page['id']: {'usable': True, 'text': '迎新夜跑活动，2026年10月24日', 'regions': [{'text': '迎新夜跑活动，2026年10月24日', 'polygon_json': [[5, 5], [145, 5], [145, 30], [5, 30]], 'extra_json': {}}]} for page in pages}

    calls = []
    monkeypatch.setattr(pdf_text, 'extract_pages_text', extract)
    monkeypatch.setattr(materials, 'tencent_ocr', lambda action, *args: calls.append(action) or cloud_answer(action))
    monkeypatch.setattr(materials, 'deepseek_suggestion', model_answer)
    worker = jobs.JobWorker()
    worker.start()
    try:
        wait_until(lambda: len(queue.records('AI', 'SUCCEEDED')) == 1)
        assert calls == ['SealOCR']
        assert queue.records('OCR')[0]['result_json']['native_text_page_count'] == 1
        assert queue.records('AI')[0]['result_json']['wait_for_ocr_job_id'] == claim.ocr['id']
    finally:
        worker.stop()


def test_old_direct_analysis_and_old_completed_ocr_never_rerun_on_start(queue, monkeypatch):
    claim = queue.item()
    with db.write() as conn:
        job = db.one(conn, 'background_jobs', claim.ocr['id'])
        revision = db.one(conn, 'application_revisions', job['input_revision_id'])
        rule = db.rows(conn, 'score_rule_versions')[0]
        analysis = db.add(conn, 'ai_analyses', application_id=claim.id, input_revision_id=revision['id'],
                          input_fingerprint=materials.revision_fingerprint(conn, revision), score_rule_version_id=rule['id'],
                          model_name='old-direct', prompt_version='old', status='SUCCEEDED', result_json={'suggestion': {'title': '迎新夜跑活动'}})
        db.change(conn, 'application_revisions', revision['id'], ai_analysis_id=analysis['id'])
        page = db.rows(conn, 'file_pages')[0]
        db.add(conn, 'analysis_inputs', analysis_id=analysis['id'], file_id=page['file_id'], page_id=page['id'], input_kind='PAGE', input_hash=page['render_sha256'], sort_order=0)
        db.change(conn, 'background_jobs', job['id'], state='SUCCEEDED', finished_at=db.now(), result_json={**job['result_json'], 'status': 'SUCCEEDED', 'processed_page_ids': [page['id']]})
    worker = jobs.JobWorker()
    worker.start()
    try:
        time.sleep(.25)
        assert queue.records('AI') == []
    finally:
        worker.stop()


def test_legacy_sync_endpoint_cannot_be_a_fourth_background_task(queue, monkeypatch):
    claim = queue.item()
    response = queue.clients['student'].post(f'/api/applications/{claim.id}/analyze', json={})
    assert response.status_code == 409
    assert queue.records('OCR')[0]['state'] == 'QUEUED'


def test_old_unstarted_ai_snapshot_does_not_suppress_automatic_current_human_draft(queue, monkeypatch):
    claim = queue.item()
    old = queue.clients['student'].post(f'/api/applications/{claim.id}/jobs', json={'kind': 'AI'}).json()['job']
    changed = queue.clients['student'].put(f'/api/applications/{claim.id}', json={'title': '学生新填写的迎新夜跑活动', 'requested_score': '.73'})
    assert changed.status_code == 200
    monkeypatch.setattr(materials, 'tencent_ocr', cloud_answer)
    monkeypatch.setattr(materials, 'deepseek_suggestion', model_answer)
    worker = jobs.JobWorker()
    worker.start()
    try:
        wait_until(lambda: len(queue.records('AI', 'SUCCEEDED')) == 1)
        with db.read() as conn:
            assert db.one(conn, 'background_jobs', old['id'])['state'] == 'STALE'
        current = queue.records('AI', 'SUCCEEDED')[0]
        assert current['id'] != old['id'] and current['result_json']['automatically_applied'] is False
        detail = queue.clients['student'].get(f'/api/applications/{claim.id}').json()
        assert detail['title'] == '学生新填写的迎新夜跑活动' and detail['requested_score'] == '0.73'
    finally:
        worker.stop()


def test_live_worker_with_old_heartbeat_keeps_its_file_leases(queue):
    claim = queue.item()
    token = f'{os.getpid()}:live-other-worker'
    with db.write() as conn:
        db.change(conn, 'background_jobs', claim.ocr['id'], state='RUNNING', worker_token=token, attempts=1,
                  heartbeat_at='2000-01-01T00:00:00+00:00', result_json={**claim.ocr['result_json'], 'active_file_ids': ['still-processing']})
    worker = jobs.JobWorker()
    worker.start()
    try:
        time.sleep(.15)
        record = queue.records('OCR')[0]
        assert record['state'] == 'RUNNING' and record['worker_token'] == token
        assert record['attempts'] == 1 and record['result_json']['active_file_ids'] == ['still-processing']
    finally:
        worker.stop()


def test_live_cache_reservation_is_not_duplicated_after_three_minutes(queue, monkeypatch):
    queue.item()
    with db.write() as conn:
        page = db.rows(conn, 'file_pages')[0]
        param_hash = hashlib.sha256((page['render_sha256'] + 'GeneralAccurateOCR' + 'v2-words').encode()).hexdigest()
        reserved = db.add(conn, 'recognition_runs', page_id=page['id'], provider='TENCENT', api_name='GeneralAccurateOCR',
                          parameters_hash=param_hash, status='RUNNING', result_json={'worker_pid': os.getpid()},
                          started_at='2000-01-01T00:00:00+00:00')
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(materials._ocr_page, page, 'GeneralAccurateOCR', queue.config)
        time.sleep(.15)
        assert not future.done()
        with db.write() as conn:
            result = cloud_answer('GeneralAccurateOCR')
            db.change(conn, 'recognition_runs', reserved['id'], status='SUCCEEDED', result_json=result, finished_at=db.now())
            materials._save_regions(conn, reserved, result)
        assert future.result(timeout=4)['id'] == reserved['id']
    with db.read() as conn:
        assert len(db.rows(conn, 'recognition_runs')) == 1


def test_lost_worker_token_cannot_write_progress_or_finish_replacement_job(queue, monkeypatch):
    claim = queue.item()
    entered, release = threading.Event(), threading.Event()
    calls = []

    def provider(action, image, config):
        calls.append(action)
        entered.set()
        assert release.wait(8)
        return cloud_answer(action)

    monkeypatch.setattr(materials, 'tencent_ocr', provider)
    worker = jobs.JobWorker()
    worker.start()
    try:
        assert entered.wait(4)
        with db.write() as conn:
            current = db.one(conn, 'background_jobs', claim.ocr['id'])
            db.change(conn, 'background_jobs', current['id'], worker_token='replacement:token', stage='新任务保留',
                      result_json={**current['result_json'], 'active_file_ids': []})
        release.set()
        wait_until(lambda: not any(worker.futures.values()))
        record = queue.records('OCR')[0]
        assert record['state'] == 'RUNNING' and record['worker_token'] == 'replacement:token' and record['stage'] == '新任务保留'
        assert queue.records('AI') == [] and calls == ['GeneralAccurateOCR']
    finally:
        release.set()
        worker.stop()
