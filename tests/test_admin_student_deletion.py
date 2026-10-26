"""Student deletion against temporary databases and synthetic files, never cloud."""
from contextlib import closing
import io
import os
import sqlite3

from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from app import admin_account_deletion, db, jobs, materials
from app.main import create_app


@pytest.fixture
def system(tmp_path, monkeypatch):
    monkeypatch.setenv('CLOUD_ANALYSIS_ENABLED', 'false')
    monkeypatch.setattr(materials, 'tencent_ocr', lambda *args: pytest.fail('real OCR forbidden'))
    monkeypatch.setattr(materials, 'deepseek_suggestion', lambda *args: pytest.fail('real model forbidden'))
    app = create_app(storage_dir=tmp_path / 'storage', database_url='sqlite:///' + (tmp_path / 'test.db').as_posix(), start_worker=False)
    clients = {}
    with TestClient(app):
        def login(name):
            if name not in clients:
                client = TestClient(app)
                response = client.post('/api/login', json={'username': name, 'password': 'Demo@2026'})
                assert response.status_code == 200, response.text
                client.headers['X-CSRF-Token'] = response.json()['csrf_token']
                clients[name] = client
            return clients[name]
        yield app, login
    for client in clients.values():
        client.close()
    db.engine.dispose()


def user(name):
    with db.read() as conn:
        return db.one(conn, 'users', username=name)


def profile(name):
    with db.read() as conn:
        return db.one(conn, 'student_profiles', user_id=db.one(conn, 'users', username=name)['id'])


def draft(client, category='COLLECTIVE', color='blue'):
    boot = client.get('/api/bootstrap').json()
    category_id = next(item['id'] for item in boot['categories'] if item['code'] == category)
    response = client.post('/api/applications', json={'category_id': category_id})
    assert response.status_code == 200, response.text
    application = response.json()
    data = io.BytesIO()
    Image.new('RGB', (220, 300), color).save(data, 'PNG')
    uploaded = client.post(f"/api/applications/{application['id']}/files", files={'file': ('synthetic-proof.png', data.getvalue(), 'image/png')})
    assert uploaded.status_code == 200, uploaded.text
    response = client.put(f"/api/applications/{application['id']}", json={'title': '合成青禾创新活动', 'requested_score': '0.30'})
    assert response.status_code == 200, response.text
    return response.json(), uploaded.json()


def approve(client, application_id):
    response = client.post(f'/api/applications/{application_id}/review', json={'action': 'APPROVE', 'idempotency_key': db.uid()})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize('identity', ['student', 'monitor', 'counselor'])
def test_only_admin_can_delete_students(system, identity):
    _, login = system
    target = user('student2')
    assert login(identity).delete('/api/admin/users/' + target['id']).status_code == 403
    assert user('student2')['id'] == target['id']


def test_delete_requires_login_and_csrf_and_never_deletes_counselor_or_admin(system):
    app, login = system
    student_id = user('student2')['id']
    with TestClient(app) as anonymous:
        assert anonymous.delete('/api/admin/users/' + student_id).status_code == 401
    admin = login('admin')
    token = admin.headers.pop('X-CSRF-Token')
    assert admin.delete('/api/admin/users/' + student_id).status_code == 403
    admin.headers['X-CSRF-Token'] = token
    for name in ('admin', 'counselor'):
        assert admin.delete('/api/admin/users/' + user(name)['id']).status_code == 403
    assert admin.delete('/api/admin/users/missing-account').status_code == 404


