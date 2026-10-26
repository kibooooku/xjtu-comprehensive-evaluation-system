"""Backed-up student deletion, with shared audit and OCR budget preservation."""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3

from fastapi import HTTPException
from sqlalchemy import delete, select

from . import db
from .security import active_grants, require


def _batches(values):
    values = list(values)
    for index in range(0, len(values), 400):
        yield values[index:index + 400]


def _ids(conn, table_name, column_name, values):
    table = db.tables[table_name]
    result = set()
    for batch in _batches(values):
        result.update(conn.execute(select(table.c.id).where(table.c[column_name].in_(batch))).scalars())
    return result


def _target(conn, target_id, actor):
    administrator = db.one(conn, 'users', actor['id'])
    if not administrator or administrator['status'] != 'ACTIVE' or administrator['base_identity'] != 'ADMIN':
        raise HTTPException(403, '只有管理员可以删除学生账号')
    require(conn, administrator, 'admin.users')
    if target_id == administrator['id']:
        raise HTTPException(403, '不能删除当前登录账号')
    person = db.one(conn, 'users', target_id)
    if not person or person['status'] == 'DELETED':
        raise HTTPException(404, '学生账号不存在或已经删除')
    if person['base_identity'] != 'STUDENT':
        raise HTTPException(403, '此功能只允许删除学生账号')
    return person


def _staffing_guard(conn, person):
    from .organization import students_of_class
    affected = {grant['class_id'] for grant, role in active_grants(conn, person)
                if role['code'] in {'MONITOR', 'REVIEWER'} and grant.get('class_id')}
    for class_id in affected:
        classroom = db.one(conn, 'classes', class_id)
        if not classroom or classroom['status'] != 'ACTIVE':
            continue
        members = students_of_class(conn, class_id) - {person['id']}
        counts = {'MONITOR': set(), 'REVIEWER': set()}
        for user_id in members:
            member = db.one(conn, 'users', user_id)
            for grant, role in active_grants(conn, member):
                if grant.get('class_id') == class_id and role['code'] in counts:
                    counts[role['code']].add(user_id)
        if len(counts['MONITOR']) != 1 or len(counts['REVIEWER']) < 2:
            raise HTTPException(409, '该学生仍担任班长或必要的审查员，请先完成班级职责交接后再删除')


def _busy(conn, target_id):
    from .materials import synchronous_analysis_active
    return bool(db.rows(conn, 'background_jobs', owner_id=target_id, state='RUNNING')
                or synchronous_analysis_active(conn, target_id))


def _backup_database():
    database = db.engine.url.database
    if not database or database == ':memory:':
        raise HTTPException(503, '当前数据库无法创建删除前备份，账号尚未删除')
    folder = (db.storage_root / 'backups').resolve()
    if not folder.is_relative_to(db.storage_root.resolve()):
        raise HTTPException(503, '备份目录配置无效，账号尚未删除')
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    destination = folder / f'pre-student-delete-{stamp}-{db.uid()[:8]}.db'
    partial = destination.with_suffix('.partial')
    try:
        # The caller holds BEGIN IMMEDIATE. A separate SQLite reader can back up
        # the committed WAL snapshot while subsequent business writes wait.
        with closing(sqlite3.connect(str(database), timeout=30)) as source, closing(sqlite3.connect(partial)) as target:
            source.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('backup integrity failure')
        partial.replace(destination)
    except Exception:
        raise HTTPException(503, '删除前备份未能完成，账号尚未删除，请稍后重试') from None
    return destination.name


