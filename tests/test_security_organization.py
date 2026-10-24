"""HTTP-level identity, RBAC and organization regression tests."""
import pytest
from fastapi.testclient import TestClient
from app import db
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(storage_dir=tmp_path)) as result:
        yield result


def login(client, username):
    client.headers.pop('X-CSRF-Token', None)
    response = client.post('/api/login', json={'username': username, 'password': 'Demo@2026'})
    assert response.status_code == 200, response.text
    client.headers['X-CSRF-Token'] = response.json()['csrf_token']
    return client.get('/api/bootstrap').json()


def test_real_login_csrf_and_password_rotation(client):
    assert client.get('/api/bootstrap').status_code == 401
    boot = login(client, 'student')
    assert boot['user']['base_identity'] == 'STUDENT'
    assert 'password_hash' not in str(boot)
    token = client.headers.pop('X-CSRF-Token')
    assert client.post('/api/logout').status_code == 403
    client.headers['X-CSRF-Token'] = token
    bad_origin = client.post('/api/logout', headers={'Origin': 'https://unrelated.invalid'})
    assert bad_origin.status_code == 403
    old_cookie = client.cookies.get('xjtu_session')
    updated = client.post('/api/password', json={'current_password': 'Demo@2026', 'new_password': 'NewPassword@2026'})
    assert updated.status_code == 200
    client.cookies.clear()
    client.cookies.set('xjtu_session', old_cookie)
    assert client.get('/api/bootstrap').status_code == 401


def test_members_hide_other_numbers_and_scope(client):
    boot = login(client, 'student')
    year, classroom = boot['years'][0]['id'], boot['classes'][0]['id']
    result = client.get(f'/api/classes/{classroom}/members', params={'year_id': year})
    assert result.status_code == 200
    for person in result.json()['members']:
        if person['user_id'] != boot['user']['id']:
            assert 'student_number' not in person
    with db.read() as conn:
        other = db.one(conn, 'classes', name='软件2402')
    assert client.get(f"/api/classes/{other['id']}/members", params={'year_id': year}).status_code == 403
    assert client.get('/api/admin/users').status_code == 403


def test_two_distinct_reviewers_and_account_disable_guard(client):
    boot = login(client, 'monitor')
    classroom = boot['classes'][0]['id']
    with db.read() as conn:
        first = db.one(conn, 'users', username='reviewer1')['id']
        second = db.one(conn, 'users', username='reviewer2')['id']
    assert client.put(f'/api/classes/{classroom}/reviewers', json={'user_ids': [first, first]}).status_code == 400
    assert client.put(f'/api/classes/{classroom}/reviewers', json={'user_ids': [first, second]}).status_code == 200
    login(client, 'admin')
    assert client.put(f'/api/admin/users/{first}', json={'status': 'DISABLED'}).status_code == 400
    users = client.get('/api/admin/users').json()
    assert next(p for p in users if p['id'] == first)['status'] == 'ACTIVE'
    assert all('password_hash' not in p for p in users)


def test_malicious_admin_matrix_cannot_open_business_permissions(client):
    login(client, 'admin')
    matrix = client.get('/api/admin/roles').json()
    admin = next(r for r in matrix['roles'] if r['code'] == 'ADMIN')
    original = next(p for p in matrix['permissions'] if p['code'] == 'materials.read')
    updated = client.put(f"/api/admin/roles/{admin['id']}/permissions", json={'permissions': [
        {'permission_id': original['id'], 'is_allowed': True, 'data_scope': 'SYSTEM_METADATA'}]})
    assert updated.status_code == 200
    with db.read() as conn:
        from app.security import allowed
        user = db.one(conn, 'users', username='admin')
        assert not allowed(conn, user, 'materials.read')
    assert client.get('/api/applications').status_code == 403


def test_csv_atomic_import_and_setup_class_activation(client):
    boot = login(client, 'admin')
    classroom = boot['classes'][0]['id']
    content = f'username,display_name,base_identity,password,student_number,class_id\nnewuser,新同学,STUDENT,Demo@2026,DEMO9001,{classroom}\nstudent,重复用户,STUDENT,Demo@2026,DEMO9002,{classroom}\n'
    result = client.post('/api/admin/import', files={'file': ('users.csv', content.encode(), 'text/csv')})
    assert result.status_code == 409
    assert not any(p['username'] == 'newuser' for p in client.get('/api/admin/users').json())
    newclass = client.post('/api/admin/classes', json={'name': '测试2501', 'code': 'TEST-CLASS'}).json()
    assert newclass['status'] == 'SETUP'
    assert client.put(f"/api/admin/classes/{newclass['id']}/assignments", json={'monitor_id': 'no', 'reviewer_ids': []}).status_code == 400


def test_current_year_defaults_but_missing_identifiers_never_resolve_first_record(client):
    boot = login(client, 'student')
    category = boot['categories'][0]['id']
    defaulted = client.post('/api/applications', json={'category_id': category})
    assert defaulted.status_code == 200
    assert defaulted.json()['year_id'] == boot['active_year']['id']
    assert client.post('/api/applications', json={'category_id': category, 'year_id': 'does-not-exist'}).status_code in (400, 404)
    assert client.post('/api/applications', json={}).status_code in (400, 404)
    assert client.get('/api/applications/not-a-real-id').status_code == 404
    with db.read() as conn:
        assert db.one(conn, 'users') is None
        assert list(conn.exec_driver_sql('PRAGMA foreign_key_check')) == []


def test_matrix_cannot_turn_monitor_into_reviewer(client):
    boot = login(client, 'admin')
    matrix = client.get('/api/admin/roles').json()
    monitor = next(r for r in matrix['roles'] if r['code'] == 'MONITOR')
    permission = next(p for p in matrix['permissions'] if p['code'] == 'review.peer')
    assert client.put(f"/api/admin/roles/{monitor['id']}/permissions", json={'permissions': [
        {'permission_id': permission['id'], 'is_allowed': True, 'data_scope': 'CLASS'}]}).status_code == 200
    with db.read() as conn:
        from app.security import allowed
        user = db.one(conn, 'users', username='monitor')
        owner = db.one(conn, 'users', username='student')
        classroom = db.one(conn, 'classes', name='软件2401')
        assert not allowed(conn, user, 'review.peer', classroom['id'], owner['id'])
