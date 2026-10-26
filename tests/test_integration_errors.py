"""Provider failures must be distinguishable without exposing keys or document text."""
import json
import io

import httpx
import pytest
from PIL import Image

from app import integrations
from app.integrations import ProviderError


CONFIG = {'deepseek_key': 'fake-private-key', 'deepseek_model': 'test-model'}


def use_response(monkeypatch, response=None, error=None):
    class Client:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def post(self, *args, **kwargs):
            if error:
                raise error
            return response
    monkeypatch.setattr(integrations.httpx, 'Client', Client)


@pytest.mark.parametrize(('error_type', 'code'), [(httpx.ReadTimeout, 'Timeout'), (httpx.ConnectError, 'ConnectionError'),
                                                  (httpx.RemoteProtocolError, 'ProtocolError'), (httpx.ReadError, 'NetworkError')])
def test_transport_errors_are_separate_and_sanitized(monkeypatch, error_type, code):
    use_response(monkeypatch, error=error_type('DO NOT EXPOSE fake-private-key or private proof'))
    with pytest.raises(ProviderError) as caught:
        integrations.deepseek_suggestion({}, CONFIG)
    assert caught.value.code == code
    assert 'fake-private-key' not in caught.value.message
    assert 'private proof' not in caught.value.message


@pytest.mark.parametrize(('status', 'code'), [(401, 'AuthenticationFailed'), (402, 'InsufficientBalance'),
                                           (429, 'RateLimited'), (503, 'HTTP503')])
def test_provider_status_has_its_own_error(monkeypatch, status, code):
    use_response(monkeypatch, httpx.Response(status, json={'error': 'DO NOT EXPOSE fake-private-key'}))
    with pytest.raises(ProviderError) as caught:
        integrations.deepseek_suggestion({}, CONFIG)
    assert caught.value.code == code
    assert 'fake-private-key' not in caught.value.message


@pytest.mark.parametrize(('body', 'code'), [(b'broken response', 'ResponseJSONError'),
    ({}, 'UnexpectedResponse'), ({'choices': []}, 'UnexpectedResponse'),
    ({'choices': [{'finish_reason': 'length', 'message': {'content': '{}'}}]}, 'OutputTruncated'),
    ({'choices': [{'finish_reason': 'stop', 'message': {'content': ''}}]}, 'EmptyOutput'),
    ({'choices': [{'finish_reason': 'stop', 'message': {'content': '{bad json'}}]}, 'OutputJSONError'),
    ({'choices': [{'finish_reason': 'stop', 'message': {'content': '[]'}}]}, 'OutputFormatError')])
def test_response_and_generated_content_failures_are_distinct(monkeypatch, body, code):
    response = httpx.Response(200, content=body) if isinstance(body, bytes) else httpx.Response(200, json=body)
    use_response(monkeypatch, response)
    with pytest.raises(ProviderError) as caught:
        integrations.deepseek_suggestion({}, CONFIG)
    assert caught.value.code == code


def test_json_object_in_markdown_wrapper_is_parsed_without_a_second_request(monkeypatch):
    use_response(monkeypatch, httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
        'content': '```json\n' + json.dumps({'title': '具体活动', 'requested_score': '.3'}) + '\n```'}}]}))
    assert integrations.deepseek_suggestion({}, CONFIG)['title'] == '具体活动'


@pytest.mark.parametrize(('page_limit', 'expected'), [(None, 12), ('500', 12), ('7', 7)])
def test_automatic_ocr_has_a_twelve_page_ceiling_without_raising_monthly_budget(monkeypatch, page_limit, expected):
    if page_limit is None:
        monkeypatch.delenv('OCR_MAX_ANALYSIS_PAGES', raising=False)
    else:
        monkeypatch.setenv('OCR_MAX_ANALYSIS_PAGES', page_limit)
    monkeypatch.setattr(integrations, 'dotenv_values', lambda path: {})
    monkeypatch.setenv('OCR_GENERAL_MONTHLY_LIMIT', '100')
    monkeypatch.setenv('OCR_SEAL_MONTHLY_LIMIT', '100')
    configured = integrations.configuration()
    assert configured['max_analysis_pages'] == expected
    assert configured['general_limit'] == configured['seal_limit'] == 100