def _plan(conn, target_id):
    plan = {'users': {target_id}}
    def add(table, column, values):
        plan[table] = _ids(conn, table, column, values)
    add('student_profiles', 'user_id', {target_id})
    add('enrollments', 'student_id', plan['student_profiles'])
    add('applications', 'enrollment_id', plan['enrollments'])
    add('application_revisions', 'application_id', plan['applications'])
    add('submission_rounds', 'application_id', plan['applications'])
    add('review_decisions', 'round_id', plan['submission_rounds'])
    add('application_events', 'application_id', plan['applications'])
    add('category_submissions', 'enrollment_id', plan['enrollments'])
    add('category_items', 'application_id', plan['applications'])
    add('background_jobs', 'application_id', plan['applications'])
    plan['background_jobs'] |= _ids(conn, 'background_jobs', 'owner_id', {target_id})
    add('revision_files', 'revision_id', plan['application_revisions'])
    add('ai_analyses', 'application_id', plan['applications'])
    add('analysis_inputs', 'analysis_id', plan['ai_analyses'])
    add('evidence_crops', 'revision_id', plan['application_revisions'])
    add('file_objects', 'uploaded_by', {target_id})

    # A shared original, recognition result, or evidence source used by another
    # student's history survives. Its uploader becomes an anonymous audit user.
    page_file, run_file, region_file = {}, {}, {}
    pages, runs, regions = db.tables['file_pages'], db.tables['recognition_runs'], db.tables['recognition_regions']
    for batch in _batches(plan['file_objects']):
        page_file.update(conn.execute(select(pages.c.id, pages.c.file_id).where(pages.c.file_id.in_(batch))).all())
    for batch in _batches(page_file):
        for row in conn.execute(select(runs.c.id, runs.c.page_id).where(runs.c.page_id.in_(batch))):
            run_file[row.id] = page_file[row.page_id]
    for batch in _batches(run_file):
        for row in conn.execute(select(regions.c.id, regions.c.run_id).where(regions.c.run_id.in_(batch))):
            region_file[row.id] = run_file[row.run_id]
    shared = set()
    checks = [('revision_files', 'file_id', {value: value for value in plan['file_objects']}),
              ('analysis_inputs', 'file_id', {value: value for value in plan['file_objects']}),
              ('analysis_inputs', 'page_id', page_file), ('analysis_inputs', 'recognition_run_id', run_file),
              ('evidence_crops', 'page_id', page_file), ('evidence_crops', 'recognition_region_id', region_file)]
    for table_name, column, file_map in checks:
        table = db.tables[table_name]
        for batch in _batches(file_map):
            for row in conn.execute(select(table.c.id, table.c[column]).where(table.c[column].in_(batch))):
                if row.id not in plan.get(table_name, set()):
                    shared.add(file_map[row[1]])
    plan['file_objects'] -= shared
    add('file_pages', 'file_id', plan['file_objects'])
    add('recognition_runs', 'page_id', plan['file_pages'])
    add('recognition_regions', 'run_id', plan['recognition_runs'])
    add('role_assignments', 'user_id', {target_id})
    plan['system_settings'] = {key for key in (f'task_control:{target_id}', f'synchronous-analysis:{target_id}')
                               if db.one(conn, 'system_settings', key)}
    detached = _ids(conn, 'activity_eligibility_versions', 'source_review_decision_id', plan['review_decisions'])

    def outside(table, column, ids):
        return _ids(conn, table, column, ids) - plan.get(table, set())
    tombstone = bool(shared)
    for table in db.metadata.tables.values():
        for foreign_key in table.foreign_keys:
            if foreign_key.column.table.name == 'users' and outside(table.name, foreign_key.parent.name, {target_id}):
                tombstone = True
            if foreign_key.column.table.name == 'role_assignments' and outside(table.name, foreign_key.parent.name, plan['role_assignments']):
                tombstone = True
    if tombstone:
        plan['users'] = set()
        plan['role_assignments'] = set()
    for table in db.metadata.tables.values():
        for foreign_key in table.foreign_keys:
            parent_ids = plan.get(foreign_key.column.table.name, set())
            referencing = outside(table.name, foreign_key.parent.name, parent_ids)
            if table.name == 'activity_eligibility_versions' and foreign_key.parent.name == 'source_review_decision_id':
                referencing -= detached
            if referencing:
                raise HTTPException(409, '该学生仍有跨账号的共享历史关联，账号尚未删除，请联系维护人员核对')
    return plan, detached, tombstone


