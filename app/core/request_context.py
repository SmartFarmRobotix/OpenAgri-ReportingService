"""
Request-scoped context propagated into log records.

`request_id_var` holds the current request's id (from the inbound
`X-Request-ID` header, or a generated uuid4 when absent). We install a
custom LogRecord factory so that *every* LogRecord created anywhere in the
process -- regardless of which logger emits it (module-level
`logging.getLogger(__name__)` loggers, the ad-hoc `logging.Logger("utils")`
instance in utils/utils.py, etc.) -- automatically carries a `request_id`
attribute. This means log lines for a given request can be correlated
without having to thread a request id through every function call.
"""
import contextvars
import logging

request_id_var: "contextvars.ContextVar[str]" = contextvars.ContextVar(
    "request_id", default="-"
)


def get_request_id() -> str:
    return request_id_var.get()


def _install_request_id_log_record_factory() -> None:
    existing_factory = logging.getLogRecordFactory()

    if getattr(existing_factory, "_rs_request_id_aware", False):
        return

    def record_factory(*args, **kwargs):
        record = existing_factory(*args, **kwargs)
        record.request_id = request_id_var.get()
        return record

    record_factory._rs_request_id_aware = True
    logging.setLogRecordFactory(record_factory)


_install_request_id_log_record_factory()