def test_default_local_attempt_limits_are_not_a_provider_balance(monkeypatch, tmp_path):
    monkeypatch.setattr(integrations, 'PROJECT_ROOT', tmp_path)
    monkeypatch.delenv('OCR_GENERAL_MONTHLY_LIMIT', raising=False)
    monkeypatch.delenv('OCR_SEAL_MONTHLY_LIMIT', raising=False)
    configured = integrations.configuration()
    assert configured['general_limit'] == configured['seal_limit'] == 1000


@pytest.mark.parametrize('code,source', [
    ('LocalBudgetExceeded', 'LOCAL_LIMIT'),
    ('ResourcesSoldOut.ResourcePackageRunOut', 'PROVIDER_RESOURCE'),
    ('ResourceUnavailable.ResourcePackageRunOut', 'PROVIDER_RESOURCE'),
    ('FailedOperation.InArrears', 'BILLING'),
    ('UnsupportedOperation.ChargeStatusException', 'BILLING'),
    ('RequestLimitExceeded', 'RATE_LIMIT'),
    ('LimitExceeded.Quota', 'RATE_LIMIT'),
    ('LimitExceeded.TooLargeFileError', 'INPUT_LIMIT'),
    ('ImageTooLarge', 'INPUT_LIMIT'),
    ('AuthFailure.SecretIdNotFound', 'AUTH'),
    ('UnauthorizedOperation', 'AUTH'),
    ('NetworkError', 'NETWORK'),
    ('HTTPError', 'NETWORK'),
    ('NotConfigured', 'CONFIGURATION'),
    ('ResourceUnavailable', 'PROVIDER_UNAVAILABLE'),
    ('ResourcesSoldOut', 'PROVIDER_UNAVAILABLE'),
    ('UnsupportedOperation.UnOpenError', 'PROVIDER_UNAVAILABLE'),
    ('FailedOperation.OcrFailed', 'UNKNOWN'),
])
def test_ocr_stop_source_uses_exact_provider_codes_not_message_keywords(code, source):
    details = {'action': 'SealOCR', 'used': 100, 'limit': 100, 'private': 'must-not-leak'}
    result = integrations.ocr_stop_info(code, details)
    assert result['stop_code'] == code and result['stop_source'] == source
    assert result['stop_details'] == ({'action': 'SealOCR', 'used': 100, 'limit': 100} if source == 'LOCAL_LIMIT' else {})
    assert 'must-not-leak' not in json.dumps(result)
    if source == 'LOCAL_LIMIT':
        assert '不代表腾讯云免费额度不足' in result['stop_message']


def test_unknown_stop_cannot_echo_private_text_or_claim_cloud_balance():
    result = integrations.ocr_stop_info('private proof / secret-key', {'used': True, 'limit': 'private proof'})
    assert result['stop_code'] == 'UnknownError' and result['stop_source'] == 'UNKNOWN'
    assert result['stop_details'] == {} and 'private proof' not in json.dumps(result)


def test_no_stop_returns_explicit_null_metadata():
    assert integrations.ocr_stop_info(None) == {'stop_code': None, 'stop_source': None, 'stop_message': None, 'stop_details': {}}


@pytest.mark.parametrize('code', ['ResourcesSoldOut.ResourcePackageRunOut', 'FailedOperation.InArrears',
                                 'RequestLimitExceeded', 'LimitExceeded.TooLargeFileError'])
def test_tencent_error_messages_preserve_stop_source_instead_of_claiming_free_exhaustion(monkeypatch, code):
    use_response(monkeypatch, httpx.Response(200, json={'Response': {'Error': {'Code': code, 'Message': 'private-provider-message'}}}))
    image = io.BytesIO()
    Image.new('RGB', (40, 40), 'white').save(image, 'PNG')
    with pytest.raises(ProviderError) as caught:
        integrations.tencent_ocr('SealOCR', image.getvalue(), {'secret_id': 'test-id', 'secret_key': 'test-key', 'region': 'ap-shanghai'})
    assert caught.value.code == code
    assert caught.value.message == integrations.ocr_stop_info(code)['stop_message']
    assert 'private-provider-message' not in caught.value.message