def test_plain_student_hard_delete_backs_up_and_releases_login_number(system):
    _, login = system
    student = login('student2')
    old_cookie = student.cookies.get('xjtu_session')
    target, student_profile = user('student2'), profile('student2')
    boot = student.get('/api/bootstrap').json()
    with db.read() as conn:
        other_users = {row['id']: row for row in db.rows(conn, 'users') if row['id'] != target['id']}
    admin = login('admin')
    # Logging the administrator in legitimately updates its last_login_at.
    with db.read() as conn:
        other_users = {row['id']: row for row in db.rows(conn, 'users') if row['id'] != target['id']}
    response = admin.delete('/api/admin/users/' + target['id'])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['preserved_audit'] is False
    backup = db.storage_root / 'backups' / body['backup']
    assert backup.is_file()
    with closing(sqlite3.connect(backup)) as conn:
        assert conn.execute('SELECT username FROM users WHERE id=?', (target['id'],)).fetchone()[0] == 'student2'
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    with db.read() as conn:
        assert db.one(conn, 'users', target['id']) is None
        assert db.one(conn, 'student_profiles', student_profile['id']) is None
        assert {row['id']: row for row in db.rows(conn, 'users')} == other_users
        assert not list(conn.exec_driver_sql('PRAGMA foreign_key_check'))
    assert not any(row['id'] == target['id'] for row in admin.get('/api/admin/users').json())
    assert student.get('/api/bootstrap').status_code == 401
    created = admin.post('/api/admin/users', json={'username': 'student2', 'display_name': '重新演示同学',
        'base_identity': 'STUDENT', 'password': 'Demo@2026', 'student_number': student_profile['student_number'], 'class_id': boot['classes'][0]['id']})
    assert created.status_code == 200, created.text
    assert created.json()['id'] != target['id']
    student.cookies.clear()
    student.cookies.set('xjtu_session', old_cookie)
    assert student.get('/api/bootstrap').status_code == 401
    assert student.post('/api/login', json={'username': 'student2', 'password': 'Demo@2026'}).status_code == 200


def test_uploaded_approved_chain_is_deleted_but_shared_activity_and_budget_survive(system):
    _, login = system
    student, other = login('student2'), login('student')
    target = user('student2')
    application, uploaded = draft(student, 'RESEARCH')
    unrelated, unrelated_file = draft(other, color='green')
    sent = student.post(f"/api/applications/{application['id']}/submit", json={})
    assert sent.status_code == 200, sent.text
    pending = approve(login('reviewer1'), application['id'])
    assert pending['state'] == 'PENDING_COUNSELOR'
    approved = approve(login('counselor'), application['id'])
    assert approved['state'] == 'APPROVED'
    activity_id = approved['activity']['id']
    with db.write() as conn:
        page_id = uploaded['pages'][0]['id']
        run = db.add(conn, 'recognition_runs', page_id=page_id, provider='TENCENT', api_name='GeneralAccurateOCR',
                     parameters_hash=db.uid(), status='FAILED', started_at=db.now(), finished_at=db.now(), result_json={})
        db.add(conn, 'recognition_runs', page_id=page_id, provider='TENCENT', api_name='SealOCR',
               parameters_hash=db.uid(), status='SUCCEEDED', started_at=db.now(), finished_at=db.now(), result_json={})
        region = db.add(conn, 'recognition_regions', run_id=run['id'], kind='TEXT', text='合成材料', polygon_json=[0, 0, 100, 20])
        revision = db.one(conn, 'application_revisions', application['revision']['id'])
        rule = db.rows(conn, 'score_rule_versions')[0]
        analysis = db.add(conn, 'ai_analyses', application_id=application['id'], input_revision_id=revision['id'],
                          input_fingerprint='synthetic', score_rule_version_id=rule['id'], status='SUCCEEDED')
        db.add(conn, 'analysis_inputs', analysis_id=analysis['id'], file_id=uploaded['id'], page_id=page_id,
               recognition_run_id=run['id'], input_kind='OCR_TEXT', input_hash='synthetic')
        db.add(conn, 'evidence_crops', revision_id=revision['id'], page_id=page_id, recognition_region_id=region['id'],
               evidence_type='PARTICIPATION', source='AI', polygon_json=[0, 0, 100, 20], private_asset_uri='private-crops/synthetic.png', public_asset_status='PENDING')
        recognition = db.one(conn, 'activity_eligibility_versions', approved['round']['eligibility_version_id'])
        activity_before = db.one(conn, 'activities', activity_id)
        unrelated_before = db.one(conn, 'applications', unrelated['id'])
        usage = {api: materials._usage(conn, api) for api in ('GeneralAccurateOCR', 'SealOCR')}
    response = login('admin').delete('/api/admin/users/' + target['id'])
    assert response.status_code == 200, response.text
    assert response.json()['deleted_counts']['applications'] == 1
    with db.read() as conn:
        assert db.one(conn, 'applications', application['id']) is None
        assert db.one(conn, 'file_objects', uploaded['id']) is None
        assert db.one(conn, 'applications', unrelated['id']) == unrelated_before
        assert db.one(conn, 'file_objects', unrelated_file['id']) is not None
        assert db.one(conn, 'activities', activity_id) == activity_before
        preserved = db.one(conn, 'activity_eligibility_versions', recognition['id'])
        assert preserved == {**recognition, 'source_review_decision_id': None}
        assert {api: materials._usage(conn, api) for api in usage} == usage
        assert not list(conn.exec_driver_sql('PRAGMA foreign_key_check'))
    assert other.get('/api/applications/' + unrelated['id']).status_code == 200
    assert other.get('/api/files/' + uploaded['id'] + '/original').status_code == 404


