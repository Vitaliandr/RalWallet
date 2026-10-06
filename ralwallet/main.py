import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from ralwallet import logs
from ralwallet.errors import AppError
from ralwallet.routes import accounts, transfers, users
from ralwallet.settings import settings

logs.setup(as_json=settings.log_json)
log = logging.getLogger("ralwallet")

CallNext = Callable[[Request], Awaitable[Response]]

# swagger выключается DOCS=false
app = FastAPI(
    title="RalWallet",
    docs_url="/docs" if settings.docs else None,
    redoc_url="/redoc" if settings.docs else None,
    openapi_url="/openapi.json" if settings.docs else None,
)

app.include_router(users.router)
app.include_router(accounts.router)
app.include_router(transfers.router)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    #не ошибка сервера, но в логе пусть будет
    log.info("отказ %s %s: %s", exc.status_code, request.url.path, exc.message)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # клиенту трейс не показываем
    log.exception("необработанная ошибка на %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "внутренняя ошибка, попробуйте позже"})


FIELDS = {
    "amount": "Сумма",
    "email": "Email",
    "password": "Пароль",
    "currency": "Валюта",
    "from_account_id": "Счёт списания",
    "to_account_id": "Счёт получателя",
    "Idempotency-Key": "Idempotency-Key",
    "last_name": "Фамилия",
    "first_name": "Имя",
    "middle_name": "Отчество",
    "phone": "Телефон",
    "to_phone": "Телефон получателя",
    "birth_date": "Дата рождения",
    "main_account_id": "Основной счёт",
    "daily_limit": "Лимит",
    "current_password": "Текущий пароль",
    "new_password": "Новый пароль",
    "new_email": "Email",
}


def _human_error(err: dict) -> str:
    last = str(err["loc"][-1]) if err["loc"] else "body"
    field = FIELDS.get(last, last)
    t = err["type"]
    ctx = err.get("ctx") or {}

    #ошибка всей формы, без названия поля
    if last == "body":
        return err["msg"].removeprefix("Value error, ")

    if t == "missing":
        return f"{field}: обязательное поле"
    if t == "greater_than":
        return f"{field}: должна быть больше нуля"
    if t == "decimal_max_places":
        return f"{field}: не больше двух знаков после запятой"
    if t == "decimal_max_digits":
        return f"{field}: слишком большое число"
    if t in ("decimal_parsing", "int_parsing", "int_type"):
        return f"{field}: нужно число"
    if t.startswith("date"):
        return f"{field}: неверная дата"
    if t == "string_too_short":
        return f"{field}: минимум {ctx.get('min_length')} символов"
    if t == "string_too_long":
        return f"{field}: максимум {ctx.get('max_length')} символов"
    if t == "value_error" and field == "Email":
        return "Email: неправильный адрес"
    if t == "value_error":
        # убираем приставку pydantic
        return f"{field}: {err['msg'].removeprefix('Value error, ')}"
    return f"{field}: неверное значение"


#по умолчанию английский и список, фронту нужна строка
@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    text = "; ".join(_human_error(e) for e in exc.errors())
    return JSONResponse(status_code=422, content={"detail": text})


# иначе после обновления браузер показывает старый js
@app.middleware("http")
async def no_stale_frontend(request: Request, call_next: CallNext) -> Response:
    response = await call_next(request)
    if request.method == "GET" and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-cache"
    return response


#CSP: выполняется только наш app.js, никаких inline
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)


@app.middleware("http")
async def security_headers(request: Request, call_next: CallNext) -> Response:
    response = await call_next(request)
    h = response.headers
    h["X-Content-Type-Options"] = "nosniff"  # не угадывать тип файла
    h["X-Frame-Options"] = "DENY"  #clickjacking
    h["Referrer-Policy"] = "no-referrer"
    h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    # swagger с CSP не работает (cdn + inline)
    if not request.url.path.startswith(("/docs", "/redoc", "/openapi.json")):
        h["Content-Security-Policy"] = CSP
    return response


#последний = выполняется первым
@app.middleware("http")
async def with_request_id(request: Request, call_next: CallNext) -> Response:
    rid = logs.new_request_id(request.headers.get("x-request-id"))
    token = logs.request_id.set(rid)
    try:
        response = await call_next(request)
    finally:
        logs.request_id.reset(token)
    response.headers["X-Request-ID"] = rid
    return response


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# монтируем последним, иначе перекроет api
app.mount("/", StaticFiles(directory=Path(__file__).parent / "web", html=True), name="web")
