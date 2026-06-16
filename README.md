# agentic-mvp-creator

**Status:** active, experimental  
**License:** [MIT](LICENSE)

[Русская версия](README.ru.md)

agentic-mvp-creator turns a plain product request into a structured, reviewed MVP build pipeline. A user sends an idea to a Telegram bot or HTTP webhook; the backend drafts a spec and implementation plan, waits for approval, runs OpenCode, validates the generated project, opens a GitHub PR, reviews it, and loops on fixes when needed.

## Problem

AI coding workflows often start from unstructured product requests and then require manual glue: creating a spec, planning implementation, preparing a workspace, running an agent, checking tests, opening a PR, watching CI, and asking for human approval. agentic-mvp-creator makes that workflow explicit, repeatable, and inspectable.

## Features

- Telegram and HTTP input adapters.
- FastAPI backend with a Postgres-backed `pipeline_jobs` state machine.
- OpenClaw-compatible spec, plan, and review generation.
- OpenCode session orchestration for coding and fix loops.
- Human approval, rejection feedback, kill, and escalation flow.
- Local tests, MVP quality gate, optional Playwright smoke tests.
- GitHub repository, branch, PR, CI polling, and PR diff review integration.
- Docker Compose setup for backend, Postgres, and OpenCode.

## Requirements

- Python 3.11
- Node.js 20 and npm for host-local runs of generated Node project checks
- Docker and Docker Compose
- Postgres, or the bundled Compose `postgres` service
- `psql` for applying migrations
- OpenClaw-compatible chat API
- OpenCode server, or the bundled Compose `opencode` service. `OPENCODE_MODEL` must use OpenCode's `provider/model` format; the default is `zai-coding-plan/glm-5.1`. The selected provider must be authenticated through a gitignored `.opencode-share/auth.json` file or provider API key variables in `.env`.
- Telegram bot token
- GitHub token with repository, workflow, pull request, and Actions read access

## Installation

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
```

Edit `.env` with local endpoints and tokens. Real `.env` files must stay out of git.

For the bundled OpenCode service, either keep your local OpenCode auth state in `.opencode-share/` or set the provider key used by `OPENCODE_MODEL` in `.env`, for example `ZAI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `OPENROUTER_API_KEY`.

More detail: [docs/installation.md](docs/installation.md)

## Run

Start the services:

```bash
docker compose up --build
```

Apply migrations from the host:

```bash
export DATABASE_URL=postgresql://pipeline:pipeline@localhost:15432/pipeline
make migrations
```

Start the backend without Docker:

```bash
make run-backend
```

Start the Telegram adapter:

```bash
make run-bot
```

## Minimal Usage Example

Send this to the Telegram bot:

```text
Build a small CRM where users can create leads, update lead statuses, add notes, and view a kanban board.
```

Or start a job through HTTP:

```bash
curl -X POST http://127.0.0.1:18000/api/v1/webhook/trigger \
  -H "X-Webhook-Secret: $WEBHOOK_SECRET" \
  -H "Content-Type: application/json" \
  -d @examples/webhook-request.json
```

More examples: [examples/](examples/)

## Commands

| Command | Purpose |
| --- | --- |
| `make install` | Install runtime dependencies. |
| `make install-dev` | Install runtime and development dependencies. |
| `make run-backend` | Start the FastAPI backend locally. |
| `make run-bot` | Start the Telegram adapter locally. |
| `make migrations` | Apply SQL migrations with `psql`. |
| `make test` | Run the test suite. |
| `make lint` | Run Ruff lint checks. |
| `make format` | Format Python code with Ruff. |
| `make format-check` | Check Python formatting with Ruff. |

## Project Structure

```text
.
  api/                 FastAPI routes
  app/                 FastAPI app and settings
  background/          poller and watchdog loops
  docs/                installation, usage, configuration, release docs
  examples/            minimal webhook examples
  migrations/          Postgres schema migrations
  pipeline/            pipeline state machine
  prompts/             LLM system prompts
  services/            integrations and domain services
  telegram_bot/        Telegram input adapter
  templates/           generated CI templates
  tests/               unit and integration tests
  utils/               logging, prompts, redaction helpers
```

## Documentation

- [Installation](docs/installation.md)
- [Usage](docs/usage.md)
- [Configuration](docs/configuration.md)
- [Architecture](docs/architecture.md)
- [Demo recording guide](docs/demo.md)
- [Release notes](docs/release.md)
- [Security policy](SECURITY.md)
- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)

## CI And Quality

GitHub Actions runs dependency installation, Ruff linting, and tests on `push` and `pull_request`. Dependabot is configured for Python dependencies and GitHub Actions.

Local verification:

```bash
make lint
make test
```
