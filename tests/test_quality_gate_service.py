import git

from services.quality_gate_service import QualityGateService


def _write_docker_contract(path):
    (path / "README.md").write_text(
        """
        # Task Tracker MVP

        Task Tracker MVP is a small web app for creating and reviewing demo tasks.
        It includes a frontend, backend, and Docker Compose runtime so reviewers can
        start the product from a clean checkout.

        ## Run

        ```bash
        cp .env.example .env  # if the project includes .env.example
        docker compose -f docker-compose.yml config
        docker compose -f docker-compose.yml build
        docker compose -f docker-compose.yml up -d
        ```

        Open http://localhost:5174 after the containers are ready.
        """
    )
    (path / "Dockerfile").write_text(
        """
        FROM node:20-bookworm-slim
        WORKDIR /app
        COPY . .
        EXPOSE 5174
        CMD ["npm", "run", "dev"]
        """
    )
    (path / ".dockerignore").write_text(
        """
        node_modules
        dist
        build
        .env
        .env.*
        *.log
        """
    )
    (path / "docker-compose.yml").write_text(
        """
        services:
          app:
            build:
              context: .
              dockerfile: Dockerfile
            ports:
              - "5174:5174"
        """
    )
    (path / ".pipeline").mkdir(exist_ok=True)
    (path / ".pipeline" / "docker-ci.sh").write_text(
        """
        #!/usr/bin/env bash
        set -euo pipefail
        docker compose -f docker-compose.yml config
        docker compose -f docker-compose.yml build
        docker compose -f docker-compose.yml up -d
        docker compose -f docker-compose.yml ps
        """
    )


def _write_mvp_config(path):
    _write_mvp_config_v2(path)


def _write_mvp_config_v2(path):
    _write_docker_contract(path)
    (path / "src").mkdir(exist_ok=True)
    (path / "src" / "App.tsx").write_text(
        "export function App() { return <button data-testid='save-task'>Save</button>; }\n"
    )
    (path / "mvp.config.json").write_text(
        """
        {
          "version": 2,
          "runtime": {
            "type": "docker_compose",
            "compose_file": "docker-compose.yml"
          },
          "readiness": [
            {"name": "frontend", "url": "http://localhost:5174", "expect_status": 200}
          ],
          "targets": {
            "new_task": {"selector": "[data-testid='new-task']"},
            "title": {"selector": "[data-testid='task-title']"},
            "save": {"selector": "[data-testid='save-task']"},
            "task_row": {"selector": "[data-testid='task-row-smoke-task']"},
            "error_message": {"selector": "[data-testid='error-message']"}
          },
          "flows": [
            {
              "id": "primary_happy_path",
              "steps": [
                {"action": "goto", "path": "/tasks"},
                {"action": "expect_styled"},
                {"action": "click", "target": "new_task"},
                {"action": "fill", "target": "title", "value": "Smoke task"},
                {
                  "action": "click",
                  "target": "save",
                  "expect_request": {
                    "method": "POST",
                    "url": "/api/tasks",
                    "status": [200, 201, 202]
                  }
                },
                {
                  "action": "wait_for_outcome",
                  "success": {"target": "task_row"},
                  "failure": {"target": "error_message"},
                  "timeout": 180000
                }
              ]
            }
          ]
        }
        """
    )


def test_quality_gate_adds_root_gitignore_and_passes_clean_mvp(tmp_path):
    _write_mvp_config(tmp_path)
    (tmp_path / "src" / "App.tsx").write_text("export function App() { return <button>Open dashboard</button>; }\n")

    result = QualityGateService().run(tmp_path)

    assert result.passed is True
    gitignore = (tmp_path / ".gitignore").read_text()
    assert "node_modules/" in gitignore
    assert "dist/" in gitignore
    assert ".env" in gitignore
    assert (tmp_path / "MVP_REPORT.md").exists()


def test_quality_gate_accepts_v2_target_flow_contract(tmp_path):
    _write_mvp_config_v2(tmp_path)

    result = QualityGateService().run(tmp_path)

    assert result.passed is True
    assert not [issue for issue in result.issues if issue.code in {"invalid_mvp_config", "invalid_smoke_step"}]


