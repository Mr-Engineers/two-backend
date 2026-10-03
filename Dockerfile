# One image for all three shops and the marketplace API. The shop is chosen at start-up with SHOP_ID
# (shop-pl | shop-de | shop-ru) and each container gets only ITS OWN runtime DATABASE_URL.
# The marketplace overrides the command: uvicorn app.marketplace.main:create_app --factory (needs MARKETPLACE_DATABASE_URL).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini ./
COPY app ./app
COPY migrations ./migrations
COPY migrations_marketplace ./migrations_marketplace
COPY scripts ./scripts

RUN useradd --system --uid 10001 --no-create-home shop
USER shop

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2).status == 200 else 1)"

CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
