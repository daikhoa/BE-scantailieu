import asyncio

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware

from . import auth
from .config import Settings
from .database import initialize
from .documents.routes import router as documents_router


class RequestLimitMiddleware:
    def __init__(self, app, maximum: int):
        self.app, self.maximum = app, maximum

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        maximum = 8192 if scope["path"].startswith("/api/v1/auth/") else self.maximum
        total = 0

        async def limited_receive():
            nonlocal total
            message = await receive()
            total += len(message.get("body", b""))
            if total > maximum:
                raise HTTPException(413, "Dữ liệu gửi lên vượt giới hạn dung lượng.")
            return message

        await self.app(scope, limited_receive, send)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    initialize(settings)
    application = FastAPI(title="Docscan API", version="1.0.0")
    application.state.settings = settings
    application.state.upload_slots = asyncio.Semaphore(2)
    application.add_middleware(RequestLimitMiddleware, maximum=settings.max_request_bytes)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "Authorization", "X-Requested-With", "Idempotency-Key"],
    )

    @application.middleware("http")
    async def private_responses(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        # Pydantic errors otherwise echo passwords and document input in the response.
        return JSONResponse(
            {
                "detail": "Dữ liệu không hợp lệ. Kiểm tra email, mật khẩu (10–128 ký tự) và các trường bắt buộc."
            },
            status_code=422,
        )

    application.include_router(auth.router)
    application.include_router(documents_router)

    @application.get("/api/v1/health", tags=["Health"])
    def health():
        return {"status": "ok", "api_version": 1}

    return application


app = create_app()
