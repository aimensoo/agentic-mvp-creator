# Architecture

## Overview

agentic-mvp-creator is a backend orchestrator for generating MVP projects from plain user requests. The default public input source is Telegram: a user sends a message, the Telegram adapter calls the FastAPI webhook, and the pipeline begins from the saved `input_text`.

High-level flow:

```text
Telegram message
  -> telegram_bot/listener.py
  -> POST /api/v1/webhook/trigger
  -> Postgres pipeline_jobs
  -> background poller
  -> PipelineRunner
  -> OpenClaw / OpenCode / local tests / GitHub / Telegram
```

## Input Adapters

The core pipeline does not depend on Telegram. It expects a `pipeline_jobs` row with:

- `input_text` - the user's product request
- `input_source` - adapter name such as `telegram` or `api`
- optional `chat_id` and `telegram_user_id` for notifications and authorization

Public adapters included in this repository:

- `telegram_bot/listener.py` - starts a job from a Telegram text message and handles approval callbacks
- `api/v1/webhook.py` - starts a job from an authenticated HTTP request

Downstream forks can add private adapters such as CRM events, queue consumers, or document imports without changing `PipelineRunner`.

## Pipeline State

Postgres stores all job state in `pipeline_jobs`. The table acts as a state machine, queue, and durable storage for intermediate artifacts.

Important fields:

- `input_text`
- `input_source`
- `request_snapshot`
- `spec_text`
- `plan_text`
- `human_approved`
- `opencode_session_id`
- `review_result`
- `github_repo`, `github_branch`, `github_commit`, `github_pr_url`
- retry and failure summary fields
- `last_activity_at`, `created_at`, `updated_at`

Runnable statuses are picked up by `background/poller.py`. Waiting and terminal statuses are left untouched until a user or operator action changes them.

## Pipeline Steps

1. `loading_request`  
   Copies `input_text` into `request_snapshot`.

2. `drafting_spec`  
   `SpecService` sends the request to OpenClaw and stores `spec_text`.

3. `planning`  
   `PlanService` turns the spec into an implementation plan and stores `plan_text`.

4. `awaiting_human_approval`  
   `TelegramService` sends summary text, `REQUEST.md`, `SPEC.md`, and `PLAN.md` with approve/reject/kill buttons.

5. `preparing_workspace`  
   Creates `workspaces/{job_id}/` and writes `REQUEST.md`, `SPEC.md`, `PLAN.md`, and `TASK.md`.

6. `coding` / `coding_in_progress`  
   `CodingService` starts or resumes an OpenCode session in the workspace.

7. `local_testing`  
   Runs local checks. Failures dispatch a fix prompt to OpenCode.

8. quality gate and optional smoke test  
   Verifies generated MVP runtime, UI contract, placeholders, Docker setup, and smoke flow.

9. `git_push`  
   Creates or updates the generated repository branch and opens a PR.

10. `ci_testing`  
    Waits for GitHub Actions and routes failures back to OpenCode.

11. `reviewing_code`  
    Runs AI review on the PR diff.

12. `fixing` / `fixing_in_progress`  
    Reuses the same OpenCode context for test, CI, and review fixes.

13. `awaiting_human_escalation`, `done`, or `failed`  
    Stops for manual intervention, finishes successfully, or records a failure.

## Services

- `DatabaseService` - Postgres CRUD for jobs
- `TelegramService` - approval, escalation, completion, and error notifications
- `SpecService` - request-to-spec generation
- `PlanService` - spec-to-plan generation
- `CodingService` - OpenCode session lifecycle
- `TestService` - local checks and GitHub CI polling
- `QualityGateService` - generated MVP quality checks
- `SmokeTestService` - optional Playwright browser flow
- `GitService` - repository, branch, commit, PR, and diff operations
- `ReviewService` - AI review of PR diff
- `WatchdogService` - stale job detection and escalation

## Boundaries

The open source repository intentionally avoids product-specific input sources. The baseline flow is:

```text
plain user request -> generated MVP pipeline
```

Keep private ingestion logic in separate adapters or forks. The core runner should remain source-agnostic and operate only on normalized `input_text`.
