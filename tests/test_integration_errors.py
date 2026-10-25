"""Provider failures must be distinguishable without exposing keys or document text."""
import json

import httpx
import pytest

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
