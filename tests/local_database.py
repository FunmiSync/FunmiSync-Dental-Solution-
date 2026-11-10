"""SQLite unit fixture for durable state transitions, never a PostgreSQL substitute."""
from uuid import uuid4
from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.orm import sessionmaker
from app.models.entities import Base


def local_session():
    engine = create_engine("sqlite:///:memory:")
    @event.listens_for(engine, "connect")
    def configure(connection, _):
        connection.create_function("gen_random_uuid", 0, lambda: uuid4().hex)
    metadata = MetaData()
    for table in Base.metadata.sorted_tables:
        copied = table.to_metadata(metadata)
        # SQLite can exercise the predicate invariant, but its locks/isolation
        # differ. Dedicated PostgreSQL concurrency tests are still required.
        for index in copied.indexes:
            where = index.dialect_options["postgresql"].get("where")
            if where is not None:
                index.dialect_options["sqlite"]["where"] = where
    metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=True)(), engine