def test_quality_gate_rejects_workspace_without_implementation_source(tmp_path):
    _write_mvp_config_v2(tmp_path)
    (tmp_path / "src" / "App.tsx").unlink()

    result = QualityGateService().run(tmp_path)

    assert result.passed is False
    assert any(issue.code == "empty_mvp_workspace" for issue in result.issues)


def test_quality_gate_requires_project_docker_ci_script(tmp_path):
    _write_mvp_config_v2(tmp_path)
    (tmp_path / ".pipeline" / "docker-ci.sh").unlink()

    result = QualityGateService().run(tmp_path)

    assert result.passed is False
    assert any(issue.code == "missing_docker_ci_script" for issue in result.issues)


def test_quality_gate_requires_readme_with_run_commands(tmp_path):
    _write_mvp_config_v2(tmp_path)
    (tmp_path / "README.md").unlink()

    result = QualityGateService().run(tmp_path)

    assert result.passed is False
    readme_issue = next(issue for issue in result.issues if issue.code == "missing_readme")
    assert "Create root README.md." in readme_issue.acceptance_checks


def test_quality_gate_missing_mvp_config_includes_acceptance_checks(tmp_path):
    _write_mvp_config_v2(tmp_path)
    (tmp_path / "mvp.config.json").unlink()

    result = QualityGateService().run(tmp_path)

    assert result.passed is False
    config_issue = next(issue for issue in result.issues if issue.code == "missing_mvp_config")
    assert "Create root mvp.config.json." in config_issue.acceptance_checks
    assert any("runtime.type" in check for check in config_issue.acceptance_checks)


def test_quality_gate_rejects_readme_without_start_commands(tmp_path):
    _write_mvp_config_v2(tmp_path)
    (tmp_path / "README.md").write_text("# Task Tracker MVP\n\nA tiny app for task tracking.\n")

    result = QualityGateService().run(tmp_path)

    assert result.passed is False
    assert any(
        issue.code == "invalid_readme" and "Docker Compose startup command" in issue.description
        for issue in result.issues
    )


def test_quality_gate_rejects_weak_project_docker_ci_script(tmp_path):
    _write_mvp_config_v2(tmp_path)
    (tmp_path / ".pipeline" / "docker-ci.sh").write_text("#!/usr/bin/env bash\nexit 0\n")

    result = QualityGateService().run(tmp_path)

    assert result.passed is False
    assert any(issue.code == "invalid_docker_ci_script" for issue in result.issues)


def test_quality_gate_rejects_v2_unknown_target_and_missing_outcome(tmp_path):
    _write_mvp_config_v2(tmp_path)
    (tmp_path / "mvp.config.json").write_text(
        """
        {
          "version": 2,
          "runtime": {"type": "docker_compose", "compose_file": "docker-compose.yml"},
          "readiness": [{"name": "frontend", "url": "http://localhost:5174", "expect_status": 200}],
          "targets": {
            "save": {"selector": "[data-testid='save-task']"}
          },
          "flows": [
            {
              "id": "primary_happy_path",
              "steps": [
                {
                  "action": "click",
                  "target": "missing",
                  "expect_request": {"method": "POST", "url": "/api/tasks", "status": [201]}
                }
              ]
            }
          ]
        }
        """
    )

    result = QualityGateService().run(tmp_path)

    assert result.passed is False
    assert any(issue.code == "invalid_smoke_step" and "missing" in issue.description for issue in result.issues)
    assert any(issue.code == "weak_smoke_flow" and "wait_for_outcome" in issue.description for issue in result.issues)


