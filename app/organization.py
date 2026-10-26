"""Authentication and scoped organization / administrator API."""
from __future__ import annotations

import csv
import io
import os
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from . import db
from .security import (COOKIE, active_grants, allowed, check_password, csrf_token, current_user,
                       make_session, own_class_ids, password_hash, public_user, require,
                       resolve_session)
from .seed import DEMO_ACCOUNTS, DEMO_PASSWORD

router = APIRouter(prefix='/api')
login_attempts = defaultdict(deque)


def demo_enabled():
    return os.getenv('DEMO_MODE', 'true').lower() in ('true', '1', 'yes')


@router.get('/demo-accounts')
def demo_accounts():
    return {'accounts': DEMO_ACCOUNTS, 'password': DEMO_PASSWORD} if demo_enabled() else {'accounts': []}


@router.get('/session')
def session(request: Request):
    user = resolve_session(request)
    if not user:
        return {'user': None}
    with db.read() as conn:
        return {'user': public_user(conn, user), 'csrf_token': csrf_token(request.cookies[COOKIE])}


@router.post('/login')
def login(data: dict, request: Request, response: Response):
    username = str(data.get('username', '')).strip()[:80]
    password = str(data.get('password', ''))[:300]
    key = ((request.client.host if request.client else ''), username)
    attempts = login_attempts[key]
    while attempts and attempts[0] < time.monotonic() - 300:
        attempts.popleft()
    if len(attempts) >= 10:
        raise HTTPException(429, '尝试次数过多，请五分钟后重试')
    with db.write() as conn:
        user = db.one(conn, 'users', username=username)
        if not user or user['status'] != 'ACTIVE' or not check_password(password, user['password_hash']):
            attempts.append(time.monotonic())
            raise HTTPException(401, '账号或密码错误，或账号已停用')
        attempts.clear()
        db.change(conn, 'users', user['id'], last_login_at=db.now())
        token = make_session(user)
        response.set_cookie(COOKIE, token, httponly=True, samesite='strict', max_age=43200,
                            secure=os.getenv('COOKIE_SECURE', 'false').lower() == 'true')
        return {'user': public_user(conn, user), 'csrf_token': csrf_token(token)}


@router.post('/logout')
def logout(response: Response, request: Request):
    user = resolve_session(request)
    if user and user['base_identity'] == 'STUDENT':
        from .jobs import pause_owner
        with db.write() as conn:
            pause_owner(conn, user['id'], 'LOGOUT')
    response.delete_cookie(COOKIE)
    return {'ok': True}


def validate_password(value):
    if not isinstance(value, str) or not 8 <= len(value) <= 128:
        raise HTTPException(400, '密码长度须为8至128位')
    return value


@router.post('/password')
def update_password(data: dict, response: Response, user=Depends(current_user)):
    if not check_password(str(data.get('current_password', '')), user['password_hash']):
        raise HTTPException(400, '当前密码不正确')
    with db.write() as conn:
        updated = db.change(conn, 'users', user['id'], password_hash=password_hash(validate_password(data.get('new_password'))))
        token = make_session(updated)
        response.set_cookie(COOKIE, token, httponly=True, samesite='strict', max_age=43200,
                            secure=os.getenv('COOKIE_SECURE', 'false').lower() == 'true')
    return {'ok': True, 'csrf_token': csrf_token(token)}


@router.get('/bootstrap')
def bootstrap(user=Depends(current_user)):
    from .migrations import active_year
    with db.read() as conn:
        visible = own_class_ids(conn, user)
        classes = [c for c in db.rows(conn, 'classes') if user['base_identity'] == 'ADMIN' or c['id'] in visible]
        permissions = [p['code'] for p in db.rows(conn, 'permissions') if allowed(conn, user, p['code'])]
        year = active_year(conn)
        return {'user': public_user(conn, user), 'years': sorted(db.rows(conn, 'academic_years'), key=lambda y: y['sort_order']),
                'active_year': year, 'current_year': year,
                'classes': classes, 'categories': sorted(db.rows(conn, 'score_categories'), key=lambda c: c['sort_order']),
                'permissions': permissions, 'demo_accounts': DEMO_ACCOUNTS if demo_enabled() else []}


