# Configuration

Configuration is loaded from environment variables and `.env`.

| Variable | Required | Description |
| --- | --- | --- |
| `DATABASE_URL` | Yes | Postgres connection string for `pipeline_jobs`. |
| `POSTGRES_PORT` | No | Host port for the bundled Compose Postgres service. Defaults to `15432`. |
| `OPENCLAW_API_URL` | Yes | OpenClaw-compatible chat endpoint. |
| `OPENCLAW_API_KEY` | No | API key when required by the endpoint. |
| `OPENCLAW_MODEL` | Yes | Model used for spec, plan, and review. |
| `OPENCODE_API_URL` | Yes | OpenCode server URL. |
| `OPENCODE_PORT` | No | Host port for the bundled Compose OpenCode service. Defaults to `14096`. |
| `OPENCODE_SERVER_PASSWORD` | No | OpenCode Basic Auth password. |
| `OPENCODE_WORKSPACE_ROOT` | No | Workspace path as seen by OpenCode. |
| `GITHUB_TOKEN` | Yes | Token for generated repo, branch, PR, and CI polling. |
| `GITHUB_REPO_OWNER` | Yes | GitHub user or organization for generated repositories. |
| `GITHUB_REPO_NAME` | Yes | Generated repository name prefix. |
| `GITHUB_REPO_PRIVATE` | No | Whether generated repositories are private. Defaults to `true`. |
| `TELEGRAM_BOT_TOKEN` | Yes | Telegram bot token. |
| `TELEGRAM_APPROVAL_CHAT_ID` | No | Fallback approval chat id for non-Telegram jobs. Telegram-started jobs reply to their source chat. |
| `TELEGRAM_ESCALATION_CHAT_ID` | No | Fallback escalation chat id for non-Telegram jobs. Telegram-started jobs reply to their source chat. |
| `TELEGRAM_NOTIFICATION_CHAT_ID` | No | Fallback completion chat id for non-Telegram jobs. Telegram-started jobs reply to their source chat. |
| `WEBHOOK_SECRET` | Yes | Shared secret for `/api/v1/webhook/trigger`. |
| `BACKEND_PORT` | No | Host port for the bundled Compose backend service. Defaults to `18000`. |
| `BACKEND_API_URL` | No | Backend API URL used by the local Telegram listener. |
| `WORKSPACE_ROOT` | No | Host directory for generated workspaces. |
| `SMOKE_TEST_ENABLED` | No | Enables Playwright smoke tests when `true`. |

Use `.env.example` as the template. It contains placeholder and demo values only.