def test_quality_gate_rejects_v2_non_data_testid_target_and_wait_for_text(tmp_path):
    _write_mvp_config_v2(tmp_path)
    (tmp_path / "mvp.config.json").write_text(
        """
        {
          "version": 2,
          "runtime": {"type": "docker_compose", "compose_file": "docker-compose.yml"},
          "readiness": [{"name": "frontend", "url": "http://localhost:5174", "expect_status": 200}],
          "targets": {
            "save": {"selector": "button.save"},
            "done": {"selector": "[data-testid='done']"},
            "error": {"selector": "[data-testid='error']"}
          },
          "flows": [
            {
              "id": "primary_happy_path",
              "steps": [
                {"action": "wait_for_text", "text": "Generating"},
                {
                  "action": "click",
                  "target": "save",
                  "expect_request": {"method": "POST", "url": "/api/tasks", "status": [201]}
                },
                {
                  "action": "wait_for_outcome",
                  "success": {"target": "done"},
                  "failure": {"target": "error"}
                }
              ]
            }
          ]
        }
        """
    )

    result = QualityGateService().run(tmp_path)

    assert result.passed is False
    assert any(issue.code == "unstable_smoke_target" for issue in result.issues)
    assert any(issue.code == "invalid_smoke_step" and "wait_for_text" in issue.description for issue in result.issues)


def test_quality_gate_rejects_v2_expect_request_on_non_click_step(tmp_path):
    _write_mvp_config_v2(tmp_path)
    (tmp_path / "mvp.config.json").write_text(
        """
        {
          "version": 2,
          "runtime": {"type": "docker_compose", "compose_file": "docker-compose.yml"},
          "readiness": [{"name": "frontend", "url": "http://localhost:5174", "expect_status": 200}],
          "targets": {
            "done": {"selector": "[data-testid='done']"},
            "error": {"selector": "[data-testid='error']"}
          },
          "flows": [
            {
              "id": "primary_happy_path",
              "steps": [
                {
                  "action": "goto",
                  "path": "/tasks",
                  "expect_request": {"method": "POST", "url": "/api/tasks", "status": [201]}
                },
                {
                  "action": "wait_for_outcome",
                  "success": {"target": "done"},
                  "failure": {"target": "error"}
                }
              ]
            }
          ]
        }
        """
    )

    result = QualityGateService().run(tmp_path)

    assert result.passed is False
    assert any("only supported on click" in issue.description for issue in result.issues)


def test_quality_gate_restores_managed_ci_workflow(tmp_path):
    _write_mvp_config(tmp_path)
    (tmp_path / "src" / "App.tsx").write_text("export function App() { return <button>Open dashboard</button>; }\n")
    workflow = tmp_path / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: fake\njobs: {}\n")

    result = QualityGateService().run(tmp_path)

    assert result.passed is True
    workflow_text = workflow.read_text()
    assert "name: CI" in workflow_text
    assert "name: fake" not in workflow_text
    assert not [issue for issue in result.issues if issue.code.startswith("managed_ci")]


def test_quality_gate_detects_placeholder_and_fake_ui_actions(tmp_path):
    _write_mvp_config(tmp_path)
    src = tmp_path / "frontend" / "src"
    src.mkdir(parents=True)
    (src / "TasksPage.tsx").write_text(
        """
        import { Button, message } from 'antd';

        export function TasksPage() {
          return (
            <>
              <div>Create client form (placeholder)</div>
              <Button onClick={() => {}}>Delete</Button>
              <Button type="primary">New proposal</Button>
              <Button onClick={() => {
                message.success('Task completed');
              }}>Complete</Button>
              <Modal title="New task" onOk={() => setCreateModal(false)} />
            </>
          );
        }
        """
    )

    result = QualityGateService().run(tmp_path)

    assert result.passed is False
    codes = {issue.code for issue in result.issues}
    assert "placeholder_code" in codes
    assert "empty_ui_handler" in codes
    assert "unwired_mutating_button" in codes
    assert "fake_success_action" in codes
    assert "state_only_modal_ok" in codes
    assert "TasksPage.tsx" in result.output


def test_quality_gate_allows_success_after_real_mutation(tmp_path):
    _write_mvp_config(tmp_path)
    src = tmp_path / "frontend" / "src"
    src.mkdir(parents=True)
    (src / "TasksPage.tsx").write_text(
        """
        export function TasksPage() {
          return <Button onClick={async () => {
            await api.patch('/tasks/1/complete');
            message.success('Task completed');
          }}>Complete</Button>;
        }
        """
    )

    result = QualityGateService().run(tmp_path)

    assert result.passed is True
    assert not [issue for issue in result.issues if issue.code == "fake_success_action"]