@pytest.mark.parametrize('name', ['monitor', 'reviewer1', 'reviewer2'])
def test_last_monitor_and_required_reviewers_need_handover(system, name):
    _, login = system
    target = user(name)
    response = login('admin').delete('/api/admin/users/' + target['id'])
    assert response.status_code == 409, response.text
    with db.read() as conn:
        assert db.one(conn, 'users', target['id'])['status'] == 'ACTIVE'
        assert db.one(conn, 'system_settings', 'task_control:' + target['id']) is None


def test_reviewing_other_students_keeps_anonymous_audit_and_releases_identity(system):
    _, login = system
    target, student_profile = user('reviewer1'), profile('reviewer1')
    monitor = login('monitor')
    classroom = monitor.get('/api/bootstrap').json()['classes'][0]['id']
    ids = [user(name)['id'] for name in ('reviewer1', 'reviewer2', 'student2')]
    assert monitor.put(f'/api/classes/{classroom}/reviewers', json={'user_ids': ids}).status_code == 200
    application, _ = draft(login('student'))
    assert login('student').post(f"/api/applications/{application['id']}/submit", json={}).status_code == 200
    approved = approve(login('reviewer1'), application['id'])
    with db.read() as conn:
        review = db.one(conn, 'review_decisions', round_id=approved['round']['id'])
        round_before = db.one(conn, 'submission_rounds', approved['round']['id'])
    admin = login('admin')
    response = admin.delete('/api/admin/users/' + target['id'])
    assert response.status_code == 200, response.text
    assert response.json()['preserved_audit'] is True
    with db.read() as conn:
        tombstone = db.one(conn, 'users', target['id'])
        assert tombstone['status'] == 'DELETED' and tombstone['display_name'] == '已删除学生'
        assert tombstone['username'] != 'reviewer1'
        assert db.one(conn, 'review_decisions', review['id']) == review
        assert db.one(conn, 'submission_rounds', round_before['id']) == round_before
        assert db.one(conn, 'role_assignments', review['role_assignment_id'])['status'] == 'ENDED'
        assert not list(conn.exec_driver_sql('PRAGMA foreign_key_check'))
    assert not any(row['id'] == target['id'] for row in admin.get('/api/admin/users').json())
    assert admin.put('/api/admin/users/' + target['id'], json={'status': 'ACTIVE'}).status_code == 404
    created = admin.post('/api/admin/users', json={'username': 'reviewer1', 'display_name': '新演示学生',
        'base_identity': 'STUDENT', 'password': 'Demo@2026', 'student_number': student_profile['student_number'], 'class_id': classroom})
    assert created.status_code == 200, created.text
    assert login('reviewer1').get('/api/bootstrap').status_code == 401


