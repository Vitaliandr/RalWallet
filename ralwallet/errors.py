#ошибки логики, наружу уходят как http ответы


class AppError(Exception):
    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFound(AppError):
    status_code = 404


class Forbidden(AppError):
    status_code = 403


class Conflict(AppError):
    status_code = 409


class NotEnoughMoney(AppError):
    status_code = 422


class TooManyAttempts(AppError):
    status_code = 429


class Unauthorized(AppError):
    status_code = 401
