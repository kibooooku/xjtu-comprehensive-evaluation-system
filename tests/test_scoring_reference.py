"""The school reference is served unchanged to logged-in users in a new tab."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from test_materials import material_app


def test_reference_requires_login(material_app):
    application, _ = material_app
    with TestClient(application) as client:
        assert client.get('/api/reference/scoring-standard').status_code == 401


@pytest.mark.parametrize('account', ['student', 'reviewer1', 'counselor', 'admin'])
def test_original_reference_is_readable_inline_by_each_role(material_app, account):
    _, login = material_app
    response = login(account).get('/api/reference/scoring-standard')
    expected = Path(__file__).resolve().parents[1] / 'app/resources/scoring-standard.pdf'
    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/pdf'
    assert response.headers['content-disposition'].startswith('inline;')
    assert response.content.startswith(b'%PDF-')
    assert response.content == expected.read_bytes()
