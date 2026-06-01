import pytest
from unittest.mock import AsyncMock, MagicMock

from services.spec_service import SpecService


@pytest.fixture
def mock_client():
    return AsyncMock()


@pytest.fixture
def service(mock_client):
    return SpecService(mock_client)


async def test_generate_returns_spec_text(service, mock_client):
    mock_client.chat = AsyncMock(return_value="# Spec\n\n## Purpose\nSome purpose")

    result = await service.generate("Build a CRM dashboard")

    assert "Spec" in result
    assert "Purpose" in result


async def test_generate_passes_correct_prompts(service, mock_client):
    mock_client.chat = AsyncMock(return_value="spec result")

    await service.generate("Build a CRM dashboard")

    mock_client.chat.assert_called_once()
    call_args = mock_client.chat.call_args
    system_prompt = call_args[0][0]
    user_message = call_args[0][1]
    assert "Spec Generator" in system_prompt
    assert user_message == "Build a CRM dashboard"
