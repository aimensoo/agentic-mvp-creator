# agentic-mvp-creator

[English README](README.md)

**Статус:** active, experimental  
**Лицензия:** [MIT](LICENSE)

agentic-mvp-creator превращает обычное текстовое описание продукта в управляемый пайплайн создания MVP. Пользователь пишет идею в Telegram-бота или HTTP webhook; backend формирует спецификацию и план, ждет подтверждения, запускает OpenCode, проверяет результат, открывает GitHub PR, дожидается CI, делает AI-review и отправляет задачу на исправление, если что-то не прошло.

## Какую проблему решает

AI-coding часто начинается с хаотичного запроса и быстро превращается в ручную склейку: нужно написать spec, составить план, подготовить workspace, запустить агента, проверить тесты, открыть PR, следить за CI и согласовывать результат. Этот проект делает весь маршрут явным, повторяемым и проверяемым.

## Возможности

- Telegram и HTTP вход для пользовательских запросов.
- FastAPI backend и Postgres state machine `pipeline_jobs`.
- Генерация spec, plan и review через OpenClaw-compatible chat API.
- Оркестрация OpenCode-сессий для coding/fix loops.
- Human approval, reject feedback, kill и escalation flow.
- Локальные проверки, MVP quality gate и опциональный Playwright smoke test.
- Интеграция с GitHub: repo, branch, PR, CI polling и review diff.
- Docker Compose окружение для backend, Postgres и OpenCode.

## Требования

- Python 3.11
- Docker и Docker Compose
- Postgres или bundled Compose `postgres`
- OpenClaw-compatible chat API
- OpenCode server или bundled Compose `opencode`
- Telegram bot token
- GitHub token с доступом к repository, workflow, pull request и Actions read

## Быстрый старт

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
```

Заполните `.env` локальными endpoint и токенами. Реальные `.env` файлы не должны попадать в git.

Запуск сервисов:

```bash
docker compose up --build
```

Применение миграций:

```bash
export DATABASE_URL=postgresql://pipeline:pipeline@localhost:15432/pipeline
make migrations
```

Локальный backend:

```bash
make run-backend
```

Telegram adapter:

```bash
make run-bot
```

## Минимальный пример

Напишите Telegram-боту:

```text
Build a small CRM where users can create leads, update lead statuses, add notes, and view a kanban board.
```

Или создайте job через HTTP:

```bash
curl -X POST http://127.0.0.1:18000/api/v1/webhook/trigger \
  -H "X-Webhook-Secret: $WEBHOOK_SECRET" \
  -H "Content-Type: application/json" \
  -d @examples/webhook-request.json
```

## Основные команды

| Команда | Назначение |
| --- | --- |
| `make install` | Установить runtime dependencies. |
| `make install-dev` | Установить runtime и dev dependencies. |
| `make run-backend` | Запустить FastAPI backend локально. |
| `make run-bot` | Запустить Telegram adapter локально. |
| `make migrations` | Применить SQL migrations через `psql`. |
| `make test` | Запустить тесты. |
| `make lint` | Запустить Ruff lint checks. |
| `make format` | Отформатировать Python-код через Ruff. |
| `make format-check` | Проверить форматирование через Ruff. |

## Документация

- [Installation](docs/installation.md)
- [Usage](docs/usage.md)
- [Configuration](docs/configuration.md)
- [Architecture](docs/architecture.md)
- [Demo recording guide](docs/demo.md)
- [Release notes](docs/release.md)
- [Security policy](SECURITY.md)
- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)
