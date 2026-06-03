import pytest
from services.coding_service import FIX_PROMPT_TEMPLATE
from utils.prompts import load_prompt, PROMPTS_DIR


def test_load_prompt_returns_content():
    result = load_prompt("spec_system")
    assert "# Spec Generator" in result
    assert len(result) > 100


def test_load_prompt_caches_result():
    result1 = load_prompt("plan_system")
    result2 = load_prompt("plan_system")
    assert result1 is result2


def test_load_prompt_not_found():
    with pytest.raises(FileNotFoundError):
        load_prompt("nonexistent_prompt")


def test_prompts_dir_exists():
    assert PROMPTS_DIR.exists()


def test_plan_prompt_requires_docker_and_relevant_tests():
    result = load_prompt("plan_system")

    assert "Runtime & Docker" in result
    assert "Dockerfile for every service" in result
    assert "README/runbook requirements" in result
    assert "short plain-language project overview" in result
    assert ".pipeline/docker-ci.sh" in result
    assert "real `.env` must not be committed" in result
    assert "Frontend tests" in result
    assert "Docker runtime tests" in result


def test_spec_prompt_has_runtime_docker_section():
    result = load_prompt("spec_system")

    assert "Runtime / Docker Requirements" in result
    assert "Docker Compose" in result
    assert "real `.env` files must not be committed" in result


def test_prompts_require_frontend_ui_quality_bar():
    spec = load_prompt("spec_system")
    plan = load_prompt("plan_system")

    assert "Frontend UX/UI Requirements" in spec
    assert "no browser-default raw HTML" in spec
    assert "Frontend UX/UI Architecture" in plan
    assert "no raw browser-default HTML" in plan
    assert "overlapping text" in plan
    assert "responsive layout" in plan


def test_prompts_require_mvp_config_v2_contract():
    spec = load_prompt("spec_system")
    plan = load_prompt("plan_system")

    assert "contract v2" in spec
    assert "data-testid" in spec
    assert "expect_request" in spec
    assert "wait_for_outcome" in spec
    assert "contract v2" in plan
    assert "data-testid" in plan
    assert "expect_request" in plan
    assert "wait_for_outcome" in plan


def test_plan_prompt_makes_openclaw_own_backend_api_contract():
    plan = load_prompt("plan_system")

    assert "Backend API Contract" in plan
    assert "authoritative backend contract" in plan
    assert "OpenClaw planning step" in plan
    assert "OpenCode does not need to invent routes" in plan
    assert "should not prewrite final `data-testid` target names" in plan
    assert "OpenCode will choose concrete targets/flows" in plan


def test_prompts_require_external_api_mock_defaults():
    spec = load_prompt("spec_system")
    plan = load_prompt("plan_system")

    assert "external API" in spec
    assert "mock/demo/local provider" in spec
    assert "without real secrets" in spec
    assert "external API integrations" in plan
    assert "mock/demo/local provider" in plan
    assert "Real provider code" in plan


def test_fix_prompt_allows_foundational_runtime_files_for_foundational_issues():
    assert "empty_mvp_workspace" in FIX_PROMPT_TEMPLATE
    assert "missing_mvp_config" in FIX_PROMPT_TEMPLATE
    assert "frontend/backend files" in FIX_PROMPT_TEMPLATE
    assert 'runtime.type: "docker_compose"' in FIX_PROMPT_TEMPLATE
    assert "non-Docker runtime" in FIX_PROMPT_TEMPLATE
    assert "If the dev context file is missing" in FIX_PROMPT_TEMPLATE
    assert "test -f mvp.config.json" in FIX_PROMPT_TEMPLATE
    assert "test -f README.md" in FIX_PROMPT_TEMPLATE
