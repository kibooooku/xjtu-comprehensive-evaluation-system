"""Add category groups and settings while preserving original rounds and evidence."""
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from fastapi import HTTPException
from . import db


def active_year(conn):
    setting = db.one(conn, 'system_settings', 'active_academic_year')
    if setting:
        year = db.one(conn, 'academic_years', (setting['value_json'] or {}).get('year_id'))
        if year:
            return year
    years = sorted(db.rows(conn, 'academic_years'), key=lambda y: y['sort_order'])
    selectable = [y for y in years if y['is_selectable']]
    if not selectable:
        raise HTTPException(409, '管理员尚未指定统计学年')
    # Keep V1's initially visible demo year; administrators explicitly choose later.
    return selectable[0]


def assert_current_year(conn, year_id):
    if active_year(conn)['id'] != year_id:
        raise HTTPException(409, '该学年已归档，只能查看；补交请联系管理员切换当前统计学年')


def ensure_group(conn, application, confirmed=True):
    parent = db.one(conn, 'category_submissions', enrollment_id=application['enrollment_id'], category_id=application['category_id'])
    if not parent:
        parent = db.add(conn, 'category_submissions', enrollment_id=application['enrollment_id'], category_id=application['category_id'])
    item = db.one(conn, 'category_items', application_id=application['id'])
    if not item:
        count = len(db.rows(conn, 'category_items', submission_id=parent['id']))
        item = db.add(conn, 'category_items', submission_id=parent['id'], application_id=application['id'],
                      sort_order=count, confirmed_at=db.now() if confirmed else None,
                      deleted_at=application.get('archived_at'))
    return parent, item


def _backup_existing():
    database = db.engine.url.database
    if not database or database == ':memory:' or not Path(database).exists():
        return None
    target_dir = db.storage_root / 'backups'
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / ('pre-v2-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f') + '.db')
    source = sqlite3.connect(str(database))
    destination = sqlite3.connect(str(target))
    try:
        source.backup(destination)
        if destination.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Database backup integrity check failed')
    finally:
        source.close()
        destination.close()
    return target


def initialize_v2():
    with db.read() as conn:
        is_initial = not db.one(conn, 'system_settings', 'schema_version')
        has_original_data = bool(db.rows(conn, 'applications'))
        competition_sum_upgrade = any(category['code'].upper() in {'RESEARCH', 'ARTS'} and category['aggregation_kind'] != 'SUM'
                                      for category in db.rows(conn, 'score_categories'))
        qualification_routing_upgrade = any(bool(category['requires_eligibility_review']) != (category['code'].upper() in {'RESEARCH', 'ARTS', 'POST'})
                                            for category in db.rows(conn, 'score_categories'))
    if has_original_data and (is_initial or competition_sum_upgrade or qualification_routing_upgrade):
        _backup_existing()
    with db.write() as conn:
        year = active_year(conn)
        if not db.one(conn, 'system_settings', 'active_academic_year'):
            db.add(conn, 'system_settings', id='active_academic_year', value_json={'year_id': year['id']})
        for application in db.rows(conn, 'applications'):
            ensure_group(conn, application, confirmed=True)
        for category in db.rows(conn, 'score_categories'):
            if category['code'].upper() in {'RESEARCH', 'ARTS'} and category['aggregation_kind'] != 'SUM':
                db.change(conn, 'score_categories', category['id'], aggregation_kind='SUM')
            requires_qualification = category['code'].upper() in {'RESEARCH', 'ARTS', 'POST'}
            if bool(category['requires_eligibility_review']) != requires_qualification:
                db.change(conn, 'score_categories', category['id'], requires_eligibility_review=requires_qualification)
        if is_initial:
            db.add(conn, 'system_settings', id='schema_version', value_json={'version': 2, 'migration': 'category-groups-preserve-rounds'})
