# Installation

## Requirements

- Python 3.11
- Docker and Docker Compose
- Postgres, or the bundled Compose service
- `psql` for applying SQL migrations
- OpenClaw-compatible chat endpoint
- OpenCode server, or the bundled Compose service
- Telegram bot token
- GitHub token for generated repository and PR operations

## Python Environment

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

## Environment File

```bash
cp .env.example .env
```

Edit `.env` with local endpoint URLs and tokens. Keep real `.env` files out of git.

## Docker Services

```bash
docker compose up --build
```

The Compose file starts Postgres, the FastAPI backend, and OpenCode. OpenClaw-compatible model serving is expected to run separately unless you point `OPENCLAW_API_URL` to another service.

## Database Migrations

```bash
export DATABASE_URL=postgresql://pipeline:pipeline@localhost:15432/pipeline
make migrations
```

The migration command applies every SQL file in `migrations/` in sorted order.
