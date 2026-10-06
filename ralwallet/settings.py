from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# засвеченные секреты
WEAK_SECRETS = {"", "change-me", "dev-secret", "secret", "jwt-secret"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://wallet:wallet@localhost:5432/wallet"
    redis_url: str = "redis://localhost:6379/0"
    kafka_servers: str = "localhost:9092"
    kafka_topic: str = "transfers"

    #дефолта нет специально
    jwt_secret: str = ""
    jwt_ttl_minutes: int = 60 * 24

    #сколько переводов в минуту можно одному юзеру
    transfers_per_minute: int = 30
    # поиск получателя, против перебора телефонов
    lookups_per_minute: int = 20
    #за докером у всех один ip, поэтому с запасом
    logins_per_ip_minute: int = 30
    registrations_per_ip_minute: int = 20

    # пополнение тестовое, поэтому лимит
    test_deposits: bool = True
    max_deposit: int = 1_000_000  # рублей за раз

    #swagger в проде лучше выключать
    docs: bool = True
    #json для loki/elk
    log_json: bool = False

    # как часто relay смотрит в outbox (сек)
    outbox_poll_interval: float = 1.0
    outbox_batch_size: int = 100

    @field_validator("jwt_secret")
    @classmethod
    def strong_secret(cls, v: str) -> str:
        if v in WEAK_SECRETS or len(v) < 32:
            raise ValueError(
                "JWT_SECRET не задан или слишком простой (нужно от 32 символов). "
                "Сгенерировать: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
            )
        return v


settings = Settings()