@router.get('/admin/statistics-year')
def statistics_year(user=Depends(current_user)):
    from .migrations import active_year
    with db.read() as conn:
        require(conn, user, 'admin.classes')
        return {'active_year': active_year(conn), 'years': sorted(db.rows(conn, 'academic_years'), key=lambda y: y['sort_order'])}


@router.put('/admin/statistics-year')
def set_statistics_year(data: dict, user=Depends(current_user)):
    from .migrations import active_year
    with db.write() as conn:
        require(conn, user, 'admin.classes')
        year = db.one(conn, 'academic_years', data.get('year_id'))
        if not year or not year['is_selectable']:
            raise HTTPException(400, '请选择有效统计学年')
        previous = active_year(conn)
        setting = db.one(conn, 'system_settings', 'active_academic_year')
        history = ((setting or {}).get('value_json') or {}).get('changes', [])
        value = {'year_id': year['id'], 'changes': (history + [{'from': previous['id'], 'to': year['id'], 'by': user['id'], 'at': db.now()}])[-100:]}
        if setting:
            db.change(conn, 'system_settings', setting['id'], value_json=value, changed_by=user['id'])
        else:
            db.add(conn, 'system_settings', id='active_academic_year', value_json=value, changed_by=user['id'])
        return {'active_year': year, 'message': '当前统计学年已更新；原学年记录保留，待审项仍可审核'}


@router.post('/admin/academic-years')
def create_academic_year(data: dict, user=Depends(current_user)):
    import re
    code = str(data.get('code', '')).strip()
    if not re.fullmatch(r'\d{4}-\d{4}', code) or int(code[5:]) != int(code[:4]) + 1:
        raise HTTPException(400, '学年代码须为连续年度，例如2027-2028')
    with db.write() as conn:
        require(conn, user, 'admin.classes')
        if db.one(conn, 'academic_years', code=code):
            raise HTTPException(409, '该学年已存在')
        prior_years = sorted(db.rows(conn, 'academic_years'), key=lambda y: y['sort_order'])
        year = db.add(conn, 'academic_years', code=code, name=str(data.get('name') or code+'学年')[:40],
                      sort_order=max((y['sort_order'] for y in prior_years), default=-1)+1, is_selectable=True)
        rule = db.rows(conn, 'score_rule_versions', status='PUBLISHED')[-1]
        # Copy the most recent class assignment per student, never revive past classes.
        latest = {}
        ordering = {y['id']: y['sort_order'] for y in prior_years}
        for enrollment in db.rows(conn, 'enrollments', status='ACTIVE'):
            cy = db.one(conn, 'class_years', enrollment['class_year_id'])
            order = ordering.get(cy['academic_year_id'], -1)
            if enrollment['student_id'] not in latest or order > latest[enrollment['student_id']][0]:
                latest[enrollment['student_id']] = (order, cy['class_id'])
        for classroom in db.rows(conn, 'classes'):
            cy = db.add(conn, 'class_years', class_id=classroom['id'], academic_year_id=year['id'], score_rule_version_id=rule['id'])
            db.add(conn, 'class_publicity', class_year_id=cy['id'], is_open=False)
            for student_id, (_, class_id) in latest.items():
                if class_id == classroom['id']:
                    db.add(conn, 'enrollments', student_id=student_id, class_year_id=cy['id'], managed_by=user['id'])
        return year


def class_year(conn, class_id, year_id):
    cy = db.one(conn, 'class_years', class_id=class_id, academic_year_id=year_id)
    if not cy:
        raise HTTPException(404, '班级学年不存在')
    return cy


def role_codes(conn, user_id, class_id=None):
    user = db.one(conn, 'users', user_id)
    if not user or user['status'] != 'ACTIVE':
        return []
    return [role['code'] for grant, role in active_grants(conn, user)
            if grant['class_id'] is None or grant['class_id'] == class_id]


def privileged_class(conn, user, class_id):
    return user['base_identity'] in ('COUNSELOR', 'ADMIN') or any(
        r in role_codes(conn, user['id'], class_id) for r in ('MONITOR', 'REVIEWER'))