def test_running_job_pause_is_committed_before_409_and_retry_clears_it(system):
    _, login = system
    student = login('student2')
    target = user('student2')
    application, _ = draft(student)
    response = student.post(f"/api/applications/{application['id']}/jobs", json={'kind': 'OCR'})
    assert response.status_code == 200, response.text
    job_id = response.json()['job']['id']
    token = f'{os.getpid()}:synthetic-worker'
    with db.write() as conn:
        db.change(conn, 'background_jobs', job_id, state='RUNNING', worker_token=token)
    admin = login('admin')
    response = admin.delete('/api/admin/users/' + target['id'])
    assert response.status_code == 409, response.text
    with db.read() as conn:
        control = db.one(conn, 'system_settings', 'task_control:' + target['id'])['value_json']
        job = db.one(conn, 'background_jobs', job_id)
        assert control['paused'] and control['reason'] == 'ADMIN_DELETE'
        assert job['state'] == 'RUNNING' and job['worker_token'] == token
        assert job['result_json']['pause_requested']
        assert db.one(conn, 'users', target['id']) is not None
    with db.write() as conn:
        db.change(conn, 'background_jobs', job_id, state='PAUSED', worker_token=None)
    assert admin.delete('/api/admin/users/' + target['id']).status_code == 200
    with db.read() as conn:
        assert db.one(conn, 'background_jobs', job_id) is None


def test_legacy_synchronous_call_also_delays_deletion(system):
    _, login = system
    target = user('student2')
    with db.write() as conn:
        db.add(conn, 'system_settings', id='synchronous-analysis:' + target['id'], value_json={'active': True, 'pid': os.getpid()})
    admin = login('admin')
    assert admin.delete('/api/admin/users/' + target['id']).status_code == 409
    with db.write() as conn:
        db.change(conn, 'system_settings', 'synchronous-analysis:' + target['id'], value_json={'active': False})
    assert admin.delete('/api/admin/users/' + target['id']).status_code == 200


def test_backup_failure_leaves_account_and_materials_intact(system, monkeypatch):
    _, login = system
    target = user('student2')
    application, uploaded = draft(login('student2'))
    def broken_backup():
        raise HTTPException(503, 'synthetic backup failure')
    monkeypatch.setattr(admin_account_deletion, '_backup_database', broken_backup)
    assert login('admin').delete('/api/admin/users/' + target['id']).status_code == 503
    with db.read() as conn:
        assert db.one(conn, 'users', target['id'])['status'] == 'ACTIVE'
        assert db.one(conn, 'applications', application['id']) is not None
        assert db.one(conn, 'file_objects', uploaded['id']) is not None


def test_shared_file_is_not_deleted_from_another_students_history(system):
    _, login = system
    target = user('student2')
    application, uploaded = draft(login('student2'))
    other = login('student')
    boot = other.get('/api/bootstrap').json()
    other_app = other.post('/api/applications', json={'category_id': boot['categories'][0]['id']}).json()
    with db.write() as conn:
        reference = db.add(conn, 'revision_files', revision_id=other_app['revision']['id'], file_id=uploaded['id'], added_by=boot['user']['id'])
    response = login('admin').delete('/api/admin/users/' + target['id'])
    assert response.status_code == 200, response.text
    assert response.json()['preserved_audit']
    with db.read() as conn:
        assert db.one(conn, 'applications', application['id']) is None
        assert db.one(conn, 'file_objects', uploaded['id']) is not None
        assert db.one(conn, 'revision_files', reference['id']) == reference
    assert other.get('/api/files/' + uploaded['id'] + '/original').status_code == 200
