import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import async_sessionmaker
from starlette.concurrency import run_in_threadpool

from minimew.api.auth import router as auth_router
from minimew.api.documents import UploadLimitMiddleware
from minimew.api.documents import router as documents_router
from minimew.api.evaluations import router as evaluations_router
from minimew.api.health import router as health_router
from minimew.api.runs import router as runs_router
from minimew.api.threads import router as threads_router
from minimew.auth_http import AuthHttpMiddleware
from minimew.database import create_engine
from minimew.execution.dispatcher import execution_lifespan
from minimew.execution.outbox import notification_lifespan
from minimew.execution.streaming import StreamOptions
from minimew.security import hash_password
from minimew.settings import Settings


def create_app() -> FastAPI:
    """由 Uvicorn 调用；配置非法时立即拒绝创建应用。"""
    settings = Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = create_engine(settings)
        app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            app.state.dummy_password_hash = await run_in_threadpool(
                hash_password, secrets.token_urlsafe(32)
            )
            async with notification_lifespan(app.state.session_factory, settings) as notifier:
                app.state.event_notifier = notifier
                try:
                    async with execution_lifespan(app.state.session_factory, settings):
                        yield
                finally:
                    app.state.event_notifier = None
        finally:
            await engine.dispose()

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(AuthHttpMiddleware)
    app.add_middleware(UploadLimitMiddleware, max_bytes=settings.document_max_upload_bytes)
    app.state.settings = settings
    app.state.event_stream_options = StreamOptions(poll_interval=settings.event_poll_seconds)
    app.state.event_notifier = None
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(threads_router)
    app.include_router(runs_router)
    app.include_router(documents_router)
    app.include_router(evaluations_router)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        # FastAPI 默认错误包含 input；认证输入可能包含明文密码，禁止回显。
        return JSONResponse(status_code=422, content={"detail": "Invalid request"})

    return app