@router.get('/classes/{class_id}/members')
def members(class_id: str, year_id: str, user=Depends(current_user)):
    with db.read() as conn:
        require(conn, user, 'organization.read', class_id)
        cy = class_year(conn, class_id, year_id)
        if user['base_identity'] == 'STUDENT' and not privileged_class(conn, user, class_id):
            profile = db.one(conn, 'student_profiles', user_id=user['id'])
            if not profile or not db.one(conn, 'enrollments', student_id=profile['id'], class_year_id=cy['id'], status='ACTIVE'):
                raise HTTPException(403, '只能查看本人所属学年班级的成员')
        result = []
        for e in db.rows(conn, 'enrollments', class_year_id=cy['id'], status='ACTIVE'):
            student = db.one(conn, 'student_profiles', e['student_id'])
            person = db.one(conn, 'users', student['user_id'])
            if person['status'] != 'ACTIVE':
                continue
            entry = {'id': student['id'], 'user_id': person['id'], 'display_name': person['display_name'],
                     'roles': role_codes(conn, person['id'], class_id)}
            if privileged_class(conn, user, class_id) or person['id'] == user['id']:
                entry['student_number'] = student['student_number']
            result.append(entry)
        return {'class': db.one(conn, 'classes', class_id), 'year': db.one(conn, 'academic_years', year_id),
                'members': result, 'publicity': db.one(conn, 'class_publicity', class_year_id=cy['id'])}


def students_of_class(conn, class_id):
    ids = set()
    years = db.rows(conn, 'academic_years')
    current_year = max(years, key=lambda y: y['sort_order'])['id'] if years else None
    for cy in db.rows(conn, 'class_years', class_id=class_id):
        if cy['academic_year_id'] != current_year:
            continue
        for e in db.rows(conn, 'enrollments', class_year_id=cy['id'], status='ACTIVE'):
            student = db.one(conn, 'student_profiles', e['student_id'])
            person = db.one(conn, 'users', student['user_id'])
            if person['status'] == 'ACTIVE' and student['profile_status'] == 'ACTIVE':
                ids.add(person['id'])
    return ids


def replace_class_role(conn, class_id, rolecode, user_ids, actor):
    role = db.one(conn, 'roles', code=rolecode)
    for grant in db.rows(conn, 'role_assignments', role_id=role['id'], class_id=class_id, status='ACTIVE'):
        db.change(conn, 'role_assignments', grant['id'], status='ENDED', valid_to=db.now())
    for user_id in set(user_ids):
        db.add(conn, 'role_assignments', user_id=user_id, role_id=role['id'], class_id=class_id, granted_by=actor['id'])


def ensure_staffing(conn):
    for classroom in db.rows(conn, 'classes', status='ACTIVE'):
        students = students_of_class(conn, classroom['id'])
        monitors, reviewers = set(), set()
        for grant in db.rows(conn, 'role_assignments', class_id=classroom['id'], status='ACTIVE'):
            if grant['user_id'] not in students:
                continue
            code = db.one(conn, 'roles', grant['role_id'])['code']
            if code == 'MONITOR':
                monitors.add(grant['user_id'])
            if code == 'REVIEWER':
                reviewers.add(grant['user_id'])
        if len(monitors) != 1 or len(reviewers) < 2:
            raise HTTPException(400, f"{classroom['name']}须有一名班长和至少两名不同的有效审查员，请先完成职责交接")


@router.put('/classes/{class_id}/reviewers')
def change_reviewers(class_id: str, data: dict, user=Depends(current_user)):
    targets = data.get('user_ids', [])
    if not isinstance(targets, list) or len(set(targets)) < 2:
        raise HTTPException(400, '每班至少需要两名不同的学生审查员')
    with db.write() as conn:
        require(conn, user, 'reviewers.manage', class_id)
        if 'MONITOR' not in role_codes(conn, user['id'], class_id):
            raise HTTPException(403, '仅本班班长可管理审查员')
        if not set(targets) <= students_of_class(conn, class_id):
            raise HTTPException(400, '审查员须为本班有效学生')
        replace_class_role(conn, class_id, 'REVIEWER', targets, user)
        ensure_staffing(conn)
    return {'ok': True}


