"""Cookie sessions, password hashes, RBAC scope intersection and mandatory policy."""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import HTTPException, Request
from . import db

COOKIE = 'xjtu_session'


def password_hash(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return 'scrypt$' + salt.hex() + '$' + digest.hex()


def check_password(password, encoded):
    try:
        _, salt, digest = encoded.split('$')
        result = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
        return hmac.compare_digest(result.hex(), digest)
    except (ValueError, TypeError):
        return False


def session_key():
    configured = os.getenv('SESSION_SECRET')
    if configured:
        return configured.encode()
    path = db.storage_root / 'session.key'
    if not path.exists():
        try:
            with path.open('xb') as stream:
                stream.write(secrets.token_bytes(48))
        except FileExistsError:
            pass
    return path.read_bytes()


def make_session(user):
    payload = json.dumps({'uid': user['id'], 'exp': int(time.time()) + 43200,
                          'nonce': secrets.token_hex(12), 'v': hashlib.sha256(user['password_hash'].encode()).hexdigest()[:16]},
                         separators=(',', ':')).encode()
    body = base64.urlsafe_b64encode(payload).decode().rstrip('=')
    return body + '.' + hmac.new(session_key(), body.encode(), hashlib.sha256).hexdigest()


def resolve_session(request):
    token = request.cookies.get(COOKIE, '')
    try:
        body, signature = token.rsplit('.', 1)
        expected = hmac.new(session_key(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        data = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
        if data['exp'] < time.time():
            return None
        with db.read() as conn:
            user = db.one(conn, 'users', data['uid'])
        if not user or user['status'] != 'ACTIVE':
            return None
        if data['v'] != hashlib.sha256(user['password_hash'].encode()).hexdigest()[:16]:
            return None
        return user
    except (ValueError, KeyError, TypeError):
        return None


def current_user(request: Request):
    user = resolve_session(request)
    if not user:
        raise HTTPException(401, '请先登录')
    return user


def csrf_token(token):
    return hmac.new(session_key(), ('csrf:' + token).encode(), hashlib.sha256).hexdigest()


def active_grants(conn, user):
    instant = db.now()
    result = []
    for grant in db.rows(conn, 'role_assignments', user_id=user['id'], status='ACTIVE'):
        if grant['valid_from'] > instant or (grant['valid_to'] and grant['valid_to'] <= instant):
            continue
        role = db.one(conn, 'roles', grant['role_id'])
        if role['allowed_identity'] == user['base_identity']:
            result.append((grant, role))
    return result


def student_enrollment(conn, user, year_id):
    student = db.one(conn, 'student_profiles', user_id=user['id'])
    if not student:
        raise HTTPException(403, '仅学生可填报')
    year = db.one(conn, 'academic_years', year_id)
    if not year or not year['is_selectable']:
        raise HTTPException(400, '请选择可填报学年')
    for enrollment in db.rows(conn, 'enrollments', student_id=student['id'], status='ACTIVE'):
        cy = db.one(conn, 'class_years', enrollment['class_year_id'])
        if cy['academic_year_id'] == year_id:
            return enrollment
    raise HTTPException(400, '本学年尚未绑定班级，请联系管理员')


def class_context(conn, enrollment_id):
    enrollment = db.one(conn, 'enrollments', enrollment_id)
    if not enrollment:
        raise HTTPException(404, '班级学年记录不存在')
    student = db.one(conn, 'student_profiles', enrollment['student_id'])
    cy = db.one(conn, 'class_years', enrollment['class_year_id'])
    return {'enrollment': enrollment, 'student': student, 'user': db.one(conn, 'users', student['user_id']),
            'class_year': cy, 'class': db.one(conn, 'classes', cy['class_id']),
            'year': db.one(conn, 'academic_years', cy['academic_year_id'])}


def own_class_ids(conn, user):
    if user['base_identity'] == 'COUNSELOR':
        return {x['class_id'] for x in db.rows(conn, 'counselor_classes', counselor_id=user['id'], status='ACTIVE')
                if x['valid_from'] <= db.now() and (not x['valid_to'] or x['valid_to'] > db.now())}
    student = db.one(conn, 'student_profiles', user_id=user['id'])
    if not student:
        return set()
    return {db.one(conn, 'class_years', e['class_year_id'])['class_id']
            for e in db.rows(conn, 'enrollments', student_id=student['id'], status='ACTIVE')}


BUSINESS_PERMISSIONS = {'application.read', 'application.write', 'application.submit', 'application.withdraw',
                       'materials.read', 'review.peer', 'review.counselor', 'review.revoke', 'activity.manage',
                       'scores.read', 'publicity.read', 'publicity.manage', 'export.read', 'reviewers.manage'}


def allowed(conn, user, permission, class_id=None, owner_id=None):
    identity = user['base_identity']
    if identity == 'ADMIN' and permission in BUSINESS_PERMISSIONS:
        return False
    if permission.startswith('admin.') and identity != 'ADMIN':
        return False
    if permission in ('application.write', 'application.submit', 'application.withdraw'):
        if identity != 'STUDENT' or (owner_id is not None and owner_id != user['id']):
            return False
    if permission == 'review.peer' and (identity != 'STUDENT' or owner_id == user['id']):
        return False
    if permission in ('review.counselor', 'review.revoke', 'publicity.manage', 'activity.manage') and identity != 'COUNSELOR':
        return False
    if permission == 'reviewers.manage' and identity != 'STUDENT':
        return False
    grants = active_grants(conn, user)
    own_classes = own_class_ids(conn, user)
    if permission == 'organization.read' and class_id is not None and identity != 'ADMIN' and class_id not in own_classes:
        return False

    def has_class_role(code):
        return any(role['code'] == code and grant['class_id'] in own_classes
                   and (class_id is None or grant['class_id'] == class_id)
                   for grant, role in grants)

    if permission == 'review.peer' and not has_class_role('REVIEWER'):
        return False
    if permission == 'reviewers.manage' and not has_class_role('MONITOR'):
        return False
    if permission == 'export.read' and identity != 'COUNSELOR' and not has_class_role('MONITOR'):
        return False
    target = db.one(conn, 'permissions', code=permission)
    if not target:
        return False
    for grant, role in grants:
        rp = db.one(conn, 'role_permissions', role_id=role['id'], permission_id=target['id'])
        if not rp or not rp['is_allowed']:
            continue
        scope = rp['data_scope']
        if role['scope_kind'] == 'CLASS' and class_id is not None and grant['class_id'] != class_id:
            continue
        if scope == 'SYSTEM_METADATA' and (permission.startswith('admin.') or permission in ('activity.read', 'activity.manage', 'organization.read')):
            return True
        if scope == 'OWN':
            if owner_id == user['id']:
                return True
            if owner_id is None and class_id is None:
                return True  # Capability discovery; no concrete resource is released here.
            if permission == 'organization.read' and class_id in own_classes:
                return True
        if scope == 'CLASS' and grant['class_id'] and grant['class_id'] in own_classes and (class_id is None or class_id == grant['class_id']):
            return True
        if scope == 'LED_CLASSES' and identity == 'COUNSELOR' and (class_id is None or class_id in own_classes):
            return True
        if scope == 'CLASS_PUBLIC' and permission == 'publicity.read' and (class_id is None or class_id in own_classes):
            return True  # Actual class-year open-state is checked by publicity endpoint.
    return False


def require(conn, user, permission, class_id=None, owner_id=None):
    if not allowed(conn, user, permission, class_id, owner_id):
        raise HTTPException(403, '没有执行此操作的数据权限')


def public_user(conn, user):
    return {k: user[k] for k in ('id', 'username', 'display_name', 'base_identity')} | {
        'roles': [{'code': role['code'], 'name': role['name'], 'class_id': grant['class_id']}
                  for grant, role in active_grants(conn, user)]}
