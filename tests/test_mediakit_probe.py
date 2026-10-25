"""Synthetic benchmark fixtures only. No real key, application database or cloud."""
import io
import json
from pathlib import Path

import httpx
import pytest
from PIL import Image

from scripts import probe_mediakit as probe

KEY = "synthetic-mediakit-key-do-not-leak-123456"
SIGNED_URL = "https://tob-upload-y.volcvod.com/private-image?Authorization=temporary-secret-signature"


def sample(tmp_path, name="s01", color="white", dimensions=(300, 200)):
    image = tmp_path / (name + ".png")
    Image.new("RGB", dimensions, color).save(image)
    return {"id": name, "image_path": str(image.resolve()), "source_page_number": 3, "expected_tokens": {"name": "测试人甲", "activity": ["合成活动", "备选活动"], "date": ""}}


def transport_handler(calls, *, ocr_status=200, timeout=False, bare_id=False):
    def handle(request):
        calls.append(request)
        if request.url.host == "mediakit.cn-beijing.volces.com":
            assert request.headers["Authorization"] == "Bearer " + KEY
            if request.url.path == probe.UPLOAD_API:
                return httpx.Response(200, json={"success": True, "result": {"file_id": "file-id" if bare_id else "mediakit://file-id", "method": "PUT", "upload_headers": [{"key": "X-Upload-Test", "value": "forward-exactly"}], "upload_url": SIGNED_URL}})
            assert request.url.path == probe.OCR_API
            payload = json.loads(request.content)
            assert payload == {"image_url": "mediakit://file-id", "tool_version": "standard"}
            if timeout:
                raise httpx.ReadTimeout("sensitive exception " + KEY + " " + SIGNED_URL, request=request)
            if ocr_status != 200:
                return httpx.Response(ocr_status, text="sensitive response " + KEY + " " + SIGNED_URL)
            return httpx.Response(200, json={"success": True, "request_id": "synthetic-request", "upload_url": SIGNED_URL, "Authorization": KEY, "result": {"ocr_result": [{"content": "测试人甲参与合成活动<script>alert(1)</script>", "confidence": 0.98, "top_left_x": 10, "top_left_y": 20, "bottom_right_x": 100, "bottom_right_y": 50}], "debug": {"key": KEY, "url": SIGNED_URL}}})
        assert request.url.host == "tob-upload-y.volcvod.com"
        assert request.method == "PUT"
        assert "authorization" not in request.headers
        assert KEY.encode() not in b"\n".join(name + b": " + value for name, value in request.headers.raw)
        assert request.headers["X-Upload-Test"] == "forward-exactly"
        assert request.headers["Content-Type"] == "image/png"
        assert request.content.startswith(b"\x89PNG")
        assert "multipart" not in request.headers["Content-Type"]
        return httpx.Response(200)
    return handle


def test_standard_upload_isolates_bearer_maps_boxes_and_reuses_cache(tmp_path):
    item = sample(tmp_path, dimensions=(3001, 4001))
    calls = []
    client = httpx.Client(transport=httpx.MockTransport(transport_handler(calls)), headers={"Authorization": "Bearer " + KEY}, auth=httpx.BasicAuth("unrelated", "never-forward"))
    output = tmp_path / "output"
    tool = probe.Probe(output, cloud=True, api_key=KEY, client=client)
    result = tool.run([item])
    assert result["samples"][0]["state"] == "SUCCEEDED"
    assert len(calls) == 3 and result["ocr_attempts_this_run"] == 1
    entry = result["samples"][0]
    assert entry["source_page_number"] == 3
    assert min(entry["uploaded_size"]) <= 2160 and max(entry["uploaded_size"]) <= 3840
    expected_x = round(10 * 3001 / entry["uploaded_size"][0], 3)
    expected_y = round(20 * 4001 / entry["uploaded_size"][1], 3)
    assert entry["blocks"][0]["original_box"][:2] == [expected_x, expected_y]
    assert entry["tokens"]["fields"]["name"]["exact_match"] is True
    assert entry["tokens"]["fields"]["activity"]["alternative_matches"] == [True, False]
    assert "date" not in entry["tokens"]["fields"]
    assert entry["blocks"][0]["confidence"] == 0.98
    overlay = Image.open(output / entry["overlay_image"])
    assert overlay.size == (3001, 4001)
    assert "&lt;script&gt;" in (output / "report.html").read_text(encoding="utf-8")
    for file in output.rglob("*"):
        if file.is_file():
            content = file.read_bytes()
            assert KEY.encode() not in content
            assert SIGNED_URL.encode() not in content
            assert b"temporary-secret-signature" not in content
    client.close()
    never_network = httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("cache must not access network")))
    rerun = probe.Probe(output, cloud=True, api_key=KEY, client=never_network).run([item])
    assert rerun["samples"][0]["state"] == "CACHED"
    assert rerun["ocr_attempts_this_run"] == 0
    assert rerun["ledger"]["attempts"] == 1
    never_network.close()