def user_entry(conn, person):
    entry = {k: v for k, v in person.items() if k != 'password_hash'}
    entry['roles'] = public_user(conn, person)['roles']
    profile = db.one(conn, 'student_profiles', user_id=person['id'])
    entry['student_number'] = profile['student_number'] if profile else None
    entry['class_ids'] = list(own_class_ids(conn, person))
    if profile:
        years = {y['id']: y['sort_order'] for y in db.rows(conn, 'academic_years')}
        entries = sorted(db.rows(conn, 'enrollments', student_id=profile['id'], status='ACTIVE'),
                         key=lambda e: years[db.one(conn, 'class_years', e['class_year_id'])['academic_year_id']], reverse=True)
        entry['class_ids'] = list(dict.fromkeys(db.one(conn, 'class_years', e['class_year_id'])['class_id'] for e in entries))
        entry['current_class_id'] = entry['class_ids'][0] if entry['class_ids'] else None
        entry['class_by_year'] = {db.one(conn, 'class_years', e['class_year_id'])['academic_year_id']:
                                  db.one(conn, 'class_years', e['class_year_id'])['class_id'] for e in entries}
    entry['counselor_class_ids'] = entry['class_ids'] if person['base_identity'] == 'COUNSELOR' else []
    return entry


@router.get('/admin/users')
def admin_users(user=Depends(current_user)):
    with db.read() as conn:
        require(conn, user, 'admin.users')
        return [user_entry(conn, p) for p in db.rows(conn, 'users') if p['status'] != 'DELETED']


def bind_student(conn, person, class_id, actor, academic_year_id=None, initial=False):
    classroom = db.one(conn, 'classes', class_id)
    if not classroom:
        raise HTTPException(400, '所选班级不存在')
    profile = db.one(conn, 'student_profiles', user_id=person['id'])
    years = db.rows(conn, 'academic_years')
    if not initial:
        academic_year_id = academic_year_id or max(years, key=lambda y: y['sort_order'])['id']
        if not db.one(conn, 'academic_years', academic_year_id):
            raise HTTPException(400, '调整归属的学年不存在')
    for e in db.rows(conn, 'enrollments', student_id=profile['id'], status='ACTIVE'):
        cy = db.one(conn, 'class_years', e['class_year_id'])
        if not initial and cy['academic_year_id'] != academic_year_id:
            continue
        if cy['class_id'] != class_id:
            if db.rows(conn, 'applications', enrollment_id=e['id']):
                raise HTTPException(409, '该学年已有申请，须保留其历史班级归属；请选择无申请的新学年调整班级')
            db.change(conn, 'enrollments', e['id'], status='ENDED', ended_at=db.now())
    for cy in db.rows(conn, 'class_years', class_id=class_id):
        if not initial and cy['academic_year_id'] != academic_year_id:
            continue
        prior = db.one(conn, 'enrollments', student_id=profile['id'], class_year_id=cy['id'])
        if prior:
            db.change(conn, 'enrollments', prior['id'], status='ACTIVE', ended_at=None, managed_by=actor['id'])
        else:
            db.add(conn, 'enrollments', student_id=profile['id'], class_year_id=cy['id'], managed_by=actor['id'])


def bind_counselor(conn, person, targets, actor):
    if not isinstance(targets, list):
        raise HTTPException(400, '带班关系必须为班级列表')
    for prior in db.rows(conn, 'counselor_classes', counselor_id=person['id'], status='ACTIVE'):
        db.change(conn, 'counselor_classes', prior['id'], status='ENDED', valid_to=db.now())
    for class_id in set(targets):
        if not db.one(conn, 'classes', class_id):
            raise HTTPException(400, '带班班级不存在')
        db.add(conn, 'counselor_classes', counselor_id=person['id'], class_id=class_id, assigned_by=actor['id'])


