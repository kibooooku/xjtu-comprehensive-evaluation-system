"""SQLAlchemy Core repository; writes serialize cross-process SQLite decisions."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import (JSON, Boolean, Column, ForeignKey, Index, Integer, MetaData,
                        Numeric, String, Table, Text, create_engine, event, select)

ROOT = Path(__file__).resolve().parent.parent
metadata = MetaData()
schema = json.loads((ROOT / 'app/schema.json').read_text(encoding='utf-8'))
tables = {}
for definition in schema['tables']:
    columns = []
    for f in definition['fields']:
        kind = f['type']
        if kind == 'JSON':
            typ = JSON
        elif kind == 'BOOLEAN':
            typ = Boolean
        elif kind in ('INTEGER', 'BIGINT', 'SMALLINT'):
            typ = Integer
        elif kind.startswith('DECIMAL'):
            precision, scale = map(int, kind[8:-1].split(','))
            typ = Numeric(precision, scale)
        else:
            typ = Text if kind == 'TEXT' else String
        args = [ForeignKey(f['fk'], deferrable=True, initially='DEFERRED')] if f.get('fk') else []
        columns.append(Column(f['name'], typ, *args, primary_key=f.get('pk', False),
                              nullable=f.get('nullable', False) if not f.get('pk') else False))
    tables[definition['id']] = Table(definition['id'], metadata, *columns)

# V2 extends the reviewed V1 model without rewriting immutable application history.
tables['system_settings'] = Table('system_settings', metadata,
    Column('id', String, primary_key=True), Column('value_json', JSON, nullable=False),
    Column('changed_by', String, ForeignKey('users.id'), nullable=True), Column('updated_at', String, nullable=False))
tables['category_submissions'] = Table('category_submissions', metadata,
    Column('id', String, primary_key=True), Column('enrollment_id', String, ForeignKey('enrollments.id'), nullable=False),
    Column('category_id', String, ForeignKey('score_categories.id'), nullable=False),
    Column('created_at', String, nullable=False), Column('updated_at', String, nullable=False))
tables['category_items'] = Table('category_items', metadata,
    Column('id', String, primary_key=True), Column('submission_id', String, ForeignKey('category_submissions.id'), nullable=False),
    Column('application_id', String, ForeignKey('applications.id'), nullable=False), Column('sort_order', Integer, nullable=False),
    Column('confirmed_at', String, nullable=True), Column('deleted_at', String, nullable=True), Column('created_at', String, nullable=False))
tables['background_jobs'] = Table('background_jobs', metadata,
    Column('id', String, primary_key=True), Column('application_id', String, ForeignKey('applications.id'), nullable=False),
    Column('owner_id', String, ForeignKey('users.id'), nullable=False), Column('kind', String, nullable=False),
    Column('state', String, nullable=False), Column('stage', String, nullable=False),
    Column('progress', Integer, nullable=False), Column('total', Integer, nullable=False),
    Column('input_revision_id', String, ForeignKey('application_revisions.id'), nullable=False),
    Column('input_fingerprint', String, nullable=False), Column('result_json', JSON, nullable=False),
    Column('error_message', Text, nullable=True), Column('force', Boolean, nullable=False), Column('attempts', Integer, nullable=False),
    Column('worker_token', String, nullable=True), Column('heartbeat_at', String, nullable=True),
    Column('created_at', String, nullable=False), Column('started_at', String, nullable=True), Column('finished_at', String, nullable=True))
Index('uq_submission_enrollment_category', tables['category_submissions'].c.enrollment_id, tables['category_submissions'].c.category_id, unique=True)
Index('uq_category_item_application', tables['category_items'].c.application_id, unique=True)
Index('ix_background_queue', tables['background_jobs'].c.state, tables['background_jobs'].c.created_at)

for table, fields in [('users', ['username']), ('student_profiles', ['user_id']),
                      ('student_profiles', ['student_number']), ('classes', ['code']),
                      ('academic_years', ['code']), ('roles', ['code']), ('permissions', ['code']),
                      ('class_years', ['class_id', 'academic_year_id']),
                      ('enrollments', ['student_id', 'class_year_id']),
                      ('role_permissions', ['role_id', 'permission_id']),
                      ('class_publicity', ['class_year_id']),
                      ('application_revisions', ['application_id', 'revision_no']),
                      ('submission_rounds', ['application_id', 'round_no']),
                      ('revision_files', ['revision_id', 'file_id']),
                      ('file_pages', ['file_id', 'page_index']),
                      ('review_decisions', ['actor_id', 'idempotency_key'])]:
    Index('uq_' + table + '_' + '_'.join(fields), *(tables[table].c[f] for f in fields), unique=True)
Index('ix_round_state', tables['submission_rounds'].c.state)
Index('ix_events_app', tables['application_events'].c.application_id, tables['application_events'].c.event_no)
Index('ix_assign_user', tables['role_assignments'].c.user_id, tables['role_assignments'].c.status)
Index('ix_file_hash', tables['file_objects'].c.sha256)

engine = None
storage_root = ROOT / 'instance'


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def uid():
    return str(uuid4())


def money(value):
    try:
        result = Decimal(str(value)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        if not result.is_finite():
            raise InvalidOperation
        return result
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(400, '分值必须是有效数字') from None


def configure(database_url=None, storage_dir=None):
    global engine, storage_root
    if engine is not None:
        engine.dispose()
    storage_root = Path(storage_dir or os.getenv('STORAGE_DIR', str(ROOT / 'instance'))).resolve()
    storage_root.mkdir(parents=True, exist_ok=True)
    url = database_url or os.getenv('DATABASE_URL') or ('sqlite:///' + (storage_root / 'evaluation.db').as_posix())
    engine = create_engine(url, connect_args={'check_same_thread': False, 'timeout': 30})

    @event.listens_for(engine, 'connect')
    def setup_sqlite(connection, _):
        cursor = connection.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.execute('PRAGMA busy_timeout=30000')
        cursor.execute('PRAGMA journal_mode=WAL')
        cursor.close()

    metadata.create_all(engine)


@contextmanager
def read():
    with engine.connect() as connection:
        yield connection


@contextmanager
def write():
    with engine.connect() as connection:
        connection.exec_driver_sql('BEGIN IMMEDIATE')
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


def rows(conn, table_name, **where):
    table = tables[table_name]
    query = select(table)
    for key, value in where.items():
        query = query.where(table.c[key].is_(None) if value is None else table.c[key] == value)
    return [dict(r) for r in conn.execute(query).mappings()]


def one(conn, table_name, id=None, **where):
    if id is None and not where:
        return None
    if id is not None:
        where['id'] = id
    matches = rows(conn, table_name, **where)
    return matches[0] if matches else None


def add(conn, table_name, **values):
    table = tables[table_name]
    values.setdefault('id', uid())
    for column in table.columns:
        if column.name in values or column.nullable:
            continue
        if column.foreign_keys:
            raise ValueError(f'{table_name}.{column.name} is required')
        if column.name.endswith('_at') or column.name in ('valid_from', 'effective_at'):
            values[column.name] = now()
        elif isinstance(column.type, Boolean):
            values[column.name] = False
        elif isinstance(column.type, Integer):
            values[column.name] = 1 if column.name in ('version_no', 'round_no', 'revision_no') else 0
        elif isinstance(column.type, Numeric):
            values[column.name] = Decimal('0')
        elif isinstance(column.type, JSON):
            values[column.name] = {}
        else:
            values[column.name] = 'ACTIVE' if column.name == 'status' else ''
    conn.execute(table.insert().values(**values))
    return one(conn, table_name, values['id'])


def change(conn, table_name, id, **values):
    table = tables[table_name]
    if 'updated_at' in table.c and 'updated_at' not in values:
        values['updated_at'] = now()
    if values:
        conn.execute(table.update().where(table.c.id == id).values(**values))
    return one(conn, table_name, id)