@pytest.mark.parametrize("timeout", [False, True])
def test_failure_stops_batch_and_is_not_retried_across_runs(tmp_path, timeout):
    first, second = sample(tmp_path), sample(tmp_path, "s02", color="blue")
    calls = []
    client = httpx.Client(transport=httpx.MockTransport(transport_handler(calls, ocr_status=503, timeout=timeout)))
    output = tmp_path / "output"
    result = probe.Probe(output, cloud=True, api_key=KEY, client=client).run([first, second])
    assert result["samples_checked"] == 1
    assert result["samples"][0]["state"] == "FAILED"
    assert result["samples"][0]["error_code"] == ("TIMEOUT" if timeout else "PROVIDER_UNAVAILABLE")
    assert result["ledger"]["attempts"] == 1
    assert len(calls) == 3
    report_text = (output / "report.json").read_text(encoding="utf-8") + (output / "report.html").read_text(encoding="utf-8")
    assert KEY not in report_text and SIGNED_URL not in report_text
    client.close()
    no_network = httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("failed OCR must not be retried implicitly")))
    repeated = probe.Probe(output, cloud=True, api_key=KEY, client=no_network).run([first])
    assert repeated["samples"][0]["error_code"] == "PREVIOUS_ATTEMPT_FAILED"
    assert repeated["ocr_attempts_this_run"] == 0
    no_network.close()


def test_cap_is_cumulative_and_active_attempt_never_retries(tmp_path):
    first, second = sample(tmp_path), sample(tmp_path, "s02", color="blue")
    calls = []
    client = httpx.Client(transport=httpx.MockTransport(transport_handler(calls, bare_id=True)))
    output = tmp_path / "output"
    result = probe.Probe(output, cloud=True, api_key=KEY, client=client, max_calls=1).run([first, second])
    assert result["samples"][0]["state"] == "SUCCEEDED"
    assert result["samples"][1]["error_code"] == "CALL_CAP_REACHED"
    assert result["ledger"]["attempts"] == 1 and len(calls) == 3
    client.close()
    with pytest.raises(probe.ProbeError, match="1至10"):
        probe.Probe(tmp_path / "invalid", max_calls=11)
    ledger = probe.Ledger(tmp_path / "active")
    ledger.reserve("pending-image", 8)
    with pytest.raises(probe.ProbeError) as active:
        ledger.reserve("pending-image", 8, retry_failed=True)
    assert active.value.code == "PREVIOUS_ATTEMPT_UNCERTAIN"
    assert ledger.stats()["attempts"] == 1


def test_offline_never_loads_key_and_shows_pending_with_cached_tencent_comparison(tmp_path, monkeypatch):
    item = sample(tmp_path)
    cache = tmp_path / "tencent.json"
    cache.write_text(json.dumps({"TextDetections": [{"DetectedText": "测试人甲参与合成活动"}]}), encoding="utf-8")
    item["tencent_result_path"] = str(cache.resolve())
    second = sample(tmp_path, "s02", color="blue")
    second["expected_tokens"] = {}
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"samples": [item, second]}, ensure_ascii=False), encoding="utf-8-sig")
    monkeypatch.setattr(probe, "ROOT", tmp_path)
    monkeypatch.setattr(probe, "load_key", lambda: pytest.fail("offline must not read credentials"))
    monkeypatch.setattr(httpx.Client, "send", lambda *args, **kwargs: pytest.fail("offline must not access network"))
    assert probe.main(["--manifest", str(manifest)]) == 0
    report_dir = tmp_path / "instance" / "mediakit-test"
    result = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
    assert result["mode"] == "offline" and result["ledger"]["attempts"] == 0
    assert result["samples"][0]["tokens"]["checked_fields"] == 0
    assert result["samples"][0]["tencent"]["tokens"]["fields"]["name"]["exact_match"] is True
    html = (report_dir / "report.html").read_text(encoding="utf-8")
    assert "尚未评价" in html and "无历史结果" in html and "既存腾讯缓存" in html
    assert "原件第3页" in html