def create_user(conn, data, actor):
    username, display = str(data.get('username', '')).strip(), str(data.get('display_name', '')).strip()
    identity = data.get('base_identity', 'STUDENT')
    if not username or not display or len(username) > 80 or len(display) > 80 or identity not in ('STUDENT', 'COUNSELOR', 'ADMIN'):
        raise HTTPException(400, '请填写有效账号、姓名及基础身份')
    if db.one(conn, 'users', username=username):
        raise HTTPException(409, '账号已存在')
    person = db.add(conn, 'users', username=username, display_name=display, base_identity=identity,
                    password_hash=password_hash(validate_password(data.get('password'))))
    role = db.one(conn, 'roles', code=identity)
    db.add(conn, 'role_assignments', user_id=person['id'], role_id=role['id'], granted_by=actor['id'])
    if identity == 'STUDENT':
        number = str(data.get('student_number', '')).strip()
        if not number or len(number) > 40 or db.one(conn, 'student_profiles', student_number=number):
            raise HTTPException(400, '学生学号不能为空、不得超过40字或与其他学生重复')
        db.add(conn, 'student_profiles', user_id=person['id'], student_number=number, profile_status='ACTIVE')
        bind_student(conn, person, data.get('class_id'), actor, initial=True)
    if identity == 'COUNSELOR':
        bind_counselor(conn, person, data.get('counselor_class_ids', []), actor)
    return user_entry(conn, person)


@router.post('/admin/users')
def admin_create_user(data: dict, user=Depends(current_user)):
    with db.write() as conn:
        require(conn, user, 'admin.users')
        return create_user(conn, data, user)


@router.put('/admin/users/{user_id}')
def admin_update_user(user_id: str, data: dict, user=Depends(current_user)):
    with db.write() as conn:
        require(conn, user, 'admin.users')
        person = db.one(conn, 'users', user_id)
        if not person or person['status'] == 'DELETED':
            raise HTTPException(404, '账号不存在')
        if data.get('base_identity', person['base_identity']) != person['base_identity']:
            raise HTTPException(400, '基础身份互斥且不可原地转换，请建立对应新账号')
        updates = {}
        if 'display_name' in data:
            if not str(data['display_name']).strip() or len(str(data['display_name'])) > 80:
                raise HTTPException(400, '姓名须为1至80字')
            updates['display_name'] = str(data['display_name']).strip()
        if data.get('password'):
            updates['password_hash'] = password_hash(validate_password(data['password']))
        if 'status' in data:
            if data['status'] not in ('ACTIVE', 'DISABLED'):
                raise HTTPException(400, '账号状态无效')
            if user_id == user['id'] and data['status'] != 'ACTIVE':
                raise HTTPException(400, '不能停用当前管理员账号')
            updates['status'] = data['status']
        if person['base_identity'] == 'STUDENT' and data.get('class_id'):
            bind_student(conn, person, data['class_id'], user, academic_year_id=data.get('academic_year_id') or data.get('year_id'))
        if person['base_identity'] == 'COUNSELOR' and 'counselor_class_ids' in data:
            bind_counselor(conn, person, data['counselor_class_ids'], user)
        person = db.change(conn, 'users', user_id, **updates)
        ensure_staffing(conn)
        return user_entry(conn, person)


@router.delete('/admin/users/{user_id}')
def admin_delete_student(user_id: str, user=Depends(current_user)):
    from .admin_account_deletion import delete_student_account
    return delete_student_account(user_id, user)


@router.post('/admin/import')
async def import_users(file: UploadFile = File(...), user=Depends(current_user)):
    content = await file.read(2 * 1024 * 1024 + 1)
    if len(content) > 2 * 1024 * 1024:
        raise HTTPException(400, '导入文件不能超过2MB')
    try:
        entries = list(csv.DictReader(io.StringIO(content.decode('utf-8-sig'))))
    except UnicodeError:
        raise HTTPException(400, '请使用UTF-8编码的CSV文件') from None
    if not entries or len(entries) > 1000:
        raise HTTPException(400, '每次导入1至1000个账号')
    with db.write() as conn:
        require(conn, user, 'admin.users')
        for index, entry in enumerate(entries, start=2):
            if entry.get('counselor_class_ids'):
                entry['counselor_class_ids'] = entry['counselor_class_ids'].split(';')
            try:
                create_user(conn, entry, user)
            except HTTPException as exc:
                raise HTTPException(exc.status_code, f'第{index}行：{exc.detail}；整批未写入') from None
    return {'imported': len(entries)}


