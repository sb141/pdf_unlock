"""Bound incoming uploads before the multipart parser can spool them to disk."""

from starlette.formparsers import MultiPartException
from starlette.responses import JSONResponse

from app.config import settings


class UploadLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        limits = {
            "/api/unlock": settings.max_total_size_mb,
            "/api/edit/session": settings.max_file_size_mb,
            "/api/convert/word": settings.max_file_size_mb,
        }
        if scope["type"] != "http" or scope.get("method") != "POST" or scope.get("path") not in limits:
            await self.app(scope, receive, send)
            return
        # The endpoint separately checks PDF bytes; allow bounded form/header overhead.
        limit = limits[scope["path"]] * 1024 * 1024 + 1024 * 1024
        error = JSONResponse({"detail": "Upload request is too large"}, status_code=422)
        headers = dict(scope.get("headers", []))
        try:
            declared_size = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared_size = 0
        if declared_size > limit:
            await error(scope, receive, send)
            return
        size = 0
        exceeded = False

        async def bounded_receive():
            nonlocal size, exceeded
            message = await receive()
            if message["type"] == "http.request":
                size += len(message.get("body", b""))
                if size > limit:
                    exceeded = True
                    # This exception makes Starlette close any partially spooled files.
                    raise MultiPartException("Upload request is too large")
            return message

        async def bounded_send(message):
            if not exceeded:
                await send(message)

        await self.app(scope, bounded_receive, bounded_send)
        if exceeded:
            await error(scope, receive, send)
