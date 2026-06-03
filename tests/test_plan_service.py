import pytest
from unittest.mock import AsyncMock

from services.plan_service import PlanService


@pytest.fixture
def mock_client():
    return AsyncMock()


@pytest.fixture
def service(mock_client):
    return PlanService(mock_client)


async def test_generate_returns_plan_text(service, mock_client):
    mock_client.chat = AsyncMock(return_value="# Plan\n\n## Architecture\nMicroservices")

    result = await service.generate("Spec text here")

    assert "Plan" in result
    assert "Architecture" in result


async def test_generate_passes_correct_prompts(service, mock_client):
    mock_client.chat = AsyncMock(return_value="plan result")

    await service.generate("spec text")

    mock_client.chat.assert_called_once()
    call_args = mock_client.chat.call_args
    system_prompt = call_args[0][0]
    user_message = call_args[0][1]
    assert "Plan Generator" in system_prompt
    assert user_message == "spec text"


async def test_generate_revision_includes_previous_plan_and_feedback(service, mock_client):
    mock_client.chat = AsyncMock(return_value="revised plan")

    result = await service.generate(
        "spec text",
        previous_plan="old plan",
        feedback="fix architecture",
    )

    assert result == "revised plan"
    user_message = mock_client.chat.call_args.args[1]
    assert "spec text" in user_message
    assert "old plan" in user_message
    assert "fix architecture" in user_message
    assert "Revise the technical plan" in user_message
