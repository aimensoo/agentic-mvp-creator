# Architecture

The project is built around a source-agnostic pipeline runner. Input adapters normalize requests into a `pipeline_jobs` row, and the core runner operates only on that row.

```text
Telegram message or HTTP request
  -> input adapter
  -> FastAPI webhook
  -> Postgres pipeline_jobs
  -> background poller
  -> PipelineRunner
  -> OpenClaw, OpenCode, tests, GitHub, Telegram notifications
```

## Main Components

- `api/` - FastAPI route handlers.
- `telegram_bot/` - Telegram input and approval adapter.
- `background/` - poller and watchdog loops.
- `pipeline/` - pipeline state transitions and orchestration.
- `services/` - integrations and domain services.
- `migrations/` - SQL schema changes.
- `prompts/` - system prompts for LLM-backed steps.
- `tests/` - unit and integration tests.

## Pipeline Stages

1. `loading_request` stores the normalized user request as `request_snapshot`.
2. `drafting_spec` creates the MVP spec through OpenClaw.
3. `planning` creates the implementation plan through OpenClaw.
4. `awaiting_human_approval` stops until Telegram or API approval.
5. `preparing_workspace` writes `REQUEST.md`, `SPEC.md`, `PLAN.md`, and `TASK.md`.
6. `coding` starts OpenCode in the generated workspace.
7. `local_testing`, quality gate, and optional smoke tests validate the result.
8. `git_push` opens or updates a GitHub PR.
9. `ci_testing` waits for GitHub Actions.
10. `reviewing_code` runs AI review on the PR diff.
11. `fixing` reuses OpenCode sessions for test, CI, and review fixes.
12. `done`, `failed`, or `awaiting_human_escalation` ends the run.

See the root `ARCHITECTURE.md` for a more detailed component-level description.
