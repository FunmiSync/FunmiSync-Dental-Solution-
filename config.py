from pydantic_settings import BaseSettings
from pydantic import Field 

class Settings (BaseSettings):
    database_username: str = Field(..., env=("database_username", "DATABASE_USERNAME"))  # type: ignore
    database_password: str = Field(..., env=("database_password", "DATABASE_PASSWORD"))   # type: ignore
    database_hostname: str = Field(..., env = ("database_hostname", "DATABASE_HOSTNAME" )) # type: ignore
    database_portname: str = Field(..., env = ("database_portname", "DATABASE_PORTNAME" ))  # type: ignore
    database_name :    str = Field(..., env = ("database_name", "DATABASE_NAME")) # type: ignore
    secret_key :       str = Field(..., env = ("secret_key", "SECRET_KEY"))  # type: ignore
    algorithm :        str = Field(..., env = ("algorithm", "ALGORITHM"))  # type: ignore
    access_token_expire_minutes : int = Field(..., env = ("access_token_expire_minutes", "ACCESS_TOKEN_EXPIRE_MINUTES")) # type: ignore
    refresh_token_expire_days :   int = Field(..., env = ("refresh_token_expire_days", "REFRESH_TOKEN_EXPIRE_DAYS")) # type: ignore  
    encryption_key : str = Field(..., env = ("encryption_key",  "ENCRYPTION_KEY"))  # type: ignore
    hash_key: str | None = Field(default=None, env=("hash_key", "HASH_KEY"))  # type: ignore
    invite_ttl_hours: int = Field(..., env = ("invite_ttl_hours", "INVITE_TTL_HOURS")) # type: ignore
    redis_url: str = Field(..., env=("redis_url", "REDIS_URL"))  # type: ignore
    backend_base_url: str = Field(..., env=("backend_base_url", "BACKEND_BASE_URL"))  # type: ignore
    google_client_id: str = Field(..., env=("google_client_id", "GOOGLE_CLIENT_ID"))  # type: ignore






    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()  # type: ignore 