def test_untrusted_upload_target_fails_before_sending_credentials_or_ocr(tmp_path):
    item = sample(tmp_path)
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.host == "mediakit.cn-beijing.volces.com"
        return httpx.Response(200, json={"success": True, "result": {"file_id": "mediakit://unsafe", "method": "PUT", "upload_headers": [], "upload_url": "https://volces.com.evil.invalid/?Signature=not-safe"}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = probe.Probe(tmp_path / "output", cloud=True, api_key=KEY, client=client).run([item])
    assert result["samples"][0]["error_code"] == "UNTRUSTED_UPLOAD"
    assert len(calls) == 1 and result["ledger"]["attempts"] == 0
    assert not probe.trusted_upload_url("https://volcvod.com@evil.invalid/image")
    assert not probe.trusted_upload_url("http://upload.volces.com/image")
    client.close()


@pytest.mark.parametrize("specific_key_error", [True, False])
def test_http403_unknown_key_is_classified_before_generic_permission_error(tmp_path, specific_key_error):
    item = sample(tmp_path)
    calls = []
    synthetic_masked_key = "api-FABRICATED-MASKED-ONLY"
    provider_message = ("InvalidParameter.InvalidAPIKey:Get api key error: platform cannot find " + synthetic_masked_key) if specific_key_error else "The service has not granted access to this resource."

    def handler(request):
        calls.append(request)
        assert request.url.host == "mediakit.cn-beijing.volces.com"
        assert request.url.path == probe.UPLOAD_API
        return httpx.Response(403, json={"success": False, "error": {"code": "AccessDenied", "type": "Forbidden", "message": provider_message}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    output = tmp_path / "output"
    result = probe.Probe(output, cloud=True, api_key=KEY, client=client).run([item, sample(tmp_path, "s02", color="blue")])
    assert result["samples_checked"] == 1 and len(calls) == 1
    assert result["ledger"]["attempts"] == 0
    entry = result["samples"][0]
    assert entry["error_code"] == ("INVALID_API_KEY" if specific_key_error else "PERMISSION_DENIED")
    if specific_key_error:
        assert entry["message"] == "平台找不到此API Key，请核对创建入口及完整Key值。"
    saved = (output / "report.json").read_text(encoding="utf-8") + (output / "report.html").read_text(encoding="utf-8")
    assert provider_message not in saved and synthetic_masked_key not in saved and KEY not in saved
    client.close()


def test_reversed_coordinate_block_retains_text_without_a_box_and_cache_keeps_ocr_time(tmp_path):
    item = sample(tmp_path, dimensions=(2000, 1600))
    item["expected_tokens"] = {"name": "异常框"}
    blocks = [{"content": f"合成文本{index}", "confidence": 0.99, "top_left_x": 10, "top_left_y": 10 + index * 25, "bottom_right_x": 160, "bottom_right_y": 30 + index * 25} for index in range(18)]
    blocks[8] = {"content": "异常框", "confidence": 0.985879, "top_left_x": 1414, "top_left_y": 1148, "bottom_right_x": 1606, "bottom_right_y": 1143}
    calls = []
    upload_handler = transport_handler(calls)

    def handler(request):
        if request.url.path == probe.OCR_API:
            calls.append(request)
            assert request.url.host == "mediakit.cn-beijing.volces.com"
            assert json.loads(request.content)["tool_version"] == "standard"
            return httpx.Response(200, json={"success": True, "request_id": "synthetic-reversed-coordinates", "result": {"ocr_result": blocks}})
        return upload_handler(request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    folder = tmp_path / "output"
    tool = probe.Probe(folder, cloud=True, api_key=KEY, client=client)
    report = tool.run([item])
    entry = report["samples"][0]
    assert entry["state"] == "SUCCEEDED" and len(entry["blocks"]) == 18
    assert entry["position_warning_blocks"] == 1
    bad = entry["blocks"][8]
    assert bad["content"] == "异常框" and bad["confidence"] == 0.985879
    assert bad["position_valid"] is False and bad["position_warning"]
    assert bad["original_box"] is None
    assert bad["top_left_y"] == 1148 and bad["bottom_right_y"] == 1143  # Never silently swap.
    assert sum(block["position_valid"] for block in entry["blocks"]) == 17
    matched = entry["tokens"]["fields"]["name"]
    assert matched["exact_match"] is True and matched["reliable_position_match"] is False
    assert matched["matched_boxes"] == [] and matched["unpositioned_matches"] == 1
    overlay = Image.open(folder / entry["overlay_image"])
    assert overlay.getpixel((1414, 1143)) == (255, 255, 255)
    assert overlay.getpixel((10, 10)) == (223, 37, 37)
    html = (folder / "report.html").read_text(encoding="utf-8")
    assert "定位异常 1 块" in html and "无可靠定位框" in html
    assert report["estimated_success_cost_cny_total"] == "0.00138"
    assert report["estimated_possible_cost_cny_total"] == "0.00138"
    # Existing successful rows may predate the new elapsed field in result_json.
    with tool.ledger.connect() as conn:
        row = conn.execute("SELECT id,result_json FROM attempts WHERE state='SUCCEEDED'").fetchone()
        payload = json.loads(row[1])
        payload.pop("ocr_elapsed_seconds", None)
        conn.execute("UPDATE attempts SET elapsed=?,result_json=? WHERE id=?", (1.234, json.dumps(payload), row[0]))
    client.close()
    no_network = httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("cached repair must not call cloud")))
    cached = probe.Probe(folder, cloud=True, api_key=KEY, client=no_network).run([item])
    assert cached["samples"][0]["state"] == "CACHED"
    assert cached["samples"][0]["ocr_elapsed_seconds"] == 1.234
    assert cached["samples"][0]["position_warning_blocks"] == 1
    assert cached["ocr_attempts_this_run"] == 0 and cached["ledger"]["attempts"] == 1
    assert cached["estimated_success_cost_cny_this_run"] == "0.00000"
    assert cached["estimated_success_cost_cny_total"] == "0.00138"
    assert "原OCR接口耗时（缓存保留）：1.234 秒" in (folder / "report.html").read_text(encoding="utf-8")
    no_network.close()
