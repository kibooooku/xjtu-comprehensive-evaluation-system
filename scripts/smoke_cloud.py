"""One synthetic page exercises real OCR + DeepSeek through the local application.

Run explicitly with --cloud. It uses at most one GeneralAccurateOCR + one SealOCR
request (cached on repeat), plus one DeepSeek generation. No real student data.
"""
import argparse
import io
import json
import sys
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont


def synthetic_proof():
    image = Image.new('RGB', (1200, 900), 'white')
    draw = ImageDraw.Draw(image)
    font_path = Path('C:/Windows/Fonts/msyh.ttc')
    if not font_path.exists():
        font_path = Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    title = ImageFont.truetype(str(font_path), 48)
    body = ImageFont.truetype(str(font_path), 32)
    draw.text((320, 90), '集体活动参与证明', font=title, fill='#172b4d')
    lines = ['仅用于软件测试的虚构材料', '姓名：王欣然    学号：DEMO202401004',
             '班级：软件2401', '参加广场舞活动2次，每次0.3分，共0.6分。',
             '活动时间：2025年10月12日、2025年10月19日', '本图不具备真实证明效力。']
    for i, line in enumerate(lines):
        draw.text((100, 230 + i * 70), line, font=body, fill='#202733')
    draw.ellipse((880, 600, 1100, 820), outline='#b82025', width=7)
    draw.text((915, 677), '测试用章', font=body, fill='#b82025')
    stream = io.BytesIO()
    image.save(stream, 'PNG')
    return stream.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cloud', action='store_true', help='Explicitly call configured providers.')
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    args = parser.parse_args()
    if not args.cloud:
        parser.error('Use --cloud to explicitly enable real provider calls.')
    with httpx.Client(base_url=args.base_url, timeout=240) as client:
        response = client.post('/api/login', json={'username': 'student', 'password': 'Demo@2026'})
        response.raise_for_status()
        client.headers['X-CSRF-Token'] = response.json()['csrf_token']
        boot = client.get('/api/bootstrap').json()
        status = client.get('/api/integrations/status')
        status.raise_for_status()
        print(json.dumps({'integration_status': status.json()}, ensure_ascii=True))
        category = next(c for c in boot['categories'] if c['code'] == 'COLLECTIVE')
        created = client.post('/api/applications', json={'year_id': boot['years'][0]['id'], 'category_id': category['id']})
        created.raise_for_status()
        app_id = created.json()['id']
        uploaded = client.post(f'/api/applications/{app_id}/files', files={'file': ('synthetic-proof.png', synthetic_proof(), 'image/png')})
        uploaded.raise_for_status()
        result = client.post(f'/api/applications/{app_id}/analyze', json={})
        result.raise_for_status()
        data = result.json()
        summary = {'application_id': app_id, 'status': data.get('status'), 'message': data.get('message'),
                   'analysis_id': data.get('analysis_id') or (data.get('analysis') or {}).get('id'),
                   'suggestion': data.get('suggestion'), 'warnings': data.get('warnings'),
                   'manual_fallback': data.get('manual_fallback')}
        print(json.dumps(summary, ensure_ascii=True, default=str))
        report = Path('instance/cloud-smoke-result.json')
        report.parent.mkdir(exist_ok=True)
        report.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        return 0 if data.get('status') in ('SUCCEEDED', 'SUCCESS', 'COMPLETED') else 1


if __name__ == '__main__':
    sys.exit(main())
