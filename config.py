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
    access_token_expire_minutes : float = Field(..., env = ("access_token_expire_minutes", "ACCESS_TOKEN_EXPIRE_MINUTES")) # type: ignore
    refresh_token_expire_days :   int = Field(..., env = ("refresh_token_expire_days", "REFRESH_TOKEN_EXPIRE_DAYS")) # type: ignore  
    encryption_key : str = Field(..., env = ("encryption_key",  "ENCRYPTION_KEY"))  # type: ignore
    invite_ttl_hours: int = Field(..., env = ("invite_ttl_hours", "INVITE_TTL_HOURS")) # type: ignore



    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()  # type: ignore 