def test_quality_gate_untracks_dependency_artifacts_without_deleting_files(tmp_path):
    _write_mvp_config(tmp_path)
    repo = git.Repo.init(tmp_path)
    node_module = tmp_path / "backend" / "node_modules" / "pkg"
    node_module.mkdir(parents=True)
    (node_module / "index.js").write_text("module.exports = {};\n")
    repo.git.add("-f", "backend/node_modules/pkg/index.js")

    result = QualityGateService().run(tmp_path)

    assert result.passed is True
    assert (node_module / "index.js").exists()
    assert "backend/node_modules/pkg/index.js" not in repo.git.ls_files()


def test_quality_gate_stats_exclude_dependencies_and_build_outputs(tmp_path):
    _write_mvp_config(tmp_path)
    (tmp_path / "backend" / "src").mkdir(parents=True)
    (tmp_path / "backend" / "src" / "main.ts").write_text("line1\nline2\n")
    (tmp_path / "backend" / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "backend" / "node_modules" / "pkg" / "index.js").write_text("ignored\n")
    (tmp_path / "backend" / "dist").mkdir()
    (tmp_path / "backend" / "dist" / "main.js").write_text("ignored\n")

    result = QualityGateService().run(tmp_path)

    assert result.own_code_files >= 1
    assert result.own_code_lines >= 2
    assert result.own_code_lines < 60


