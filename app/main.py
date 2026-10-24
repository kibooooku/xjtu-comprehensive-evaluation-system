from __future__ import annotations
import hmac
import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError, StatementError

from . import db
from .security import COOKIE, csrf_token


def create_app(database_url=None, storage_dir=None, seed_demo=True):
    load_dotenv(db.ROOT / '.env', override=False)
    db.configure(database_url, storage_dir)
    if seed_demo:
        from .seed import initialize
        initialize()
    application = FastAPI(title='西安交通大学德育分统计管理系统', version='1.0.0', docs_url='/api/docs', redoc_url=None)

    @application.middleware('http')
    async def secure_request(request: Request, call_next):
        length = request.headers.get('content-length')
        if length:
            try:
                if int(length) < 0:
                    raise ValueError
                if int(length) > 25 * 1024 * 1024:
                    return JSONResponse({'detail': '请求体不能超过25MB，单文件不能超过20MB'}, 413)
            except ValueError:
                return JSONResponse({'detail': '请求长度无效'}, 400)
        if request.method in ('POST', 'PUT', 'DELETE', 'PATCH') and request.url.path.startswith('/api/'):
            origin = request.headers.get('origin')
            if origin:
                target = urlsplit(origin)
                if target.netloc != request.headers.get('host') or target.scheme != request.url.scheme:
                    return JSONResponse({'detail': '请求来源验证失败'}, 403)
            token = request.cookies.get(COOKIE)
            if request.url.path != '/api/login' and token:
                provided = request.headers.get('x-csrf-token', '')
                if not hmac.compare_digest(provided, csrf_token(token)):
                    return JSONResponse({'detail': '请求已失效，请刷新页面后重试'}, 403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @application.exception_handler(IntegrityError)
    async def integrity_failure(request, exc):
        return JSONResponse({'detail': '数据存在重复或关联冲突，请刷新后重试'}, 409)

    @application.exception_handler(StatementError)
    async def statement_failure(request, exc):
        return JSONResponse({'detail': '数据格式无效，请检查填写内容'}, 400)

    @application.get('/api/health')
    def health():
        with db.read() as conn:
            conn.exec_driver_sql('SELECT 1')
        return {'status': 'ok', 'version': '1.0.0'}

    from .organization import router as organization_router
    from .workflow import router as workflow_router
    from .materials import router as materials_router
    application.include_router(organization_router)
    application.include_router(workflow_router)
    application.include_router(materials_router)
    application.mount('/static', StaticFiles(directory=Path(__file__).parent / 'static'), name='static')

    @application.get('/')
    def index():
        return FileResponse(Path(__file__).parent / 'static/index.html', media_type='text/html')

    return application
