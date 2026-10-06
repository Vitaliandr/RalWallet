import json
import logging
import uuid
from contextvars import ContextVar

# id запроса. contextvar, у каждой корутины свой
request_id: ContextVar[str] = ContextVar("request_id", default="-")


class _AddRequestId(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id.get()
        return True


class _JsonFormatter(logging.Formatter):
    #для loki/elk
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "time": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "msg": record.getMessage(),
        }
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False)


def setup(as_json: bool = False) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(_AddRequestId())
    if as_json:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"))

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


def new_request_id(incoming: str | None) -> str:
    # чужой id берём только если он нормальный
    if incoming and len(incoming) <= 64 and incoming.replace("-", "").isalnum():
        return incoming
    return uuid.uuid4().hex[:12]


def mask_email(email: str) -> str:
    #anna@mail.ru -> a***@mail.ru
    name, _, domain = email.partition("@")
    return f"{name[:1]}***@{domain}" if domain else "***"
