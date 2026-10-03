# syntax=docker/dockerfile:1
#
# One image: the Marketplace API by default, the three shops by overriding the command (docker-compose.yml does).
#
# Marketplace (needs a migrated database: the image does NOT migrate or seed, see `python -m app.cli bootstrap`).
# Data source, one of (SUPABASE_* wins when both are set; these are the variables of the AWS deployment):
#   docker build -t marketplace .
#   docker run --rm -p 8010:8000 \
#     -e SUPABASE_URL=https://<project-ref>.supabase.co -e SUPABASE_KEY=<service_role key> \
#     -e MARKETPLACE_API_TOKEN=<proxy token> -e MARKETPLACE_ADMIN_TOKEN=<admin token> marketplace
#   or a direct SQL connection: -e MARKETPLACE_DATABASE_URL=postgresql://marketplace_rt:<pw>@<host>:5432/postgres?sslmode=require
#   Production: -e APP_ENV=production (requires MARKETPLACE_API_TOKEN, https for SUPABASE_URL or sslmode=require+).
#
# A shop (SHOP_ID + its own runtime DATABASE_URL):
#   docker run --rm -p 8001:8000 -e SHOP_ID=shop-pl -e DATABASE_URL=... marketplace \
#     uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000

WORKDIR /srv

# Dependencies first: this layer is rebuilt only when requirements.txt changes.
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY alembic.ini ./
COPY app ./app
COPY migrations ./migrations
COPY migrations_marketplace ./migrations_marketplace
COPY scripts ./scripts

# Unprivileged user; nothing is written at run time, so the container also works with a read-only root filesystem.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app
USER 10001

EXPOSE 8000

# Liveness only (no database query); PORT lets hosting platforms choose the listening port.
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/health/live' % os.environ.get('PORT', '8000'), timeout=2).status == 200 else 1)"]

STOPSIGNAL SIGTERM

# `exec` makes uvicorn PID 1, so SIGTERM from `docker stop` shuts it down gracefully.
CMD ["sh", "-c", "exec uvicorn app.marketplace.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8000} --timeout-graceful-shutdown 10"]