def test_quality_gate_requires_mvp_config(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.tsx").write_text("export function App() { return <div />; }\n")

    result = QualityGateService().run(tmp_path)

    assert result.passed is False
    assert any(issue.code == "missing_mvp_config" for issue in result.issues)


def test_quality_gate_rejects_legacy_mvp_config(tmp_path):
    (tmp_path / "mvp.config.json").write_text(
        '{"app_url":"http://localhost:5174","start_command":"docker compose up -d","smoke_flow":["open_app","login"]}'
    )

    result = QualityGateService().run(tmp_path)

    assert result.passed is False
    assert any(issue.code == "invalid_mvp_config" and "contract v2" in issue.description for issue in result.issues)


def test_quality_gate_rejects_blocking_docker_compose_start_command(tmp_path):
    _write_docker_contract(tmp_path)
    (tmp_path / "mvp.config.json").write_text(
        """
        {
          "version": 2,
          "runtime": {
            "type": "docker_compose",
            "compose_file": "docker-compose.yml",
            "start_command": "docker compose up"
          },
          "readiness": [{"name": "frontend", "url": "http://localhost:5174", "expect_status": 200}],
          "targets": {
            "save": {"selector": "[data-testid='save-task']"},
            "done": {"selector": "[data-testid='done']"},
            "error": {"selector": "[data-testid='error']"}
          },
          "flows": [
            {
              "id": "primary_happy_path",
              "steps": [
                {
                  "action": "click",
                  "target": "save",
                  "expect_request": {"method": "POST", "url": "/api/tasks", "status": [201]}
                },
                {
                  "action": "wait_for_outcome",
                  "success": {"target": "done"},
                  "failure": {"target": "error"}
                }
              ]
            }
          ]
        }
        """
    )

    result = QualityGateService().run(tmp_path)

    assert result.passed is False
    assert "non-blocking" in result.output


def test_quality_gate_rejects_v2_flow_without_network_and_outcome(tmp_path):
    _write_docker_contract(tmp_path)
    (tmp_path / "mvp.config.json").write_text(
        """
        {
          "version": 2,
          "runtime": {"type": "docker_compose", "compose_file": "docker-compose.yml"},
          "readiness": [{"name": "frontend", "url": "http://localhost:5174", "expect_status": 200}],
          "targets": {
            "save": {"selector": "[data-testid='save-task']"}
          },
          "flows": [
            {
              "id": "primary_happy_path",
              "steps": [
                {"action": "click", "target": "save"}
              ]
            }
          ]
        }
        """
    )

    result = QualityGateService().run(tmp_path)

    assert result.passed is False
    assert any(issue.code == "weak_smoke_flow" for issue in result.issues)


def test_quality_gate_rejects_broken_tailwind_v4_setup(tmp_path):
    _write_mvp_config(tmp_path)
    client = tmp_path / "client"
    (client / "src").mkdir(parents=True)
    (client / "package.json").write_text(
        """
        {
          "dependencies": {
            "react": "^19.0.0",
            "react-dom": "^19.0.0",
            "tailwindcss": "^4.2.4"
          },
          "devDependencies": {
            "vite": "^8.0.0",
            "@vitejs/plugin-react": "^6.0.0"
          }
        }
        """
    )
    (client / "src" / "main.tsx").write_text("import './index.css';\n")
    (client / "src" / "index.css").write_text("@tailwind base;\n@tailwind components;\n@tailwind utilities;\n")
    (client / "src" / "App.tsx").write_text(
        "export function App() { return <button className='bg-blue-600 text-white px-4 py-2 rounded shadow'>Save</button>; }\n"
    )

    result = QualityGateService().run(tmp_path)

    assert result.passed is False
    assert any(issue.code == "broken_tailwind_v4_setup" for issue in result.issues)


def test_quality_gate_requires_compose_file_for_docker_start_command(tmp_path):
    (tmp_path / "mvp.config.json").write_text(
        """
        {
          "version": 2,
          "runtime": {"type": "docker_compose", "compose_file": "docker-compose.yml"},
          "readiness": [{"name": "frontend", "url": "http://localhost:5174", "expect_status": 200}],
          "targets": {
            "save": {"selector": "[data-testid='save-task']"},
            "done": {"selector": "[data-testid='done']"},
            "error": {"selector": "[data-testid='error']"}
          },
          "flows": [
            {
              "id": "primary_happy_path",
              "steps": [
                {
                  "action": "click",
                  "target": "save",
                  "expect_request": {"method": "POST", "url": "/api/tasks", "status": [201]}
                },
                {
                  "action": "wait_for_outcome",
                  "success": {"target": "done"},
                  "failure": {"target": "error"}
                }
              ]
            }
          ]
        }
        """
    )

    result = QualityGateService().run(tmp_path)

    assert result.passed is False
    assert any(issue.code == "missing_docker_compose" for issue in result.issues)


def test_quality_gate_rejects_compose_build_without_dockerfile(tmp_path):
    _write_mvp_config(tmp_path)
    (tmp_path / "Dockerfile").unlink()

    result = QualityGateService().run(tmp_path)

    assert result.passed is False
    assert any(issue.code == "missing_dockerfile" for issue in result.issues)


def test_quality_gate_rejects_compose_port_mismatch(tmp_path):
    _write_mvp_config(tmp_path)
    (tmp_path / "docker-compose.yml").write_text(
        """
        services:
          app:
            build: .
            ports:
              - "9999:5174"
        """
    )

    result = QualityGateService().run(tmp_path)

    assert result.passed is False
    assert any(issue.code == "compose_port_mismatch" for issue in result.issues)


def test_quality_gate_allows_compose_env_file_when_template_exists(tmp_path):
    _write_mvp_config(tmp_path)
    (tmp_path / ".env.example").write_text("DATABASE_URL=postgres://demo:demo@db:5432/demo\n")
    (tmp_path / "docker-compose.yml").write_text(
        """
        services:
          app:
            build: .
            ports:
              - "5174:5174"
            env_file: .env
        """
    )

    result = QualityGateService().run(tmp_path)

    assert result.passed is True
    assert not [issue for issue in result.issues if issue.code == "missing_env_template"]


def test_quality_gate_rejects_external_api_placeholder_secret_without_mock_provider(tmp_path):
    _write_mvp_config(tmp_path)
    (tmp_path / ".env.example").write_text("IMAGE_PROVIDER=replicate\nREPLICATE_API_TOKEN=demo\n")
    src = tmp_path / "backend" / "src"
    src.mkdir(parents=True)
    (src / "image.ts").write_text(
        """
        import Replicate from 'replicate';

        export async function generateImage() {
          const client = new Replicate({ auth: process.env.REPLICATE_API_TOKEN });
          return client.run('owner/model', { input: { prompt: 'paint by numbers' } });
        }
        """
    )

    result = QualityGateService().run(tmp_path)

    assert result.passed is False
    assert any(issue.code == "external_api_requires_mock" for issue in result.issues)


def test_quality_gate_allows_external_api_when_env_defaults_to_mock_provider(tmp_path):
    _write_mvp_config(tmp_path)
    (tmp_path / ".env.example").write_text("IMAGE_PROVIDER=mock\nREPLICATE_API_TOKEN=demo\n")
    src = tmp_path / "backend" / "src"
    src.mkdir(parents=True)
    (src / "image.ts").write_text(
        """
        import Replicate from 'replicate';

        export async function generateImage() {
          if (process.env.IMAGE_PROVIDER === 'mock') {
            return { imageUrl: '/fixtures/generated-demo.png' };
          }
          const client = new Replicate({ auth: process.env.REPLICATE_API_TOKEN });
          return client.run('owner/model', { input: { prompt: 'paint by numbers' } });
        }
        """
    )

    result = QualityGateService().run(tmp_path)

    assert result.passed is True
    assert not [issue for issue in result.issues if issue.code == "external_api_requires_mock"]


def test_quality_gate_requires_env_template_for_compose_env_file(tmp_path):
    _write_mvp_config(tmp_path)
    (tmp_path / "docker-compose.yml").write_text(
        """
        services:
          app:
            build: .
            ports:
              - "5174:5174"
            env_file:
              - .env
        """
    )

    result = QualityGateService().run(tmp_path)

    assert result.passed is False
    assert any(issue.code == "missing_env_template" for issue in result.issues)


def test_quality_gate_rejects_non_standard_local_env_file(tmp_path):
    _write_mvp_config(tmp_path)
    (tmp_path / ".env.example").write_text("DATABASE_URL=postgres://demo:demo@db:5432/demo\n")
    (tmp_path / "docker-compose.yml").write_text(
        """
        services:
          app:
            build: .
            ports:
              - "5174:5174"
            env_file: .env.local
        """
    )

    result = QualityGateService().run(tmp_path)

    assert result.passed is False
    assert any(issue.code == "unsupported_env_file" for issue in result.issues)


def test_quality_gate_rejects_prisma_service_on_alpine_docker_image(tmp_path):
    _write_mvp_config(tmp_path)
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / ".dockerignore").write_text("node_modules\n")
    (backend / "package.json").write_text(
        """
        {
          "dependencies": {
            "@prisma/client": "^5.0.0"
          },
          "devDependencies": {
            "prisma": "^5.0.0"
          }
        }
        """
    )
    (backend / "Dockerfile").write_text(
        """
        FROM node:20-alpine AS builder
        WORKDIR /app
        COPY . .
        RUN npx prisma generate

        FROM node:20-alpine
        WORKDIR /app
        COPY --from=builder /app .
        CMD ["node", "dist/main"]
        """
    )
    (tmp_path / "docker-compose.yml").write_text(
        """
        services:
          app:
            build: ./backend
            ports:
              - "5174:5174"
        """
    )

    result = QualityGateService().run(tmp_path)

    assert result.passed is False
    assert any(issue.code == "prisma_alpine_runtime_risk" for issue in result.issues)


def test_quality_gate_rejects_known_invalid_compose_image_tags(tmp_path):
    _write_mvp_config(tmp_path)
    (tmp_path / "docker-compose.yml").write_text(
        """
        services:
          app:
            build: .
            ports:
              - "5174:5174"
          postgres:
            image: postgres:16-slim
          redis:
            image: "redis:7-slim"
        """
    )

    result = QualityGateService().run(tmp_path)

    assert result.passed is False
    invalid_images = [issue for issue in result.issues if issue.code == "invalid_compose_image_tag"]
    assert len(invalid_images) == 2
    assert "Do not invent Docker image tags" in result.output
