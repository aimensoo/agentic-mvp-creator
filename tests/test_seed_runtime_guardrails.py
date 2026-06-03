import json
from pathlib import Path

from services.coding_service import FIX_PROMPT_TEMPLATE, TASK_PROMPT_TEMPLATE
from services.quality_gate_service import _mvp_config_issues, _prisma_migration_sql_issues, _seed_runtime_command_issues


def test_quality_gate_rejects_ts_node_seed_command(tmp_path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        """
services:
  backend:
    build: ./backend
""".lstrip()
    )

    issues = _seed_runtime_command_issues(
        tmp_path,
        compose,
        compose.read_text(),
        "docker compose exec backend npx ts-node prisma/seed.ts",
    )

    assert [issue.code for issue in issues] == ["typescript_seed_runtime_dependency"]
    assert "production runtime" in issues[0].description


def test_quality_gate_rejects_npm_seed_script_using_ts_node(tmp_path):
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "seed": "ts-node prisma/seed.ts",
                }
            }
        )
    )
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        """
services:
  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
""".lstrip()
    )

    issues = _seed_runtime_command_issues(
        tmp_path,
        compose,
        compose.read_text(),
        "docker compose exec backend npm run seed",
    )

    assert [issue.code for issue in issues] == ["typescript_seed_runtime_dependency"]
    assert "npm run seed" in issues[0].description


def test_quality_gate_rejects_prisma_seed_without_schema_init(tmp_path):
    backend = tmp_path / "backend"
    prisma = backend / "prisma"
    prisma.mkdir(parents=True)
    (backend / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {
                    "@prisma/client": "^5.22.0",
                    "prisma": "^5.22.0",
                },
                "scripts": {
                    "seed": "node prisma/seed.js",
                    "start": "node dist/main.js",
                },
            }
        )
    )
    (prisma / "schema.prisma").write_text(
        """
datasource db {
  provider = "postgresql"
  url      = env("DATABASE_URL")
}

model User {
  id String @id @default(cuid())
}
""".lstrip()
    )
    (backend / "Dockerfile").write_text('CMD ["node", "dist/main.js"]\n')
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        """
services:
  backend:
    build: ./backend
""".lstrip()
    )

    issues = _seed_runtime_command_issues(
        tmp_path,
        compose,
        compose.read_text(),
        "docker compose exec backend npm run seed",
    )

    assert [issue.code for issue in issues] == ["missing_prisma_schema_init"]
    assert "Clean CI" in issues[0].description


def test_quality_gate_rejects_prisma_migrate_deploy_without_migrations(tmp_path):
    backend = tmp_path / "backend"
    prisma = backend / "prisma"
    prisma.mkdir(parents=True)
    (backend / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {
                    "@prisma/client": "^5.22.0",
                    "prisma": "^5.22.0",
                },
                "scripts": {
                    "seed": "node prisma/seed.js",
                    "start": "npx prisma migrate deploy && node dist/main.js",
                },
            }
        )
    )
    (prisma / "schema.prisma").write_text(
        """
datasource db {
  provider = "postgresql"
  url      = env("DATABASE_URL")
}

model User {
  id String @id @default(cuid())
}
""".lstrip()
    )
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        """
services:
  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
""".lstrip()
    )

    issues = _seed_runtime_command_issues(
        tmp_path,
        compose,
        compose.read_text(),
        "docker compose exec backend npm run seed",
    )

    assert [issue.code for issue in issues] == ["missing_prisma_migrations"]
    assert "No migration" not in issues[0].description
    assert "migration.sql" in issues[0].description


def test_quality_gate_rejects_hash_comments_in_prisma_migration_sql(tmp_path):
    backend = tmp_path / "backend"
    migration = backend / "prisma" / "migrations" / "20240101000001_partial_task_index" / "migration.sql"
    migration.parent.mkdir(parents=True)
    migration.write_text(
        """
# Please make sure to read the migration rules before editing this file.
-- Create index
CREATE INDEX "Task_due_at_active_idx" ON "Task"("due_at") WHERE "completed_at" IS NULL;
""".lstrip()
    )

    issues = _prisma_migration_sql_issues(tmp_path, [(backend, backend / "Dockerfile")])

    assert [issue.code for issue in issues] == ["invalid_prisma_migration_sql"]
    assert issues[0].path == "backend/prisma/migrations/20240101000001_partial_task_index/migration.sql"
    assert issues[0].line == 1
    assert "P3018" in issues[0].description


def test_quality_gate_accepts_prisma_seed_with_db_push(tmp_path):
    backend = tmp_path / "backend"
    prisma = backend / "prisma"
    prisma.mkdir(parents=True)
    (backend / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {
                    "@prisma/client": "^5.22.0",
                    "prisma": "^5.22.0",
                },
                "scripts": {
                    "seed": "prisma db push && node prisma/seed.js",
                },
            }
        )
    )
    (prisma / "schema.prisma").write_text("model User { id String @id @default(cuid()) }\n")
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        """
services:
  backend:
    build: ./backend
""".lstrip()
    )

    issues = _seed_runtime_command_issues(
        tmp_path,
        compose,
        compose.read_text(),
        "docker compose exec backend npm run seed",
    )

    assert issues == []


