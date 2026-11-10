from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator
from urllib.parse import urlsplit

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', case_sensitive=False)
    database_username: str = Field(...)
    database_password: str = Field(...)
    database_hostname: str = Field(...)
    database_portname: str = Field(...)
    database_name: str = Field(...)
    secret_key: str = Field(...)
    algorithm: str = Field(...)
    access_token_expire_minutes: int = Field(...)
    refresh_token_expire_days: int = Field(...)
    encryption_key: str = Field(...)
    hash_key: str | None = Field(default=None)
    invite_ttl_hours: int = Field(...)
    redis_url: str = Field(...)
    backend_base_url: str = Field(...)
    google_client_id: str = Field(...)
    toroforge_network: str = Field(...)
    toroforge_base_url: str = Field(...)
    toroforge_connectw_url: str = Field(...)
    toroforge_payment_url: str = Field(default='https://www.toronet.org')
    toroforge_deployer_url: str = Field(...)
    toroforge_admin: str = Field(...)
    toroforge_adminpwd: str = Field(...)
    toroforge_settlement_field: str | None = None
    toroforge_settlement_success_values: list[str] = []
    cors_origins: list[str] = ['https://www.fumisync.com']
    @field_validator("cors_origins")
    @classmethod
    def trusted_origins(cls, origins):
        cleaned = []
        for origin in origins:
            parsed = urlsplit(origin)
            if (parsed.scheme not in {"http", "https"} or not parsed.netloc
                or parsed.username or parsed.password or parsed.path not in {"", "/"}
                or parsed.query or parsed.fragment or "*" in origin):
                raise ValueError("CORS origins must be explicit HTTP(S) origins")
            cleaned.append(origin.rstrip("/"))
        return cleaned

settings = Settings()
