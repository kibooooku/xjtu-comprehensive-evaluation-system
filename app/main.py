from __future__ import annotations
import hmac
import os
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError, StatementError

from . import db
from .security import COOKIE, csrf_token
from .limits import MAX_REQUEST_BYTES


def create_app(database_url=None, storage_dir=None, seed_demo=True, start_worker=True):
    load_dotenv(db.ROOT / '.env', override=False)
    from .jobs import JobWorker, stop_all
    stop_all()
    db.configure(database_url, storage_dir)
    if seed_demo:
        from .seed import initialize
        initialize()
    from .migrations import initialize_v2
    initialize_v2()

    @asynccontextmanager
    async def lifespan(application):
        worker = JobWorker() if start_worker else None
        if worker:
            worker.start()
        try:
            yield
        finally:
            if worker:
                await asyncio.to_thread(worker.stop)

    application = FastAPI(title='西安交通大学德育分统计管理系统', version='2.0.0', docs_url='/api/docs', redoc_url=None, lifespan=lifespan)

    @application.middleware('http')
    async def secure_request(request: Request, call_next):
        length = request.headers.get('content-length')
        if length:
            try:
                if int(length) < 0:
                    raise ValueError
                if int(length) > MAX_REQUEST_BYTES:
                    return JSONResponse({'detail': '单文件不能超过200MB（请求体上限205MB，含上传表单）'}, 413)
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
        return {'status': 'ok', 'version': '2.0.0'}

    from .organization import router as organization_router
    from .activity_matching import router as activity_matching_router
    from .workflow import router as workflow_router
    from .materials import router as materials_router
    from .submissions import router as submissions_router
    from .jobs import router as jobs_router
    application.include_router(organization_router)
    application.include_router(activity_matching_router)
    application.include_router(workflow_router)
    application.include_router(materials_router)
    application.include_router(submissions_router)
    application.include_router(jobs_router)
    application.mount('/static', StaticFiles(directory=Path(__file__).parent / 'static'), name='static')

    @application.get('/')
    def index():
        return FileResponse(Path(__file__).parent / 'static/index.html', media_type='text/html')

    return application