def test_opencode_prompts_require_production_safe_seed_commands():
    for prompt in (TASK_PROMPT_TEMPLATE, FIX_PROMPT_TEMPLATE):
        assert "production" in prompt
        assert "ts-node" in prompt
        assert "runtime-safe JS seed" in prompt
        assert "prisma db push" in prompt
        assert "prisma migrate deploy" in prompt


def test_quality_gate_rejects_click_steps_without_selector_or_text(tmp_path):
    (tmp_path / "mvp.config.json").write_text(
        json.dumps(
            {
                "version": 2,
                "runtime": {"type": "docker_compose", "compose_file": "docker-compose.yml"},
                "readiness": [{"name": "frontend", "url": "http://localhost:3000", "expect_status": 200}],
                "targets": {
                    "done": {"selector": "[data-testid='done']"},
                    "error": {"selector": "[data-testid='error']"},
                },
                "flows": [
                    {
                        "id": "primary_happy_path",
                        "steps": [
                            {"action": "click"},
                            {
                                "action": "wait_for_outcome",
                                "success": {"target": "done"},
                                "failure": {"target": "error"},
                            },
                        ],
                    }
                ],
            }
        )
    )

    issues = _mvp_config_issues(tmp_path)

    assert any(issue.code == "invalid_smoke_step" for issue in issues)
    assert any("flows[0].steps[0]" in issue.description for issue in issues)


def test_quality_gate_accepts_supported_upload_and_wait_smoke_steps(tmp_path):
    (tmp_path / "test-image.png").write_bytes(b"image")
    (tmp_path / "mvp.config.json").write_text(
        json.dumps(
            {
                "version": 2,
                "runtime": {"type": "docker_compose", "compose_file": "docker-compose.yml"},
                "readiness": [{"name": "frontend", "url": "http://localhost:3000", "expect_status": 200}],
                "fixtures": {"files": {"sample_image": "test-image.png"}},
                "targets": {
                    "file_upload": {"selector": "[data-testid='file-upload']"},
                    "generate": {"selector": "[data-testid='generate']"},
                    "download_pdf": {"selector": "[data-testid='download-pdf']"},
                    "error": {"selector": "[data-testid='error']"},
                },
                "flows": [
                    {
                        "id": "primary_happy_path",
                        "steps": [
                            {"action": "upload_file", "target": "file_upload", "file": "sample_image"},
                            {
                                "action": "click",
                                "target": "generate",
                                "expect_request": {"method": "POST", "url": "/api/jobs", "status": [201]},
                            },
                            {
                                "action": "wait_for_outcome",
                                "success": {"target": "download_pdf"},
                                "failure": {"target": "error"},
                                "timeout": 180000,
                            },
                        ],
                    }
                ],
            }
        )
    )

    issues = _mvp_config_issues(tmp_path)

    assert not [issue for issue in issues if issue.code == "invalid_smoke_step"]


def test_quality_gate_rejects_unknown_nested_smoke_action(tmp_path):
    (tmp_path / "mvp.config.json").write_text(
        json.dumps(
            {
                "version": 2,
                "runtime": {"type": "docker_compose", "compose_file": "docker-compose.yml"},
                "readiness": [{"name": "frontend", "url": "http://localhost:3000", "expect_status": 200}],
                "targets": {
                    "done": {"selector": "[data-testid='done']"},
                    "error": {"selector": "[data-testid='error']"},
                },
                "flows": [
                    {
                        "id": "primary_happy_path",
                        "steps": [
                            {"action": "drag_magic", "target": "done"},
                            {
                                "action": "wait_for_outcome",
                                "success": {"target": "done"},
                                "failure": {"target": "error"},
                            },
                        ],
                    }
                ],
            }
        )
    )

    issues = _mvp_config_issues(tmp_path)

    assert any(issue.code == "invalid_smoke_step" for issue in issues)
    assert any("drag_magic" in issue.description for issue in issues)


def test_ci_smoke_runner_reports_nested_step_path():
    ci_template = (Path(__file__).resolve().parents[1] / "templates" / "ci.yml.j2").read_text()

    assert "legacy smoke_flow/primary_entity contracts are not accepted by CI" in ci_template
    assert "primary_entity.create_flow[{nested_index}]" not in ci_template
    assert 'elif action == "upload_file"' in ci_template
    assert 'elif action == "wait_for_text"' not in ci_template
    assert 'elif action == "expect_download"' in ci_template
    assert "MVP_CONTRACT_VERSION=2" in ci_template
    assert "expect_request" in ci_template
    assert "click_with_expect_request" in ci_template
    assert 'elif action == "wait_for_outcome"' in ci_template
    assert "payload_json" in ci_template
    assert "browser smoke failure diagnostics" in ci_template
    assert "current_url" in ci_template
    assert "localStorage" in ci_template
    assert "responses_4xx_5xx" in ci_template
    assert "smoke-failure.png" in ci_template
    assert "actions/upload-artifact@v4" in ci_template