def _archive_usage(conn, run_ids):
    additions = defaultdict(lambda: defaultdict(int))
    table = db.tables['recognition_runs']
    for batch in _batches(run_ids):
        for row in conn.execute(select(table.c.api_name, table.c.started_at).where(table.c.id.in_(batch), table.c.provider == 'TENCENT')):
            month = str(row.started_at or '')[:7]
            if re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])', month):
                additions[month][row.api_name] += 1
    if not additions:
        return {}
    existing = db.one(conn, 'system_settings', 'ocr_usage_archive')
    archive = (existing or {}).get('value_json', {})
    if not isinstance(archive, dict):
        raise HTTPException(503, 'OCR调用计数记录异常，账号尚未删除')
    archive = {month: dict(values) if isinstance(values, dict) else values for month, values in archive.items()}
    for month, values in additions.items():
        bucket = archive.setdefault(month, {})
        if not isinstance(bucket, dict):
            raise HTTPException(503, 'OCR调用计数记录异常，账号尚未删除')
        for action, count in values.items():
            prior = bucket.get(action, 0)
            if type(prior) is not int or prior < 0:
                raise HTTPException(503, 'OCR调用计数记录异常，账号尚未删除')
            bucket[action] = prior + count
    if existing:
        db.change(conn, 'system_settings', existing['id'], value_json=archive)
    else:
        db.add(conn, 'system_settings', id='ocr_usage_archive', value_json=archive)
    return {month: dict(values) for month, values in additions.items()}


def delete_student_account(target_id, actor):
    from .jobs import pause_owner
    # Commit the pause before reporting an in-flight request. Raising inside
    # this transaction would undo it and leave the provider running freely.
    with db.write() as conn:
        person = _target(conn, target_id, actor)
        _staffing_guard(conn, person)
        pause_owner(conn, target_id, reason='ADMIN_DELETE')
        busy = _busy(conn, target_id)
    if busy:
        raise HTTPException(409, '已暂停该学生的后台任务；正在等待已发出的请求结束，请稍后再次点击删除')

    with db.write() as conn:
        person = _target(conn, target_id, actor)
        _staffing_guard(conn, person)
        pause_owner(conn, target_id, reason='ADMIN_DELETE')
        busy = _busy(conn, target_id)
        if not busy:
            plan, detached, tombstone = _plan(conn, target_id)
            backup_name = _backup_database()
            conn.exec_driver_sql('PRAGMA defer_foreign_keys=ON')
            archived = _archive_usage(conn, plan['recognition_runs'])
            for row_id in detached:
                db.change(conn, 'activity_eligibility_versions', row_id, source_review_decision_id=None)
            if tombstone:
                for grant in db.rows(conn, 'role_assignments', user_id=target_id, status='ACTIVE'):
                    db.change(conn, 'role_assignments', grant['id'], status='ENDED', valid_to=db.now())
                db.change(conn, 'users', target_id, username='deleted_' + db.uid().replace('-', ''),
                          display_name='已删除学生', status='DELETED', password_hash='!deleted:' + db.uid(), last_login_at=None)
            for table_name, ids in reversed(list(plan.items())):
                table = db.tables[table_name]
                for batch in _batches(ids):
                    conn.execute(delete(table).where(table.c.id.in_(batch)))
            if list(conn.exec_driver_sql('PRAGMA foreign_key_check')):
                raise HTTPException(409, '仍有关联历史需要保留，账号尚未删除')
            result = {'ok': True, 'deleted_user_id': target_id, 'preserved_audit': tombstone,
                      'deleted_counts': {table: len(ids) for table, ids in plan.items()},
                      'archived_ocr_attempts': archived, 'backup': backup_name,
                      'message': '学生账号已删除，原账号和学号可重新使用'}
    if busy:
        raise HTTPException(409, '已暂停该学生的后台任务；正在等待已发出的请求结束，请稍后再次点击删除')
    return result
