import logging
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from core.request_context import request_id_var

logger = logging.getLogger("app.request")

REQUEST_ID_HEADER = "X-Request-ID"


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Reads the inbound `X-Request-ID` header (generating one when absent),
    makes it available to structured log lines for the duration of the
    request via a contextvar (see core.request_context), and echoes it
    back on the response so callers can correlate their own logs with
    ours.
    """

    async def dispatch(self, request: Request, call_next):
        incoming_request_id = request.headers.get(REQUEST_ID_HEADER)
        request_id = incoming_request_id or str(uuid.uuid4())
        token = request_id_var.set(request_id)
        try:
            logger.info(
                "request started method=%s path=%s",
                request.method,
                request.url.path,
            )
            response = await call_next(request)
            logger.info(
                "request finished method=%s path=%s status_code=%s",
                request.method,
                request.url.path,
                response.status_code,
            )
        finally:
            request_id_var.reset(token)

        response.headers[REQUEST_ID_HEADER] = request_id
        return response
