"""Hermetic test settings; never use developer credentials or real services."""
import os
import base64
from contextlib import contextmanager
from unittest.mock import patch


def configure_test_environment():
    values = {
        "DATABASE_USERNAME": "test", "DATABASE_PASSWORD": "test",
        "DATABASE_HOSTNAME": "127.0.0.1", "DATABASE_PORTNAME": "1", "DATABASE_NAME": "test",
        "SECRET_KEY": "test-only-signing-secret-do-not-deploy", "ALGORITHM": "HS256",
        "ACCESS_TOKEN_EXPIRE_MINUTES": "30", "REFRESH_TOKEN_EXPIRE_DAYS": "7",
        "ENCRYPTION_KEY": base64.urlsafe_b64encode(b"0" * 32).decode(),
        "HASH_KEY": "test-only-lookup-key", "INVITE_TTL_HOURS": "72",
        "REDIS_URL": "redis://127.0.0.1:1/0", "BACKEND_BASE_URL": "https://backend.invalid",
        "GOOGLE_CLIENT_ID": "test-client", "TOROFORGE_NETWORK": "testnet",
        "TOROFORGE_BASE_URL": "https://provider.invalid", "TOROFORGE_PAYMENT_URL": "https://payment.invalid",
        "TOROFORGE_CONNECTW_URL": "https://kyc.invalid", "TOROFORGE_DEPLOYER_URL": "https://deploy.invalid",
        "TOROFORGE_ADMIN": "test", "TOROFORGE_ADMINPWD": "test",
    }
    for name, value in values.items():
        os.environ[name] = value
        os.environ[name.lower()] = value


@contextmanager
def guarded_local_services():
    from sqlalchemy.engine import Engine
    original = Engine.connect
    def connect(engine, *args, **kwargs):
        if engine.dialect.name != "sqlite" or engine.url.database != ":memory:":
            raise AssertionError("Only in-memory SQLite is allowed in Phase 4 tests")
        return original(engine,*args,**kwargs)
    with patch.object(Engine,"connect",new=connect), \
         patch("redis.Redis.execute_command",side_effect=AssertionError("No live Redis")), \
         patch("redis.asyncio.Redis.execute_command",side_effect=AssertionError("No live Redis")), \
         patch("httpx.AsyncClient.send",side_effect=AssertionError("No live provider")):
        yield
