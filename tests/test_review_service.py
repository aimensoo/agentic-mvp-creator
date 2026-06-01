import pytest
from unittest.mock import AsyncMock
from services.review_service import ReviewService, ReviewResult, ReviewIssue


@pytest.fixture
def mock_client():
    return AsyncMock()


@pytest.fixture
def service(mock_client):
    return ReviewService(mock_client)


async def test_review_approved(service, mock_client):
    mock_client.chat = AsyncMock(
        return_value=(
            '{"approved": true, "summary": "LGTM", "issues": [], '
            '"missing_from_spec": [], "extra_not_in_spec": [], "tests_present": true}'
        )
    )

    result = await service.run(spec_text="spec", pr_diff="diff content")

    assert isinstance(result, ReviewResult)
    assert result.approved is True
    assert result.summary == "LGTM"
    assert result.issues == []
    assert result.tests_present is True


async def test_review_with_issues(service, mock_client):
    mock_client.chat = AsyncMock(
        return_value=(
            '{"approved": false, "summary": "Problems found", '
            '"issues": [{"severity": "critical", "description": "No error handling"}], '
            '"missing_from_spec": ["auth"], "extra_not_in_spec": [], "tests_present": false}'
        )
    )

    result = await service.run(spec_text="spec", pr_diff="diff")

    assert result.approved is False
    assert len(result.issues) == 1
    assert result.issues[0].severity == "critical"
    assert result.issues[0].description == "No error handling"
    assert "auth" in result.missing_from_spec
    assert result.tests_present is False


async def test_review_parses_json_in_markdown_code_block(service, mock_client):
    mock_client.chat = AsyncMock(
        return_value=(
            "```json\n"
            '{"approved": true, "summary": "ok", "issues": [], '
            '"missing_from_spec": [], "extra_not_in_spec": [], "tests_present": true}\n'
            "```"
        )
    )

    result = await service.run(spec_text="spec", pr_diff="diff")

    assert result.approved is True


async def test_review_invalid_json_returns_not_approved(service, mock_client):
    mock_client.chat = AsyncMock(return_value="This is not JSON at all")

    result = await service.run(spec_text="spec", pr_diff="diff")

    assert result.approved is False
    assert "parse" in result.summary.lower() or "JSON" in result.summary


async def test_review_passes_spec_and_diff_in_user_message(service, mock_client):
    mock_client.chat = AsyncMock(
        return_value=(
            '{"approved": true, "summary": "ok", "issues": [], '
            '"missing_from_spec": [], "extra_not_in_spec": [], "tests_present": true}'
        )
    )

    await service.run(spec_text="my spec text", pr_diff="my diff content")

    call_args = mock_client.chat.call_args
    system_prompt = call_args[0][0]
    user_message = call_args[0][1]
    assert "Code Reviewer" in system_prompt
    assert "Honest MVP behavior" in system_prompt
    assert "visible buttons" in system_prompt
    assert "Do NOT reject lock files" in system_prompt
    assert "Docker runtime correctness" in system_prompt
    assert "invented or unpublished image tags" in system_prompt
    assert "without a committed safe `.env.example`" in system_prompt
    assert "Prisma-backed Node service uses Alpine" in system_prompt
    assert "frontend-backend integration" in system_prompt
    assert "Frontend product design quality" in system_prompt
    assert "raw/default browser HTML" in system_prompt
    assert "overlapping rows" in system_prompt
    assert "data-testid" in system_prompt
    assert "expect_request" in system_prompt
    assert "wait_for_outcome" in system_prompt
    assert "External API secret safety" in system_prompt
    assert "mock/demo/local provider" in system_prompt
    assert "my spec text" in user_message
    assert "my diff content" in user_message


async def test_review_result_defaults():
    issue = ReviewIssue(severity="minor", description="typo")
    result = ReviewResult(
        approved=True,
        summary="ok",
        issues=[issue],
        missing_from_spec=[],
        extra_not_in_spec=[],
        tests_present=True,
    )
    assert result.issues[0].severity == "minor"
