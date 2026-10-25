"""Large attachments pass both request framing and actual PDF validation."""
import io

import pypdfium2 as pdfium
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from app import materials
from app.limits import MAX_ATTACHMENT_BYTES, MAX_ATTACHMENT_PAGES, MAX_REQUEST_BYTES
from app.main import create_app


def blank_pdf(pages):
    document = pdfium.PdfDocument.new()
    try:
        for _ in range(pages):
            page = document.new_page(72, 72)
            page.close()
        stream = io.BytesIO()
        document.save(stream)
        return stream.getvalue()
    finally:
        document.close()


def test_real_500_page_pdf_is_accepted_and_501_is_rejected():
    assert MAX_ATTACHMENT_PAGES == 500
    mime, rendered = materials.render_upload(blank_pdf(500), '500-pages.pdf')
    assert mime == 'application/pdf' and len(rendered) == 500
    with pytest.raises(HTTPException) as rejected:
        materials.render_upload(blank_pdf(501), '501-pages.pdf')
    assert rejected.value.status_code == 400 and '500' in rejected.value.detail


def test_upload_over_previous_file_and_http_limits_succeeds(tmp_path):
    # A valid PNG with a benign padded trailer exercises >20MB and >25MB together.
    stream = io.BytesIO()
    Image.new('RGB', (100, 100), 'white').save(stream, 'PNG')
    content = stream.getvalue() + b'\0' * (26 * 1024 * 1024)
    with TestClient(create_app(storage_dir=tmp_path, start_worker=False)) as client:
        login = client.post('/api/login', json={'username': 'student', 'password': 'Demo@2026'})
        assert login.status_code == 200
        client.headers['X-CSRF-Token'] = login.json()['csrf_token']
        bootstrap = client.get('/api/bootstrap').json()
        application = client.post('/api/applications', json={'category_id': bootstrap['categories'][0]['id']}).json()
        result = client.post(f"/api/applications/{application['id']}/files", files={'file': ('large.png', content, 'image/png')})
        assert result.status_code == 200, result.text
        assert result.json()['byte_size'] == len(content)
        assert client.get(result.json()['url']).content == content
        too_large = client.post('/api/applications', json={}, headers={'Content-Length': str(MAX_REQUEST_BYTES + 1)})
        assert too_large.status_code == 413 and '200MB' in too_large.json()['detail']


def test_file_boundary_accepts_200mb_and_rejects_one_byte_more():
    assert MAX_ATTACHMENT_BYTES == 200 * 1024 * 1024
    stream = io.BytesIO()
    Image.new('RGB', (10, 10), 'white').save(stream, 'PNG')

    class DeclaredSize(bytes):
        # Keep the test light while exercising the production decoder at the boundary.
        def __len__(self):
            return self.declared_size

    content = DeclaredSize(stream.getvalue())
    content.declared_size = MAX_ATTACHMENT_BYTES
    assert materials.render_upload(content, 'boundary.png')[0] == 'image/png'
    content.declared_size += 1
    with pytest.raises(HTTPException) as rejected:
        materials.render_upload(content, 'oversized.png')
    assert rejected.value.status_code == 400 and '200MB' in rejected.value.detail
