import asyncio
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import re
import time

from utils.logger import get_logger

logger = get_logger(__name__)


class OpenCodeRunState(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    STUCK = "stuck"
    WAITING_PERMISSION = "waiting_permission"
    WAITING_INPUT = "waiting_input"
    EMPTY_RESULT = "empty_result"
    MODEL_ERROR = "model_error"


EMPTY_DIFF_CONTINUE_PROMPT = """Continue the current task now.

Do not wait for user input or ask whether to proceed.
If fixes are required, edit the workspace files and run the relevant checks.
If no code change is needed, state the concrete evidence and finish clearly.
"""


TASK_PROMPT_TEMPLATE = """You are a coding agent. Your task is to implement an MVP project based on the specification and plan below.

## Workspace

Working directory: `{workspace_path}`

## Before you start

1. Read `SPEC.md` — this is the technical specification. Implement ONLY the MVP scope defined there. Nothing extra.
2. Read `PLAN.md` — this is the implementation plan. Follow the project structure and phases defined there.
3. Read `REQUEST.md` — the original user request for additional context.

## Development approach

- Use `dev-docs` to search for up-to-date documentation on frameworks and libraries you use. This reduces API hallucinations.
- Follow **TDD**: write tests FIRST, then implement the code to make them pass.
- Generate a root `README.md` that is useful from a clean checkout. It must include:
  - a short plain-language description of what the MVP is and who/what it is for;
  - exact setup and run commands, including `.env.example` -> `.env` when applicable and the Docker Compose startup command;
  - seed/demo commands and demo login credentials when auth or seed data is present;
  - local URLs/ports that match `mvp.config.json` readiness and Docker Compose.
- Treat `PLAN.md`'s Backend API Contract as authoritative. Implement the specified endpoints, HTTP methods, route params, query params, request bodies, response schemas, status codes, validation, auth/session behavior, error shapes, and persistence side effects exactly.
- Do not invent alternative backend routes, payload shapes, response fields, or status codes when `PLAN.md` already specifies them. If `SPEC.md` and `PLAN.md` conflict, document the blocker in `BLOCKERS.md` instead of silently choosing a third contract.
- Build an **honest runnable MVP**: implement the main business workflow end-to-end, not a broad UI shell.
- Every visible button/control must either perform a real action end-to-end (UI -> API -> persistence -> refreshed UI) or be removed from the UI.
- Do NOT leave placeholders, TODOs, "coming soon", fake success notifications, empty handlers, or pages that only say a feature name.
- If a CRUD/action is outside MVP scope, do not render that button/link. Smaller working scope is better than a non-working screen.
- Build a presentable MVP UI, not raw browser-default HTML. If you use Tailwind, Bootstrap, a component library, or custom CSS, verify that the CSS is actually imported and compiled. Tailwind v4 must use the v4 setup (`@import "tailwindcss";`) or you must pin/configure Tailwind v3 correctly.
- The UI must meet a basic product-quality bar. Avoid raw/default browser HTML, plain white boxes, overlapping text, broken table rows, uncontrolled full-width forms, and screens where content floats without a clear layout.
- Use a coherent app shell: stable navigation, page header, constrained content width, consistent spacing, typography, colors, borders, radius, and hover/focus/disabled states.
- Build shared UI primitives for buttons, inputs/selects, forms, tables/lists/cards, loading states, empty states, errors, and page layout. Use them consistently instead of one-off markup.
- Forms must have aligned labels/inputs, validation/error messages, loading/submitting disabled state, sensible defaults, and no required empty selects in the demo flow.
- Tables/lists/cards must have readable density, stable columns/rows, empty/loading states, and actions that do not shift or overlap layout.
- The primary screens must be usable at desktop and narrow/mobile widths without text collisions or horizontal layout breakage.
- The demo user must be able to complete the primary workflow immediately after seed/login. Do not require selecting users/managers/assignees from empty or inaccessible lists. If the current authenticated user is the manager/assignee, default that field from auth context or include the current user as a valid option.
- Treat Docker as part of the deliverable. If the app has frontend/backend/database services, create a root `docker-compose.yml`, one Dockerfile per built service, root/context `.dockerignore`, `.env.example`, health/readiness checks where practical, and documented ports. `docker compose up -d` must start the whole MVP from a clean checkout.
- Create project-owned Docker CI at `.pipeline/docker-ci.sh`. This script must run from the repository root in GitHub Actions, use `set -euo pipefail`, run the project-appropriate `docker compose config`, image build, runtime startup, migration/seed/readiness checks, and leave the Docker runtime running for the managed browser smoke step.
- The pipeline freezes `.pipeline/docker-ci.sh` after the first push for the job. During later fixes, do not weaken or rewrite that Docker CI script to make CI pass; fix Dockerfiles, compose, runtime config, migrations, seed data, readiness, or app code instead.
- Create a root `.gitignore` that excludes `node_modules/`, `dist/`, `build/`, `.env`, `.env.*`, coverage, caches, logs, and OS/editor files while allowing `.env.example`.
- Never commit a real `.env`. If Docker Compose uses `env_file: .env`, create a safe `.env.example` with all required keys and demo/runtime values so CI can copy `.env.example` to `.env` before `docker compose config`.
- Do not read real `.env` or `.env.*` files. Use `.env.example`, source config, and test output instead; if local commands create `.env`, leave it untracked and unopened.
- If the MVP uses an external API/vendor that normally requires a secret key, implement a CI-safe mock/demo/local provider and make it the default in `.env.example`. Docker CI and the primary browser smoke flow must pass without real secrets. Real providers may run only when a real key and explicit real provider mode are configured outside git.
- Do not commit dependencies, build outputs, local env files, logs, caches, or generated runtime artifacts.
- Do not edit `.github/workflows/ci.yml` or other managed CI files. CI is owned and regenerated by the pipeline orchestrator; fix application code, Docker/runtime config, or `mvp.config.json` instead.
- Keep all temporary logs/scripts/runtime files inside the workspace, for example `.runtime/` or `dev/tmp/`. Do not read or write `/tmp`, `/var`, `$HOME`, parent directories, or other external paths that require interactive permission approval.
- Docker/README/seed commands must match reality: the app should start from README instructions and include demo data/credentials when auth is present.
- Docker files must match reality: compose build contexts must contain the referenced Dockerfile, published ports must match `mvp.config.json` v2 readiness URLs, and seed commands must reference existing compose services.
- Use real published Docker image tags only. Do not invent tags such as `postgres:16-slim` or `redis:7-slim`; use known valid official tags such as `postgres:16-alpine`, `postgres:16`, `redis:7-alpine`, or `redis:7`.
- Docker seed commands must run inside the production container without devDependencies or TypeScript runtime tooling. Do not use `npx ts-node`, `tsx`, `ts-node/register`, or a package script that depends on them for `mvp.config.json.runtime.seed_command`; compile the seed to JavaScript or provide a runtime-safe JS seed and run it with `node`.
- Keep external API integrations CI-safe. If `.env.example` contains placeholder keys such as `OPENAI_API_KEY`, `REPLICATE_API_TOKEN`, `STRIPE_SECRET_KEY`, or similar, it must also default the relevant provider/mode to `mock`, `demo`, `local`, or `offline`, and the backend must not call the real vendor in that mode.
- If a Node service uses Prisma, a clean Docker/CI database must get its schema before seed data runs: either commit initial `prisma/migrations/*/migration.sql` files and run `prisma migrate deploy`, or run `prisma db push` before `node prisma/seed.js` in the Docker seed/init path.
- If a Node service uses Prisma, prefer Debian slim Node images with OpenSSL installed. Do not use Alpine for Prisma-backed Docker runtime unless you have explicitly verified Prisma schema engines work in the container.
- Quality gates will fail the job for dependency artifacts in git, placeholders, fake-success actions, empty handlers, or unwired mutating buttons.
- Create root `mvp.config.json` using contract v2. CI must not guess the UI. The generated app must expose stable `data-testid` targets and declare the runnable happy path:
  - `version: 2`
  - `runtime.type: "docker_compose"` and `runtime.compose_file`, with optional production-safe `runtime.seed_command`
  - `readiness`: frontend/API URLs with expected HTTP statuses
  - `fixtures.files`: logical fixture names to committed sample files, when uploads are part of the flow
  - `targets`: stable UI selectors, always using `data-testid`, for every control/outcome used by smoke flows
  - `flows`: browser steps for the primary happy path
- You are responsible for choosing the concrete `mvp.config.json` `targets` and `flows` after implementing the UI. The `expect_request` method/path/status must match `PLAN.md`'s Backend API Contract.
- Add matching `data-testid` attributes to the real UI elements referenced by `targets`. Do not use visible text, CSS layout selectors, or button labels as the contract.
- v2 flow steps should use target-based actions: `goto`, `click`, `fill`, `select`, `upload_file`, `expect_download`, `wait_for_response`, `screenshot`, `expect_styled`, and `wait_for_outcome`.
- When a submit/generate/save click should call the backend, add `expect_request` on that click, for example `{{"action":"click","target":"generate","expect_request":{{"method":"POST","url":"/api/jobs","status":[200,201,202]}}}}`.
- Do not wait for fragile intermediate UI text such as `Generating`. After the network assertion, use `wait_for_outcome` with stable success and failure targets, for example success `download_pdf` and failure `error_message`.

## Required tests

- Add backend API integration tests for the primary create/read/update path.
- Add frontend tests or smoke steps that prove the rendered UI is styled and critical controls work.
- Add frontend/backend integration coverage through the browser smoke flow: UI action -> API -> persistence -> refreshed UI.
- Add Docker runtime checks or scripts covering `docker compose config`, image build/start readiness, and seed/demo data.

## Dev Docs (required)

Create and maintain dev docs throughout your work:
- `dev/active/mvp-implementation/mvp-implementation-plan.md` — strategic plan
- `dev/active/mvp-implementation/mvp-implementation-context.md` — current state, decisions, key files
- `dev/active/mvp-implementation/mvp-implementation-tasks.md` — task checklist by phases

Update `dev/active/mvp-implementation/mvp-implementation-context.md` after completing each phase. The `SESSION PROGRESS` section must always reflect the real state.

## Blockers

If you encounter a blocker (missing dependency, ambiguous requirement, conflict in spec), **write about it explicitly in `BLOCKERS.md`**. Do NOT invent solutions or guess.
"""


FIX_PROMPT_TEMPLATE = """You need to fix specific issues found during review or testing.

## Workspace

Working directory: `{workspace_path}`

## Before you start

Read `dev/active/mvp-implementation/mvp-implementation-context.md` to understand the current state of the project.
Then inspect the current workspace files directly. Do not assume the previous session context is available.
If the dev context file is missing, do not stop and do not ask for input. Use `REQUEST.md`, `SPEC.md`, `PLAN.md`,
and `TASK.md` as context, then create or update the missing dev docs as part of the fix when relevant.

## Issues to fix

{issues_text}

## Additional context

{context}

## Rules

- Fix ONLY the listed issues. Do NOT go beyond MVP scope.
- "Fix ONLY the listed issues" means do not add unrelated product features. If a listed issue is foundational
  (`empty_mvp_workspace`, `missing_mvp_config`, `invalid_mvp_config`, `missing_readme`,
  `missing_docker_ci_script`, `invalid_docker_ci_script`), creating or updating the necessary app source,
  frontend/backend files, tests, Docker runtime, `.env.example`, README, and smoke-test contract is in scope.
- `mvp.config.json` v2 must use `runtime.type: "docker_compose"` for this pipeline. Do not switch to a
  non-Docker runtime to avoid Docker setup.
- If the issue list contains `missing_mvp_config`, create a root `mvp.config.json` before finishing. It must
  declare runtime, readiness, data-testid targets, flows, `expect_request`, and `wait_for_outcome` for a real
  runnable happy path.
- If the issue list contains `missing_readme`, create a root `README.md` before finishing. It must document the
  actual Docker Compose setup/run commands, ports, env bootstrap, and demo/seed flow that exist in the workspace.
- Before finishing any foundational fix, run file-existence checks for the exact missing root files, for example
  `test -f mvp.config.json` and `test -f README.md`.
- If an issue says a visible control is a placeholder or fake action, either implement the full UI -> API -> persistence flow or remove the control from the UI.
- If an issue says the UI is unstyled/browser-default, fix the CSS pipeline and verify compiled styles actually affect visible controls.
- If an issue says the UI is low-quality, raw/default, overlapping, or visually broken, fix the frontend layout/design system directly. Do not only make tests pass.
- If a create/edit form requires current-user fields such as managerId/assigneeId, default them from the authenticated user or make sure the demo user appears in an accessible select. The demo user must be able to create the primary record without admin-only lookup data.
- Keep the root `.gitignore` correct and never commit dependencies, build outputs, `.env` files, logs, or caches.
- Keep Docker env handling clean: real `.env` stays untracked; `.env.example` contains safe demo/runtime defaults; compose must work in CI after copying `.env.example` to `.env`.
- Do not read real `.env` or `.env.*` files. Use `.env.example`, source config, and test output instead; if local commands create `.env`, leave it untracked and unopened.
- Keep the root `README.md` accurate. If run commands, ports, seed/demo commands, env bootstrap, or demo credentials change, update README to match the actual Docker runtime and `mvp.config.json`.
- Keep external API integrations CI-safe. If `.env.example` contains placeholder keys such as `OPENAI_API_KEY`, `REPLICATE_API_TOKEN`, `STRIPE_SECRET_KEY`, or similar, it must also default the relevant provider/mode to `mock`, `demo`, `local`, or `offline`, and the backend must not call the real vendor in that mode.
- Use only real published Docker image tags in compose. If runtime logs mention `manifest unknown`, replace the image tag with a valid official tag rather than changing tests.
- Keep Docker seed commands production-safe. If `mvp.config.json.runtime.seed_command` runs inside a Docker Compose service, it must not call `npx ts-node`, `tsx`, `ts-node/register`, or an npm script that depends on TypeScript runtime tooling. Compile the seed or add a runtime-safe JS seed that can run with `node` in the production image.
- Keep `.pipeline/docker-ci.sh` aligned with the originally generated Docker runtime check. If Docker CI fails after the first push, fix the Docker/runtime/app cause rather than weakening the frozen Docker CI contract.
- Keep Prisma schema initialization production-safe. If CI logs show missing tables such as `public.User` or `No migration found in prisma/migrations`, add committed Prisma migrations and run `prisma migrate deploy`, or make the Docker seed/init path run `prisma db push` before `node prisma/seed.js`.
- Keep all temporary logs/scripts/runtime files inside the workspace, for example `.runtime/` or `dev/tmp/`. Do not read or write `/tmp`, `/var`, `$HOME`, parent directories, or other external paths that require interactive permission approval.
- Keep `mvp.config.json` accurate. Prefer v2 with `runtime`, `readiness`, data-testid `targets`, `fixtures`, and `flows`. If you change ports, demo credentials, seed commands, targets, or the primary workflow, update the smoke-test contract too.
- Keep backend APIs aligned with `PLAN.md`'s Backend API Contract. During fixes, do not change endpoint methods, paths, request/response shapes, or status codes just to make the smoke test easier; align the UI and `expect_request` checks to the planned contract unless the issue explicitly identifies the plan as wrong.
- Do not edit `.github/workflows/ci.yml` or generated CI/test infrastructure to hide failures. The orchestrator restores managed CI before push; fix the product/runtime cause or the supported `mvp.config.json` smoke-flow contract.
- If browser smoke fails on a target-based v2 flow, fix the real UI target/data-testid, backend request, or final outcome. Do not replace `expect_request` / `wait_for_outcome` with fragile text waits such as `Generating`.
- If browser smoke diagnostics show a protected route redirecting back to `/login?redirect=...` after login, do not weaken the smoke expectation or reroute the test. Fix the auth flow: login API errors, response shape, token/cookie storage key, route guard, session hydration, and seeded demo credentials.
- If an issue code is `frontend_empty_terminal_dom`, the backend create/preview path already succeeded. Start with frontend terminal-state rendering, polling lifecycle, and preservation of final job data; do not start by rewriting Docker, backend generation, or the PDF/preview pipeline unless the diagnostic evidence is disproven.
- Use browser smoke diagnostics from CI or local output directly: failing step path, current URL, page text, storage keys, cookie metadata, console errors, failed requests, 4xx/5xx responses, recent network requests, HTML tail, and any screenshot/html artifacts.
- Treat any issue acceptance checks as mandatory before stopping. For protected-route auth redirects, verify with the Docker browser smoke flow or an equivalent Playwright flow that logs in and reaches the expected protected route.
- If an issue code is `ci_failure_diagnostic`, use its evidence as the routing map: identify the failed command/job/step, search exact error text in the workspace, inspect the related checks, and fix the application/runtime cause. Do not change CI, tests, or smoke expectations just to hide the failure.
- After fixing, ensure all tests pass locally.
- Tests must cover the primary runtime contract, not just isolated functions:
  - backend API integration tests for the main create/read/update path;
  - frontend tests or smoke steps that prove the rendered UI is styled and critical controls work;
  - frontend/backend integration via the browser smoke flow;
  - Docker runtime checks such as `docker compose config`, build/start readiness, and seed/demo data.
- If CI/runtime context includes Docker logs, fix the concrete container error from those logs. For Prisma/OpenSSL/libssl errors, change the service Dockerfile/runtime base image and dependencies rather than hiding the failing command.
- Update `SESSION PROGRESS` in `dev/active/mvp-implementation/mvp-implementation-context.md`.
- Update tasks in `dev/active/mvp-implementation/mvp-implementation-tasks.md`.
"""


class CodingService:
    def __init__(
        self,
        opencode_client,
        timeout: int = 600,
        busy_stable_seconds: int = 120,
        stuck_seconds: int = 600,
        empty_diff_followup_min_stable_seconds: int = 500,
        max_empty_diff_followups: int = 1,
        clock=time.monotonic,
    ):
        self._client = opencode_client
        self._timeout = timeout
        self._busy_stable_seconds = busy_stable_seconds
        self._stuck_seconds = stuck_seconds
        self._empty_diff_followup_min_stable_seconds = empty_diff_followup_min_stable_seconds
        self._max_empty_diff_followups = max_empty_diff_followups
        self._clock = clock
        self._busy_observations: dict[str, _BusyObservation] = {}
        self._empty_diff_followups: dict[str, int] = {}
        self._auto_denied_permissions: set[tuple[str, str]] = set()

    async def start(self, job_id: str, workspace_path: str) -> str:
        session_id = await self._client.create_session(title=f"job_{job_id}", workspace_path=workspace_path)
        logger.info("coding_service.session_created", job_id=job_id, session_id=session_id)

        prompt = TASK_PROMPT_TEMPLATE.format(workspace_path=workspace_path)
        await self._client.send_prompt_async(session_id, prompt)
        logger.info("coding_service.run_dispatched", job_id=job_id, session_id=session_id)
        return session_id

    async def is_complete(self, job_id: str, session_id: str) -> bool:
        state = await self.check_state(job_id, session_id)
        return state == OpenCodeRunState.COMPLETED

    async def check_state(self, job_id: str, session_id: str) -> OpenCodeRunState:
        is_busy = await self._client.is_session_busy(session_id)
        if is_busy:
            waiting_state = await self._detect_waiting_state(job_id, session_id)
            if waiting_state is not None:
                return waiting_state
            return await self._state_despite_busy_status(job_id, session_id)

        diff = await self._client.get_diff(session_id)
        self._busy_observations.pop(session_id, None)
        if not diff:
            empty_diff_state = await self._empty_diff_message_state(job_id, session_id)
            if empty_diff_state == "model_error":
                return OpenCodeRunState.MODEL_ERROR
            if empty_diff_state == "empty_result":
                return OpenCodeRunState.EMPTY_RESULT
            if empty_diff_state in {"awaiting_assistant", "unknown"}:
                return await self._state_despite_busy_status(job_id, session_id)
        else:
            self._empty_diff_followups.pop(session_id, None)
        _log_completed(job_id, session_id, diff)
        return OpenCodeRunState.COMPLETED

    async def _state_despite_busy_status(self, job_id: str, session_id: str) -> OpenCodeRunState:
        try:
            diff = await self._client.get_diff(session_id)
        except Exception as exc:
            logger.warning(
                "coding_service.busy_diff_check_failed",
                job_id=job_id,
                session_id=session_id,
                error=str(exc),
            )
            return OpenCodeRunState.RUNNING

        now = self._clock()
        diff_fields = _diff_log_fields(diff)
        activity_messages = await self._session_messages_for_activity(job_id, session_id)
        signature = _run_activity_signature(diff, activity_messages)
        observation = self._busy_observations.get(session_id)
        if observation is None or observation.signature != signature:
            self._busy_observations[session_id] = _BusyObservation(
                signature=signature,
                first_seen_at=now,
                last_seen_at=now,
            )
            logger.info(
                "coding_service.run_still_active",
                job_id=job_id,
                session_id=session_id,
                stable_seconds=0,
                **diff_fields,
            )
            return OpenCodeRunState.RUNNING

        observation.last_seen_at = now
        stable_seconds = now - observation.first_seen_at
        if not diff and stable_seconds >= self._empty_diff_followup_min_stable_seconds:
            if await self._send_empty_diff_followup(
                job_id,
                session_id,
                reason="busy_empty_diff",
                stable_seconds=stable_seconds,
            ):
                return OpenCodeRunState.RUNNING

        if not diff and stable_seconds >= self._stuck_seconds:
            self._busy_observations.pop(session_id, None)
            logger.warning(
                "coding_service.stuck_detected",
                job_id=job_id,
                session_id=session_id,
                stable_seconds=round(stable_seconds, 1),
                **diff_fields,
            )
            return OpenCodeRunState.STUCK

        if not diff:
            logger.info(
                "coding_service.run_still_active",
                job_id=job_id,
                session_id=session_id,
                stable_seconds=round(stable_seconds, 1),
                **diff_fields,
            )
            return OpenCodeRunState.RUNNING

        if stable_seconds < self._busy_stable_seconds:
            logger.info(
                "coding_service.run_still_active",
                job_id=job_id,
                session_id=session_id,
                stable_seconds=round(stable_seconds, 1),
                **diff_fields,
            )
            return OpenCodeRunState.RUNNING

        self._empty_diff_followups.pop(session_id, None)
        self._busy_observations.pop(session_id, None)
        logger.warning(
            "coding_service.busy_status_overridden",
            job_id=job_id,
            session_id=session_id,
            stable_seconds=round(stable_seconds, 1),
            **diff_fields,
        )
        _log_completed(job_id, session_id, diff, status_override=True)
        return OpenCodeRunState.COMPLETED

    async def _session_messages_for_activity(self, job_id: str, session_id: str) -> list:
        try:
            return await self._client.get_session_messages(session_id)
        except Exception as exc:
            logger.warning(
                "coding_service.activity_messages_check_failed",
                job_id=job_id,
                session_id=session_id,
                error=str(exc),
            )
            return []

    async def _send_empty_diff_followup(
        self,
        job_id: str,
        session_id: str,
        *,
        reason: str,
        stable_seconds: float | None = None,
    ) -> bool:
        sent_count = self._empty_diff_followups.get(session_id, 0)
        if sent_count >= self._max_empty_diff_followups:
            logger.info(
                "coding_service.empty_diff_followup_skipped",
                job_id=job_id,
                session_id=session_id,
                reason=reason,
                followups=sent_count,
                max_followups=self._max_empty_diff_followups,
                stable_seconds=round(stable_seconds, 1) if stable_seconds is not None else None,
            )
            return False

        self._empty_diff_followups[session_id] = sent_count + 1
        try:
            await self._client.send_prompt_async(session_id, EMPTY_DIFF_CONTINUE_PROMPT)
        except Exception as exc:
            logger.warning(
                "coding_service.empty_diff_followup_failed",
                job_id=job_id,
                session_id=session_id,
                reason=reason,
                followups=self._empty_diff_followups[session_id],
                error=str(exc),
            )
            return False

        logger.warning(
            "coding_service.empty_diff_followup_sent",
            job_id=job_id,
            session_id=session_id,
            reason=reason,
            followups=self._empty_diff_followups[session_id],
            stable_seconds=round(stable_seconds, 1) if stable_seconds is not None else None,
        )
        return True

    async def _detect_waiting_state(self, job_id: str, session_id: str) -> OpenCodeRunState | None:
        try:
            messages = await self._client.get_session_messages(session_id)
        except Exception as exc:
            logger.warning(
                "coding_service.session_messages_check_failed",
                job_id=job_id,
                session_id=session_id,
                error=str(exc),
            )
            messages = []

        messages_state = _detect_waiting_state(messages, session_id, source_is_session_scoped=True)
        if messages_state == OpenCodeRunState.WAITING_INPUT:
            _log_waiting_state_detected(job_id, session_id, messages_state, source="messages")
            return messages_state

        try:
            events = await self._client.read_events()
        except Exception as exc:
            logger.warning(
                "coding_service.event_check_failed",
                job_id=job_id,
                session_id=session_id,
                error=str(exc),
            )
            events = []

        events_state = _detect_waiting_state(events, session_id, source_is_session_scoped=False)
        if events_state == OpenCodeRunState.WAITING_INPUT:
            _log_waiting_state_detected(job_id, session_id, events_state, source="events")
            return events_state

        if await self._auto_deny_sensitive_env_permissions(
            job_id,
            session_id,
            messages,
            source="messages",
            source_is_session_scoped=True,
        ):
            return OpenCodeRunState.RUNNING

        if await self._auto_deny_sensitive_env_permissions(
            job_id,
            session_id,
            events,
            source="events",
            source_is_session_scoped=False,
        ):
            return OpenCodeRunState.RUNNING

        if messages_state is not None:
            _log_waiting_state_detected(job_id, session_id, messages_state, source="messages")
            return messages_state

        if events_state is not None:
            _log_waiting_state_detected(job_id, session_id, events_state, source="events")
            return events_state
        return None

    async def _auto_deny_sensitive_env_permissions(
        self,
        job_id: str,
        session_id: str,
        payload,
        *,
        source: str,
        source_is_session_scoped: bool,
    ) -> bool:
        permission_ids = _sensitive_env_permission_ids(
            payload,
            session_id,
            source_is_session_scoped=source_is_session_scoped,
        )
        handled = False
        for permission_id in permission_ids:
            permission_key = (session_id, permission_id)
            if permission_key in self._auto_denied_permissions:
                handled = True
                continue

            try:
                await self._client.reply_permission(
                    session_id,
                    permission_id,
                    response="reject",
                    remember=False,
                )
            except Exception as exc:
                logger.warning(
                    "coding_service.env_read_auto_deny_failed",
                    job_id=job_id,
                    session_id=session_id,
                    permission_id=permission_id,
                    source=source,
                    error=str(exc),
                )
                continue

            self._auto_denied_permissions.add(permission_key)
            handled = True
            logger.warning(
                "coding_service.env_read_auto_denied",
                job_id=job_id,
                session_id=session_id,
                permission_id=permission_id,
                source=source,
            )
        return handled

    async def _empty_diff_message_state(self, job_id: str, session_id: str) -> str:
        try:
            messages = await self._client.get_session_messages(session_id)
        except Exception as exc:
            logger.warning(
                "coding_service.empty_result_check_failed",
                job_id=job_id,
                session_id=session_id,
                error=str(exc),
            )
            return "unknown"

        if not messages:
            logger.warning(
                "coding_service.empty_result_no_messages",
                job_id=job_id,
                session_id=session_id,
            )
            return "empty_result"

        for msg in messages:
            if _is_model_error_message(msg):
                logger.warning(
                    "coding_service.empty_result_model_error",
                    job_id=job_id,
                    session_id=session_id,
                )
                return "model_error"

        if not any(_is_assistant_message(msg) for msg in messages):
            logger.info(
                "coding_service.empty_diff_awaiting_assistant",
                job_id=job_id,
                session_id=session_id,
                message_count=len(messages),
            )
            return "awaiting_assistant"

        return "messages_present"

    async def abort(self, session_id: str) -> None:
        await self._client.abort_session(session_id)

    async def run(self, job_id: str, workspace_path: str) -> str:
        session_id = await self._client.create_session(title=f"job_{job_id}", workspace_path=workspace_path)
        logger.info("coding_service.session_created", job_id=job_id, session_id=session_id)

        prompt = TASK_PROMPT_TEMPLATE.format(workspace_path=workspace_path)

        try:
            await asyncio.wait_for(
                self._client.send_message(session_id, prompt),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            logger.error("coding_service.run_timeout", job_id=job_id)
            await self._client.abort_session(session_id)
            raise RuntimeError(f"OpenCode coding timeout for job {job_id}")

        diff = await self._client.get_diff(session_id)
        logger.info("coding_service.run_completed", job_id=job_id, **_diff_log_fields(diff))

        return session_id

    async def start_fix(self, job_id: str, workspace_path: str, issues: list[dict], context: str = "") -> str:
        session_id = await self._client.create_session(title=f"job_{job_id}_fix", workspace_path=workspace_path)
        logger.info("coding_service.fix_session_created", job_id=job_id, session_id=session_id)

        prompt = _build_fix_prompt(workspace_path, issues, context)
        await self._client.send_prompt_async(session_id, prompt)
        logger.info("coding_service.fix_dispatched", job_id=job_id, session_id=session_id)
        return session_id

    async def fix(self, session_id: str, issues: list[dict], context: str = "") -> None:
        prompt = _build_fix_prompt(".", issues, context)

        try:
            await asyncio.wait_for(
                self._client.send_message(session_id, prompt),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            logger.error("coding_service.fix_timeout", session_id=session_id)
            await self._client.abort_session(session_id)
            raise RuntimeError(f"OpenCode fix timeout for session {session_id}")

        logger.info("coding_service.fix_completed", session_id=session_id)


def _build_fix_prompt(workspace_path: str, issues: list[dict], context: str = "") -> str:
    issues_text = _format_issues_for_prompt(issues) if issues else "No specific issues listed."

    return FIX_PROMPT_TEMPLATE.format(
        workspace_path=workspace_path,
        issues_text=issues_text,
        context=context or "No additional context.",
    )


def _format_issues_for_prompt(issues: list[dict]) -> str:
    formatted = []
    for item in issues:
        severity = item.get("severity", "unknown")
        code = item.get("code")
        description = item.get("description", "")
        header = f"- [{severity}]"
        if code:
            header += f" {code}:"
        formatted.append(f"{header} {description}".rstrip())
        path = item.get("path")
        line = item.get("line")
        if path:
            formatted.append(f"  Path: {path}:{line}" if line else f"  Path: {path}")
        _append_prompt_list(formatted, "Evidence", item.get("evidence"))
        _append_prompt_list(formatted, "Related checks", item.get("related_checks"))
        _append_prompt_list(formatted, "Acceptance checks", item.get("acceptance_checks"))
    return "\n".join(formatted)


def _append_prompt_list(lines: list[str], title: str, values) -> None:
    if not values:
        return
    if not isinstance(values, list):
        values = [values]
    lines.append(f"  {title}:")
    for value in values:
        lines.append(f"  - {value}")


@dataclass
class _BusyObservation:
    signature: str
    first_seen_at: float
    last_seen_at: float


@dataclass
class _DiffStats:
    files_changed: int
    insertions: int
    deletions: int
    patch_chars: int
    payload_chars: int
    signature: str


def _diff_signature(diff: list) -> str:
    payload = json.dumps(diff, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _run_activity_signature(diff: list, messages: list) -> str:
    payload = {
        "diff": diff,
        "messages": _messages_activity_payload(messages),
    }
    serialized = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _messages_activity_payload(messages: list) -> list:
    if not isinstance(messages, list):
        return []

    activity = []
    for msg in messages[-8:]:
        if not isinstance(msg, dict):
            continue
        info = msg.get("info") if isinstance(msg.get("info"), dict) else {}
        parts = msg.get("parts") if isinstance(msg.get("parts"), list) else []
        activity.append(
            {
                "id": msg.get("id") or info.get("id"),
                "role": _message_role(msg),
                "finish": msg.get("finish") or info.get("finish"),
                "completed": _nested_get(info, "time", "completed"),
                "parts": [_part_activity_payload(part) for part in parts[-12:]],
            }
        )
    return activity


def _part_activity_payload(part) -> dict:
    if not isinstance(part, dict):
        return {}
    state = part.get("state") if isinstance(part.get("state"), dict) else {}
    return {
        "id": part.get("id"),
        "type": part.get("type"),
        "tool": part.get("tool"),
        "callID": part.get("callID"),
        "status": state.get("status"),
        "title": state.get("title"),
    }


def _nested_get(value, *keys):
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _diff_log_fields(diff: list) -> dict:
    stats = _diff_stats(diff)
    return {
        "files_changed": stats.files_changed,
        "diff_insertions": stats.insertions,
        "diff_deletions": stats.deletions,
        "diff_patch_chars": stats.patch_chars,
        "diff_payload_chars": stats.payload_chars,
        "diff_signature": stats.signature[:12],
    }


def _diff_stats(diff: list) -> "_DiffStats":
    payload = json.dumps(diff, sort_keys=True, default=str, separators=(",", ":"))
    patch_text = _diff_patch_text(diff)
    insertions, deletions = _count_unified_patch_changes(patch_text)
    return _DiffStats(
        files_changed=len(diff) if isinstance(diff, list) else 0,
        insertions=insertions,
        deletions=deletions,
        patch_chars=len(patch_text),
        payload_chars=len(payload),
        signature=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    )


def _diff_patch_text(diff) -> str:
    chunks: list[str] = []
    for item in diff if isinstance(diff, list) else []:
        _append_patch_chunks(chunks, item)
    return "\n".join(chunk for chunk in chunks if chunk)


def _append_patch_chunks(chunks: list[str], value) -> None:
    if isinstance(value, str):
        return
    if isinstance(value, list):
        for item in value:
            _append_patch_chunks(chunks, item)
        return
    if not isinstance(value, dict):
        return

    for key in ("patch", "diff"):
        patch_value = value.get(key)
        if isinstance(patch_value, str):
            chunks.append(patch_value)

    changes = value.get("changes")
    if isinstance(changes, list):
        for item in changes:
            if isinstance(item, str):
                chunks.append(item)
            else:
                _append_patch_chunks(chunks, item)
    elif isinstance(changes, str):
        chunks.append(changes)


def _count_unified_patch_changes(patch_text: str) -> tuple[int, int]:
    insertions = 0
    deletions = 0
    for line in patch_text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            insertions += 1
        elif line.startswith("-"):
            deletions += 1
    return insertions, deletions


def _log_completed(job_id: str, session_id: str, diff: list, status_override: bool = False) -> None:
    logger.info(
        "coding_service.run_completed",
        job_id=job_id,
        session_id=session_id,
        status_override=status_override,
        **_diff_log_fields(diff),
    )


def _log_waiting_state_detected(job_id: str, session_id: str, state: OpenCodeRunState, *, source: str) -> None:
    logger.warning(
        "coding_service.waiting_state_detected",
        job_id=job_id,
        session_id=session_id,
        state=state.value,
        source=source,
    )


def _detect_waiting_state(payload, session_id: str, source_is_session_scoped: bool = False) -> OpenCodeRunState | None:
    if not isinstance(payload, (dict, list)):
        return None

    states: list[OpenCodeRunState] = []
    for item in _walk_payload(payload):
        if not isinstance(item, dict):
            continue
        if not source_is_session_scoped and not _matches_session(item, session_id):
            continue
        state = _waiting_state_from_item(item)
        if state is not None:
            states.append(state)

    if OpenCodeRunState.WAITING_INPUT in states:
        return OpenCodeRunState.WAITING_INPUT
    if OpenCodeRunState.WAITING_PERMISSION in states:
        return OpenCodeRunState.WAITING_PERMISSION

    text = _payload_text(_recent_payload(payload)).lower()
    if source_is_session_scoped and _looks_like_waiting_question(text):
        return OpenCodeRunState.WAITING_INPUT
    if source_is_session_scoped and _looks_like_waiting_permission(text):
        return OpenCodeRunState.WAITING_PERMISSION
    return None


def _walk_payload(value):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_payload(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_payload(child)


def _matches_session(item: dict, session_id: str) -> bool:
    return _payload_contains_session_id(item, session_id)


def _payload_contains_session_id(value, session_id: str) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"sessionID", "sessionId", "session_id"} and str(child) == session_id:
                return True
            if _payload_contains_session_id(child, session_id):
                return True
    elif isinstance(value, list):
        return any(_payload_contains_session_id(child, session_id) for child in value)
    return False


def _waiting_state_from_item(item: dict) -> OpenCodeRunState | None:
    event_type = str(item.get("event") or item.get("type") or item.get("name") or "").lower()
    data = item.get("data") if isinstance(item.get("data"), dict) else item
    if isinstance(data, dict) and isinstance(data.get("properties"), dict):
        data = data["properties"]
    if isinstance(item.get("properties"), dict):
        data = item["properties"]
    data_type = str(data.get("type") or data.get("permissionType") or data.get("permission_type") or "").lower()
    status = str(data.get("status") or data.get("state") or "").lower()

    if "permission.replied" in event_type or status in {"replied", "approved", "rejected", "resolved", "done"}:
        return None

    if _is_pending_sensitive_env_read(item):
        return OpenCodeRunState.WAITING_PERMISSION

    if "permission" in event_type or data.get("permissionID") or data.get("permissionId") or data.get("permission_id"):
        if data_type == "question" or _looks_like_waiting_question(_payload_text(data).lower()):
            return OpenCodeRunState.WAITING_INPUT
        return OpenCodeRunState.WAITING_PERMISSION

    if data_type == "question" and status not in {"replied", "resolved", "done"}:
        return OpenCodeRunState.WAITING_INPUT
    return None


def _sensitive_env_permission_ids(
    payload,
    session_id: str,
    source_is_session_scoped: bool = False,
) -> list[str]:
    if not isinstance(payload, (dict, list)):
        return []

    permission_ids: list[str] = []
    seen: set[str] = set()
    for item in _walk_payload(payload):
        if not isinstance(item, dict):
            continue
        if not source_is_session_scoped and not _matches_session(item, session_id):
            continue

        permission_id = _pending_permission_id(item)
        if not permission_id or permission_id in seen:
            continue
        if _is_sensitive_env_permission(item):
            seen.add(permission_id)
            permission_ids.append(permission_id)

    return permission_ids


def _pending_permission_id(item: dict) -> str | None:
    event_type = str(item.get("event") or item.get("type") or item.get("name") or "").lower()
    data = _permission_data(item)
    status = str(data.get("status") or data.get("state") or "").lower()
    if "permission.replied" in event_type or status in {"replied", "approved", "rejected", "resolved", "done"}:
        return None
    if not _looks_like_permission_payload(item, data):
        return None

    for source in (data, item):
        for key in ("permissionID", "permissionId", "permission_id"):
            value = source.get(key)
            if value:
                return str(value)

    value = data.get("id")
    return str(value) if value else None


def _permission_data(item: dict) -> dict:
    data = item.get("data") if isinstance(item.get("data"), dict) else item
    if isinstance(data, dict) and isinstance(data.get("properties"), dict):
        data = data["properties"]
    if isinstance(item.get("properties"), dict):
        data = item["properties"]
    return data if isinstance(data, dict) else {}


def _looks_like_permission_payload(item: dict, data: dict) -> bool:
    event_type = str(item.get("event") or item.get("type") or item.get("name") or "").lower()
    if "permission" in event_type:
        return True
    if any(data.get(key) or item.get(key) for key in ("permissionID", "permissionId", "permission_id")):
        return True
    return bool(data.get("sessionID") or data.get("sessionId") or data.get("session_id")) and bool(data.get("title"))


def _is_sensitive_env_permission(item: dict) -> bool:
    data = _permission_data(item)
    data_type = str(data.get("type") or data.get("permissionType") or data.get("permission_type") or "").lower()
    if data_type not in {"read", "file.read"} and not _is_pending_sensitive_env_read(item):
        return False
    return _contains_sensitive_env_path(data) or _contains_sensitive_env_path(item)


def _is_pending_sensitive_env_read(item: dict) -> bool:
    if str(item.get("tool") or "").lower() != "read":
        return False

    state = item.get("state") if isinstance(item.get("state"), dict) else {}
    status = str(state.get("status") or item.get("status") or "").lower()
    if status in {"completed", "error", "failed", "cancelled", "canceled", "aborted"}:
        return False

    input_data = state.get("input") if isinstance(state.get("input"), dict) else item.get("input")
    if not isinstance(input_data, dict):
        return False

    file_path = str(input_data.get("filePath") or input_data.get("path") or input_data.get("file") or "")
    return _is_sensitive_env_path(file_path)


def _contains_sensitive_env_path(value) -> bool:
    for text in _walk_strings(value):
        for token in re.split(r"[\s'\"`]+", text):
            candidate = token.strip(".,:;()[]{}<>")
            if candidate and _is_sensitive_env_path(candidate):
                return True
    return False


def _walk_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _walk_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_strings(child)


def _is_sensitive_env_path(file_path: str) -> bool:
    name = file_path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    if name == ".env.example" or name.endswith(".env.example"):
        return False
    return name == ".env" or name.startswith(".env.") or name.endswith(".env")


def _payload_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_payload_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_payload_text(item) for item in value)
    return ""


def _message_role(msg) -> str:
    if not isinstance(msg, dict):
        return ""
    role = msg.get("role")
    if not role and isinstance(msg.get("info"), dict):
        role = msg["info"].get("role")
    return str(role or "").lower()


def _is_assistant_message(msg) -> bool:
    return _message_role(msg) == "assistant"


def _recent_payload(value):
    if isinstance(value, dict):
        if isinstance(value.get("messages"), list):
            return value["messages"][-3:]
        if isinstance(value.get("items"), list):
            return value["items"][-3:]
        if isinstance(value.get("data"), list):
            return value["data"][-3:]
    if isinstance(value, list):
        return value[-3:]
    return value


def _looks_like_waiting_permission(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "permission required",
            "waiting for permission",
            "external_directory",
            "access external directory",
            "allow once",
            "allow always",
        )
    )


_MODEL_ERROR_PATTERNS = (
    "free promotion has ended",
    "modelerror",
    "not supported for format",
    "provider.*not found",
    "provider.*model.*not.*found",
    "model not supported",
    "unsupported model",
    "invalid model",
    "no model configured",
    "model.*not found",
)


def _is_model_error_message(msg) -> bool:
    text = _payload_text(msg).lower()
    return any((re.search(pattern, text) if ".*" in pattern else pattern in text) for pattern in _MODEL_ERROR_PATTERNS)


def _looks_like_waiting_question(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "waiting for user input",
            "waiting for input",
            "please confirm",
            "need confirmation",
        )
    )
