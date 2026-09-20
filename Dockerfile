FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv/app

# Postgres client tools for the copy-AloBot-db and restore-drill scripts.
# Pinned to major 16 to match the postgres:16 server: a newer pg_dump emits
# settings an older server rejects, which broke the first restore drill.
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
         -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && . /etc/os-release \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $VERSION_CODENAME-pgdg main" \
         > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends postgresql-client-16 \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app
COPY scripts ./scripts
COPY alembic ./alembic
COPY alembic.ini ./

# Runtime dependencies only - `.`, never `.[dev]`. pytest, playwright and the
# rest of the test tooling have no business in a production image, and the
# tests themselves are not copied in either.
RUN pip install --no-cache-dir .

# The running version, stamped at build time from the git commit. The badge in
# the panel's sidebar and /health both read it, so "which build is this host
# running" is answerable without ssh. See the `build` target in the Makefile.
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}

# Not root. The process only reads its code and writes the heartbeat file in
# /tmp, so it needs to own neither.
RUN useradd --system --create-home --uid 10001 dashboard
USER dashboard

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
