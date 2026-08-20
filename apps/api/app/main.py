from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import api_router
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version="0.13.0",
    description="Amazon Seller Intelligence API — listing, competitive, reports, usage, and bulk due diligence",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    _request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    messages: list[str] = []
    for error in exc.errors():
        loc = ".".join(str(part) for part in error["loc"] if part != "body")
        msg = error["msg"]
        messages.append(f"{loc}: {msg}" if loc else msg)
    return JSONResponse(status_code=400, content={"detail": "; ".join(messages)})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
