# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


@dataclass(slots=True)
class GatewayError(Exception):
    status_code: int
    message: str
    error_type: str
    code: str
    headers: dict[str, str] | None = None
    audit_decision: str | None = None
    audit_provider: str | None = None


def error_payload(message: str, error_type: str, code: str) -> dict[str, Any]:
    return {"error": {"message": message, "type": error_type, "code": code}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(GatewayError)
    async def gateway_error(request: Request, exc: GatewayError) -> JSONResponse:
        request.state.gateway_error_code = exc.code
        headers = {"X-Request-ID": getattr(request.state, "request_id", "unknown")}
        if exc.headers:
            headers.update(exc.headers)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(exc.message, exc.error_type, exc.code),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, _: RequestValidationError) -> JSONResponse:
        request.state.gateway_error_code = "invalid_request"
        return JSONResponse(
            status_code=400,
            content=error_payload("The request is invalid", "invalid_request_error", "invalid_request"),
            headers={"X-Request-ID": getattr(request.state, "request_id", "unknown")},
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "not_found" if exc.status_code == 404 else "method_not_allowed" if exc.status_code == 405 else "http_error"
        request.state.gateway_error_code = code
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(str(exc.detail), "invalid_request_error", code),
            headers={"X-Request-ID": getattr(request.state, "request_id", "unknown")},
        )

    @app.exception_handler(Exception)
    async def unexpected(request: Request, _: Exception) -> JSONResponse:
        request.state.gateway_error_code = "internal_error"
        return JSONResponse(
            status_code=500,
            content=error_payload("Internal gateway error", "server_error", "internal_error"),
            headers={"X-Request-ID": getattr(request.state, "request_id", "unknown")},
        )
