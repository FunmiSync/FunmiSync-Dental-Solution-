from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import declarative_base
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from app.config import settings


DATABASE_URL = URL.create("postgresql", username=settings.database_username, password=settings.database_password,
    host=settings.database_hostname, port=int(settings.database_portname), database=settings.database_name,
    query={"sslmode": "require"})
SQLALCHEMY_DATABASE_URL = DATABASE_URL.render_as_string(hide_password=False)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=1800, pool_timeout=5,
                       connect_args={"connect_timeout":5})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ping_db(*, check_schema=False) -> tuple[bool, str]:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            if check_schema:
                conn.execute(text("SELECT operation_key FROM external_operations LIMIT 0"))
                revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                if revision != "f4a100000001":
                    return False, "Database schema upgrade required"
        return True, "DB connection OK"

    except SQLAlchemyError as e:
        return False, f"DB check failed ({e.__class__.__name__})"