@router.get('/admin/roles')
def admin_roles(user=Depends(current_user)):
    with db.read() as conn:
        require(conn, user, 'admin.roles')
        return {'roles': db.rows(conn, 'roles'), 'permissions': db.rows(conn, 'permissions'),
                'matrix': db.rows(conn, 'role_permissions')}


@router.put('/admin/roles/{role_id}/permissions')
def update_matrix(role_id: str, data: dict, user=Depends(current_user)):
    scopes = {'OWN', 'CLASS', 'LED_CLASSES', 'CLASS_PUBLIC', 'SYSTEM_METADATA'}
    entries = data.get('permissions')
    if not isinstance(entries, list):
        raise HTTPException(400, '权限配置格式无效')
    with db.write() as conn:
        require(conn, user, 'admin.roles')
        if not db.one(conn, 'roles', role_id):
            raise HTTPException(404, '角色不存在')
        for entry in entries:
            permission = db.one(conn, 'permissions', entry.get('permission_id'))
            if not permission or entry.get('data_scope') not in scopes or not isinstance(entry.get('is_allowed'), bool):
                raise HTTPException(400, '权限项或作用域无效')
            prior = db.one(conn, 'role_permissions', role_id=role_id, permission_id=permission['id'])
            db.change(conn, 'role_permissions', prior['id'], is_allowed=entry['is_allowed'],
                      data_scope=entry['data_scope'], changed_by=user['id'], changed_at=db.now())
    return {'ok': True, 'message': '权限已保存；强制业务约束仍然生效'}


@router.post('/admin/classes')
def create_class(data: dict, user=Depends(current_user)):
    name, code = str(data.get('name', '')).strip(), str(data.get('code', '')).strip()
    if not name or not code or len(name) > 80 or len(code) > 40:
        raise HTTPException(400, '班级名称和代码不能为空且不能超长')
    with db.write() as conn:
        require(conn, user, 'admin.classes')
        if db.one(conn, 'classes', code=code):
            raise HTTPException(409, '班级代码已存在')
        classroom = db.add(conn, 'classes', name=name, code=code, status='SETUP',
                           program_name=str(data.get('program_name', ''))[:80])
        rule = db.rows(conn, 'score_rule_versions', status='PUBLISHED')[-1]
        for year in db.rows(conn, 'academic_years'):
            cy = db.add(conn, 'class_years', class_id=classroom['id'], academic_year_id=year['id'], score_rule_version_id=rule['id'])
            db.add(conn, 'class_publicity', class_year_id=cy['id'], is_open=False)
        return classroom | {'message': '班级筹备中，请绑定学生并配置一名班长、至少两名审查员后启用'}


@router.put('/admin/classes/{class_id}/assignments')
def class_assignments(class_id: str, data: dict, user=Depends(current_user)):
    with db.write() as conn:
        require(conn, user, 'admin.classes')
        classroom = db.one(conn, 'classes', class_id)
        if not classroom:
            raise HTTPException(404, '班级不存在')
        monitor_id, reviewers = data.get('monitor_id'), data.get('reviewer_ids', [])
        students = students_of_class(conn, class_id)
        if monitor_id not in students or not isinstance(reviewers, list) or len(set(reviewers)) < 2 or not set(reviewers) <= students:
            raise HTTPException(400, '请选择本班一名班长和至少两名不同学生审查员')
        replace_class_role(conn, class_id, 'MONITOR', [monitor_id], user)
        replace_class_role(conn, class_id, 'REVIEWER', reviewers, user)
        if 'counselor_ids' in data:
            for prior in db.rows(conn, 'counselor_classes', class_id=class_id, status='ACTIVE'):
                db.change(conn, 'counselor_classes', prior['id'], status='ENDED', valid_to=db.now())
            for counselor_id in set(data['counselor_ids']):
                person = db.one(conn, 'users', counselor_id)
                if not person or person['base_identity'] != 'COUNSELOR' or person['status'] != 'ACTIVE':
                    raise HTTPException(400, '带班人须为有效辅导员')
                db.add(conn, 'counselor_classes', counselor_id=counselor_id, class_id=class_id, assigned_by=user['id'])
        db.change(conn, 'classes', class_id, status='ACTIVE')
        ensure_staffing(conn)
    return {'ok': True}
