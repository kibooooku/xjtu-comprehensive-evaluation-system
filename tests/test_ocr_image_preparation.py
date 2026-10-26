"""Every Tencent path sends bounded JPEG copies and retains original geometry."""
import base64
import hashlib
import io
import json
import random

import httpx
import pytest
from PIL import Image

from app import integrations, materials, pdf_text
from app.integrations import ProviderError
from test_materials import material_app, create_claim, png, upload


CONFIG = {'secret_id': 'synthetic', 'secret_key': 'synthetic', 'region': 'ap-shanghai'}


@pytest.fixture(scope='module')
def large_image():
    size = (2200, 3000)
    image = Image.frombytes('RGB', size, random.Random(42).randbytes(size[0] * size[1] * 3))
    stream = io.BytesIO()
    image.save(stream, 'PNG')
    return stream.getvalue(), size


@pytest.mark.parametrize('action', ['GeneralAccurateOCR', 'SealOCR'])
def test_actual_large_raster_is_compressed_before_single_http_and_coordinates_restore(monkeypatch, large_image, action):
    source, size = large_image
    digest = hashlib.sha256(source).hexdigest()
    calls = []
    client = httpx.Client

    def respond(request):
        payload = json.loads(request.content)
        raw = base64.b64decode(payload['ImageBase64'], validate=True)
        assert len(raw) <= integrations.OCR_IMAGE_BYTES_BUDGET
        assert len(payload['ImageBase64']) <= integrations.OCR_BASE64_BUDGET
        assert len(request.content) <= integrations.OCR_REQUEST_BYTES_BUDGET
        with Image.open(io.BytesIO(raw)) as image:
            assert image.format == 'JPEG' and max(image.size) <= integrations.OCR_MAX_IMAGE_SIDE
            width, height = image.size
        expected_headers = integrations.tencent_headers(action, request.content, CONFIG, int(request.headers['X-TC-Timestamp']))
        assert request.headers['Authorization'] == expected_headers['Authorization']
        assert payload.get('IsWords', False) is (action == 'GeneralAccurateOCR')
        calls.append((width, height))
        point = {'X': width / 4, 'Y': height / 5}
        rect = {**point, 'Width': width / 2, 'Height': height / 3}
        value = {'RequestId': 'offline', 'TextDetections': [{'DetectedText': '合成文字', 'Confidence': 98,
                 'Polygon': [point], 'ItemPolygon': rect, 'WordCoordPoint': [{'WordCoordinate': [point]}]}],
                 'SealInfos': [{'Location': rect}], 'Location': rect}
        return httpx.Response(200, json={'Response': value})

    monkeypatch.setattr(integrations.httpx, 'Client', lambda *args, **kwargs: client(transport=httpx.MockTransport(respond)))
    result = integrations.tencent_ocr(action, source, CONFIG)
    point = {'X': round(size[0] / 4), 'Y': round(size[1] / 5)}
    rect = {**point, 'Width': round(size[0] / 2), 'Height': round(size[1] / 3)}
    assert result['TextDetections'][0]['Polygon'] == [point]
    assert result['TextDetections'][0]['WordCoordPoint'][0]['WordCoordinate'] == [point]
    assert result['TextDetections'][0]['ItemPolygon'] == result['SealInfos'][0]['Location'] == result['Location'] == rect
    assert len(calls) == 1 and hashlib.sha256(source).hexdigest() == digest
    assert result['_image_preprocess']['version'] == integrations.OCR_IMAGE_PREPROCESS_VERSION


@pytest.mark.parametrize('size', [(12000, 80), (80, 12000)])
def test_small_encoded_image_with_excessive_edge_is_still_scaled(monkeypatch, size):
    source = png(*size)
    assert len(source) < 50_000
    prepared, scale_x, scale_y = integrations.prepare_ocr_image(source)
    with Image.open(io.BytesIO(prepared)) as image:
        assert max(image.size) == integrations.OCR_MAX_IMAGE_SIDE
        assert image.format == 'JPEG'
        assert scale_x == size[0] / image.width and scale_y == size[1] / image.height


