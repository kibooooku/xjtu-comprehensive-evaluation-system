"""Explicit, one-page real-provider V2 background-queue smoke test. No real student data."""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient
from app.main import create_app
from app import db
from scripts.smoke_cloud import synthetic_proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cloud', action='store_true')
    args = parser.parse_args()
    if not args.cloud:
        parser.error('Pass --cloud to explicitly enable real provider calls.')
    storage = Path('.test-data/live-v2').resolve()
    with TestClient(create_app(storage_dir=storage)) as client:
        logged = client.post('/api/login', json={'username': 'student', 'password': 'Demo@2026'})
        logged.raise_for_status()
        client.headers['X-CSRF-Token'] = logged.json()['csrf_token']
        boot = client.get('/api/bootstrap').json()
        parent = client.post('/api/submissions', json={'category_id': boot['categories'][0]['id']}).json()
        if parent['items']:
            app = parent['items'][0]
        else:
            response = client.post(f"/api/submissions/{parent['id']}/items", json={'staged': True})
            response.raise_for_status()
            app = response.json()
            uploaded = client.post(f"/api/applications/{app['id']}/files", files={'file': ('synthetic-proof-v2.png', synthetic_proof(), 'image/png')})
            uploaded.raise_for_status()
            assert client.get('/api/analysis-jobs').json()['jobs'] == []
            confirmed = client.post(f"/api/submissions/{parent['id']}/confirm-upload", json={'item_ids': [app['id']]})
            confirmed.raise_for_status()
        response = client.post(f"/api/applications/{app['id']}/jobs", json={'kind': 'AI'})
        response.raise_for_status()
        ai_id = response.json()['job']['id']
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            client.get('/api/bootstrap')  # Navigation requests do not own/cancel work.
            ai = client.get('/api/analysis-jobs/' + ai_id).json()
            if ai['state'] not in ('QUEUED', 'RUNNING'):
                break
            time.sleep(.5)
        if ai['state'] != 'SUCCEEDED':
            print(json.dumps({'status': ai['state'], 'message': ai.get('error_message')}, ensure_ascii=True))
            return 1
        detail = client.get(f"/api/applications/{app['id']}").json()
        participation = [crop for crop in detail['crops'] if crop['evidence_type'] == 'PARTICIPATION']
        assert len(participation) == 1
        page = next(p for file in detail['files'] for p in file['pages'] if p['id'] == participation[0]['page_id'])
        assert participation[0]['box'][2] == page['width_px']
        assert participation[0]['box'][3] == round(page['height_px'] / 4)
        assert participation[0]['highlights']
        assert detail['requested_score'] == '0.60'
        assert '广场舞' in detail['title']
        assert ai['result_json'].get('automatically_applied') is True
        usage = client.get('/api/integrations/status').json()
        report = {'status': 'SUCCEEDED', 'title': detail['title'], 'score': detail['requested_score'],
                  'automatically_applied': True, 'participation_count': len(participation),
                  'crop_dimensions': participation[0]['box'][2:], 'original_page_dimensions': [page['width_px'], page['height_px']],
                  'page_number': participation[0]['page_number'], 'highlight_fields': [h['field'] for h in participation[0]['highlights']],
                  'validity_kinds': sorted({crop.get('validity_kind') for crop in detail['crops'] if crop['evidence_type'] == 'VALIDITY'}),
                  'general_used': usage['general']['used'], 'seal_used': usage['seal']['used']}
        output = Path('docs/v2')
        (output / 'screenshots').mkdir(parents=True, exist_ok=True)
        (output / 'real-provider-smoke.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        image = client.get(participation[0]['url'])
        image.raise_for_status()
        (output / 'screenshots/participation-context.png').write_bytes(image.content)
        print(json.dumps(report, ensure_ascii=True))
        return 0


if __name__ == '__main__':
    sys.exit(main())
