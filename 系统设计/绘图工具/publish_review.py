"""Render and package the design review artifacts; no business system is implemented."""
from pathlib import Path
import concurrent.futures
import html
import json
import math
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
CHROME = Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe')

def rasterize(entry):
    image = ROOT / '图稿' / (entry['id'] + '.svg')
    element = ET.parse(image).getroot()
    box = [float(n) for n in element.attrib['viewBox'].split()]
    width, height = math.ceil(box[2] * 1.3), math.ceil(box[3] * 1.3)
    staging = ROOT / '.work' / 'raster'
    staging.mkdir(parents=True, exist_ok=True)
    wrapper = staging / (entry['id'] + '.html')
    wrapper.write_text('<!doctype html><meta charset="utf-8"><style>html,body{margin:0;width:100%;height:100%;overflow:hidden;background:white}img{display:block;width:100vw;height:100vh}</style><img src="' + image.as_uri() + '">', encoding='utf-8')
    output = image.with_suffix('.png')
    command = [str(CHROME), '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check', '--disable-background-networking', '--disable-component-update', '--disable-sync', '--hide-scrollbars', '--allow-file-access-from-files', '--force-device-scale-factor=1', '--virtual-time-budget=700', '--user-data-dir=' + str(ROOT / '.work' / 'chrome-render-profile'), '--screenshot=' + str(output), f'--window-size={width},{height}', wrapper.as_uri()]
    result = subprocess.run(command, capture_output=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode or not output.exists():
        raise RuntimeError('Chrome could not render ' + entry['id'])
    return {'id': entry['id'], 'width': width, 'height': height, 'bytes': output.stat().st_size}

def publish(index):
    encoded = json.dumps(index, ensure_ascii=False).replace('</', '<\\/')
    document = r'''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>德育分统计系统 · 设计评审</title>
<style>
*{box-sizing:border-box}body{margin:0;color:#203b50;background:#f3f6fa;font:14px "Microsoft YaHei",sans-serif}aside{position:fixed;inset:0 auto 0 0;width:280px;background:#172f43;color:#d7e6ee;padding:24px 17px;overflow:auto}h1{font-size:20px;margin:0 0 6px;color:white}aside small{color:#a5bac8}.nav{display:grid;gap:7px;margin-top:22px}.nav button{background:transparent;border:1px solid #375167;border-radius:7px;color:#d7e6ee;text-align:left;padding:10px;cursor:pointer;line-height:1.6}.nav button:hover,.nav button.active{background:#2a5874;border-color:#84c4d6}.nav b{font-size:11px;color:#9cd2df}main{margin-left:280px;padding:25px;min-height:100vh}header{display:flex;gap:20px;align-items:center;justify-content:space-between}h2{font-size:24px;margin:0}.pill{display:inline-block;padding:5px 10px;border-radius:20px;background:#e5eff5;color:#356078;font-size:12px}.tools{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:18px 0 12px}.tools button,.tools a{border:1px solid #b4c7d4;border-radius:6px;padding:8px 12px;background:white;color:#234a62;text-decoration:none;cursor:pointer}.tools span{font-variant-numeric:tabular-nums}.viewport{background:white;border:1px solid #ccd9e2;border-radius:10px;height:calc(100vh - 260px);min-height:400px;overflow:auto;padding:14px;box-shadow:0 3px 12px #172f4308}.viewport img{display:block;max-width:none;height:auto;transform-origin:top left}.notes{line-height:1.8;color:#526b7e;margin-top:12px}.docs{display:flex;gap:14px;flex-wrap:wrap;margin-top:10px}.docs a{color:#285d83}footer{font-size:12px;margin-top:13px;color:#6d8291}.meta{margin-top:9px;color:#657c8e}.tag{display:block;margin-bottom:7px}.hint{font-size:12px;color:#a3bacb;line-height:1.8;margin-top:20px}@media(max-width:850px){aside{position:static;width:auto}.nav{grid-template-columns:repeat(2,1fr)}main{margin:0;padding:15px}.viewport{height:65vh}header{display:block}}
</style>
<aside><h1>德育分统计系统</h1><small>系统设计 · V1 评审稿 · 2026-10-24</small><div class="nav" id="nav"></div><div class="hint">先看 01、04、08 了解整体。<br>一级 DFD 的 05a / 05b 为互补切片。<br>横向滚动或调整缩放查看细节。</div></aside>
<main><header><div><span class="pill" id="kind"></span><h2 id="title"></h2><div class="meta" id="meta"></div></div><span class="pill">仅设计 · 尚未进行业务实现</span></header>
<div class="tools"><button onclick="fit()">适应宽度</button><button onclick="zoom(-.15)">缩小</button><span id="scale"></span><button onclick="zoom(.15)">放大</button><a id="svgLink" target="_blank">打开 SVG 原图</a><a id="pngLink" target="_blank">打开 PNG</a><a href="VisualParadigm/德育分统计系统-设计评审.vpp">下载可编辑 VP 工程</a></div>
<div class="viewport" id="view"><img id="figure" alt="系统设计图"></div><div class="notes" id="notes"></div>
<div class="docs"><a href="设计评审说明.md">总设计说明</a><a href="RBAC与领域设计说明.md">RBAC 与领域模型</a><a href="数据库设计说明.md">数据库设计与约束</a><a href="数据流分析说明.md">数据流字典与层次平衡</a><a href="数据字典.md">33 张表完整数据字典</a></div>
<footer>清晰 SVG / PNG 从共享语义模型独立绘制；VisualParadigm/原生导出 保留试用版水印输出，未作去水印处理。腾讯云与 DeepSeek 均未被本轮制图调用。</footer></main>
<script>const diagrams=__INDEX__;let selected=null,ratio=1,base=1;const figure=document.getElementById('figure');const nav=document.getElementById('nav');for(const [i,d]of diagrams.entries()){const b=document.createElement('button');b.innerHTML='<b>'+d.id+'</b><br>'+d.name;b.onclick=()=>select(i);nav.append(b)}function update(){figure.style.width=Math.round(base*ratio)+'px';document.getElementById('scale').textContent=Math.round(ratio*100)+'%'}function fit(){if(!selected)return;ratio=Math.min(1,(document.getElementById('view').clientWidth-30)/base);update()}function zoom(amount){ratio=Math.max(.15,Math.min(3,ratio+amount));update()}function select(i){selected=diagrams[i];for(const [j,b]of [...nav.children].entries())b.classList.toggle('active',i===j);document.getElementById('title').textContent=selected.name;document.getElementById('kind').textContent={class:'UML 类图',dfd:'数据流分析',erd:'数据库 ER'}[selected.kind];document.getElementById('meta').textContent=selected.nodes+' 个节点 · '+selected.edges+' 条关系 / 数据流';const url='图稿/'+selected.id;figure.onload=()=>{base=figure.naturalWidth;fit()};figure.src=url+'.svg';document.getElementById('svgLink').href=url+'.svg';document.getElementById('pngLink').href=url+'.png';const n=document.getElementById('notes');n.textContent='';for(const line of selected.notes||[]){const p=document.createElement('div');p.textContent='• '+line;n.append(p)}document.getElementById('view').scrollTo(0,0);location.hash=selected.id}const initial=Math.max(0,diagrams.findIndex(d=>d.id===decodeURIComponent(location.hash.slice(1))));select(initial);</script></html>'''.replace('__INDEX__', encoded)
    (ROOT / '设计图总览.html').write_text(document, encoding='utf-8')

def contact_sheet(index):
    from PIL import Image, ImageDraw, ImageFont
    width, cell_height, columns = 1800, 330, 3
    font = ImageFont.truetype(r'C:\Windows\Fonts\msyh.ttc', 19)
    small = ImageFont.truetype(r'C:\Windows\Fonts\msyh.ttc', 15)
    sheet = Image.new('RGB', (width, math.ceil(len(index)/columns)*cell_height+90), '#eef3f8')
    draw = ImageDraw.Draw(sheet)
    draw.text((28,20), '德育分统计系统 · 14 张设计图索引', font=font, fill='#203b50')
    draw.text((28,52), '完整内容请打开 SVG / PNG 或设计图总览；原生 VP 工程可编辑', font=small, fill='#526b7e')
    for i, entry in enumerate(index):
        x=(i%columns)*(width//columns)+14; y=(i//columns)*cell_height+90
        draw.rounded_rectangle((x,y,x+570,y+312),radius=10,fill='white',outline='#ccd9e2')
        im=Image.open(ROOT/'图稿'/(entry['id']+'.png')).convert('RGB'); im.thumbnail((540,245))
        sheet.paste(im,(x+(570-im.width)//2,y+10+(245-im.height)//2))
        name=entry['name']; name=name if len(name)<28 else name[:27]+'…'
        draw.text((x+14,y+262),entry['id'],font=small,fill='#285d83')
        draw.text((x+14,y+285),name,font=small,fill='#203b50')
    sheet.save(ROOT/'设计图预览.png')

def main():
    index=json.loads((ROOT/'源文件'/'diagram-index.json').read_text(encoding='utf-8'))
    results=[]
    for entry in index:
        result=rasterize(entry); results.append(result); print(json.dumps(result),flush=True)
    publish(index); contact_sheet(index)
    (ROOT/'源文件'/'raster-report.json').write_text(json.dumps(results,indent=2),encoding='utf-8')

if __name__=='__main__': main()