def test_transparency_has_white_background_and_even_small_files_are_prepared():
    image = Image.new('RGBA', (100, 140), (255, 0, 0, 0))
    stream = io.BytesIO()
    image.save(stream, 'PNG')
    prepared, scale_x, scale_y = integrations.prepare_ocr_image(stream.getvalue())
    with Image.open(io.BytesIO(prepared)) as output:
        assert output.format == 'JPEG' and output.size == image.size and output.mode == 'RGB'
        assert output.getpixel((50, 70)) == (255, 255, 255)
    assert (scale_x, scale_y) == (1.0, 1.0)


@pytest.mark.parametrize('constraint', ['OCR_IMAGE_BYTES_BUDGET', 'OCR_BASE64_BUDGET', 'OCR_REQUEST_BYTES_BUDGET'])
def test_any_impossible_budget_stops_before_http(monkeypatch, constraint):
    monkeypatch.setattr(integrations, constraint, 30)
    monkeypatch.setattr(integrations.httpx, 'Client', lambda *args, **kwargs: pytest.fail('no request may exceed a budget'))
    with pytest.raises(ProviderError) as caught:
        integrations.tencent_ocr('SealOCR', png(120, 180), CONFIG)
    assert caught.value.code == 'ImageTooLarge'


def test_provider_size_failure_is_not_blindly_retried(monkeypatch):
    calls = []
    client = httpx.Client

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={'Response': {'Error': {'Code': 'LimitExceeded.TooLargeFileError', 'Message': 'synthetic'}}})

    monkeypatch.setattr(integrations.httpx, 'Client', lambda *args, **kwargs: client(transport=httpx.MockTransport(respond)))
    with pytest.raises(ProviderError) as caught:
        integrations.tencent_ocr('GeneralAccurateOCR', png(), CONFIG)
    assert caught.value.code == 'LimitExceeded.TooLargeFileError' and len(calls) == 1


def test_searchable_pdf_seals_use_same_adapter_without_changing_native_coordinates(material_app, monkeypatch):
    _, login = material_app
    client = login('student')
    claim, _, _ = create_claim(client)
    stream = io.BytesIO()
    Image.new('RGB', (220, 300), 'white').save(stream, 'PDF')
    uploaded = client.post(f"/api/applications/{claim['id']}/files", files={'file': ('synthetic.pdf', stream.getvalue(), 'application/pdf')})
    assert uploaded.status_code == 200
    monkeypatch.setattr(pdf_text, 'extract_pages_text', lambda path, pages: {
        page['id']: {'usable': True, 'text': '合成活动名称，2026年10月26日', 'regions': [
            {'text': '合成活动名称，2026年10月26日', 'polygon_json': [[20, 30], [100, 30], [100, 50], [20, 50]], 'extra_json': {}}]} for page in pages})
    monkeypatch.setattr(materials, 'configuration', lambda: {**CONFIG, 'enabled': True, 'general_limit': 5, 'seal_limit': 5, 'max_analysis_pages': 12})
    monkeypatch.setattr(materials, 'tencent_ocr', integrations.tencent_ocr)
    requests = []
    http_client = httpx.Client

    def respond(request):
        requests.append(request.headers['X-TC-Action'])
        with Image.open(io.BytesIO(base64.b64decode(json.loads(request.content)['ImageBase64']))) as image:
            assert image.format == 'JPEG'
        return httpx.Response(200, json={'Response': {'RequestId': 'offline-seal', 'SealInfos': []}})

    monkeypatch.setattr(integrations.httpx, 'Client', lambda *args, **kwargs: http_client(transport=httpx.MockTransport(respond)))
    with materials.db.read() as conn:
        user = materials.one(conn, 'users', username='student')
    result = materials.run_ocr(claim['id'], user)
    assert result['status'] == 'SUCCEEDED' and result['native_text_page_count'] == 1
    assert requests == ['SealOCR']
    with materials.db.read() as conn:
        native = materials.rows(conn, 'recognition_runs', api_name=materials.NATIVE_TEXT_API)[0]
        assert materials.rows(conn, 'recognition_regions', run_id=native['id'])[0]['polygon_json'] == [[20, 30], [100, 30], [100, 50], [20, 50]]
