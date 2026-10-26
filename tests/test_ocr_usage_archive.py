"""Deleting evidence retains aggregate OCR budget usage; only synthetic data."""
import pytest
from sqlalchemy import delete

from app import db, materials
from app.integrations import ProviderError
from test_materials import material_app, create_claim, upload


@pytest.fixture
def page(material_app, monkeypatch):
    monkeypatch.setattr(materials, 'now', lambda: '2026-12-15T12:00:00.000+00:00')
    _, login = material_app
    client = login('student')
    claim, _, _ = create_claim(client)
    file = upload(client, claim['id'])
    with db.read() as conn:
        record = db.one(conn, 'file_pages', file['pages'][0]['id'])
    monkeypatch.setattr(materials, 'tencent_ocr', lambda *args: pytest.fail('no real or unplanned OCR call'))
    return client, record


def add_attempt(conn, page, *, action='GeneralAccurateOCR', provider='TENCENT', month='2026-12', state='FAILED'):
    return db.add(conn, 'recognition_runs', page_id=page['id'], provider=provider, api_name=action,
                  parameters_hash=db.uid(), status=state, result_json={},
                  started_at=month + '-05T00:00:00.000+00:00', finished_at=None)


def test_archive_and_retained_attempts_add_by_exact_utc_month_and_api(page):
    client, record = page
    with db.write() as conn:
        for state in ('SUCCEEDED', 'FAILED', 'RUNNING'):
            add_attempt(conn, record, state=state)
        add_attempt(conn, record, action='SealOCR')
        add_attempt(conn, record, provider='CACHE')
        add_attempt(conn, record, provider='LOCAL_PDF', action='NativePDFText')
        add_attempt(conn, record, month='2026-11')
        add_attempt(conn, record, month='2027-01')
        db.add(conn, 'system_settings', id='ocr_usage_archive', value_json={
            '2026-12': {'GeneralAccurateOCR': 5, 'SealOCR': 2},
            '2026-11': {'GeneralAccurateOCR': 90, 'SealOCR': 80},
            '2027-01': {'GeneralAccurateOCR': 70, 'SealOCR': 60}})
        assert materials._usage(conn, 'GeneralAccurateOCR') == 8
        assert materials._usage(conn, 'SealOCR') == 3
    status = client.get('/api/integrations/status').json()
    assert status['general']['used'] == 8 and status['seal']['used'] == 3
    assert status['provider_balance_known'] is False


def test_missing_archive_preserves_existing_budget_count(page):
    _, record = page
    with db.write() as conn:
        add_attempt(conn, record)
        assert materials._usage(conn, 'GeneralAccurateOCR') == 1
        assert materials._usage(conn, 'SealOCR') == 0


@pytest.mark.parametrize('archive', [
    {'2026-12': {'GeneralAccurateOCR': -9}},
    {'2026-12': {'GeneralAccurateOCR': True}},
    {'2026-12': {'GeneralAccurateOCR': '9'}},
    {'2026-12': []},
    [],
])
def test_malformed_archive_never_reduces_retained_budget_or_breaks_status(page, archive):
    client, record = page
    with db.write() as conn:
        add_attempt(conn, record)
        db.add(conn, 'system_settings', id='ocr_usage_archive', value_json=archive)
        assert materials._usage(conn, 'GeneralAccurateOCR') == 1
    assert client.get('/api/integrations/status').json()['general']['used'] == 1


def test_deleting_attempt_rows_and_archiving_atomically_does_not_restore_free_credit(page):
    _, record = page
    with db.write() as conn:
        first = add_attempt(conn, record)
        second = add_attempt(conn, record)
        before = materials._usage(conn, 'GeneralAccurateOCR')
        db.add(conn, 'system_settings', id='ocr_usage_archive', value_json={'2026-12': {'GeneralAccurateOCR': 2, 'SealOCR': 0}})
        conn.execute(delete(db.tables['recognition_runs']).where(db.tables['recognition_runs'].c.id.in_([first['id'], second['id']])))
        assert materials._usage(conn, 'GeneralAccurateOCR') == before == 2
    with pytest.raises(ProviderError) as caught:
        materials._ocr_page(record, 'GeneralAccurateOCR', {'general_limit': 2, 'seal_limit': 1})
    assert caught.value.code == 'LocalBudgetExceeded'
    assert caught.value.diagnostics == {'action': 'GeneralAccurateOCR', 'used': 2, 'limit': 2}
    with db.read() as conn:
        assert db.rows(conn, 'recognition_runs') == []  # No cloud reservation or HTTP retry.
