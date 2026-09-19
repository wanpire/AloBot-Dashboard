from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for THIS project's own tables only.

    AloBot's tables are never declared here - they are reflected
    read-only at runtime by `app.alobot` so the definitions cannot drift
    from the schema AloBot's own Alembic migrations actually created.
    """
