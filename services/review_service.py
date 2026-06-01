import json
import re

from pydantic import BaseModel

from utils.logger import get_logger
from utils.prompts import load_prompt
from services.openclaw_client import OpenClawClient

logger = get_logger(__name__)


class ReviewIssue(BaseModel):
    severity: str
    description: str


class ReviewResult(BaseModel):
    approved: bool
    summary: str
    issues: list[ReviewIssue] = []
    missing_from_spec: list[str] = []
    extra_not_in_spec: list[str] = []
    tests_present: bool = False


class ReviewService:
    def __init__(self, client: OpenClawClient):
        self._client = client

    async def run(self, spec_text: str, pr_diff: str) -> ReviewResult:
        system_prompt = load_prompt("review_system")
        user_message = f"## Specification\n\n{spec_text}\n\n## Code Diff\n\n{pr_diff}"

        logger.info("review_service.running")
        raw = await self._client.chat(system_prompt, user_message)
        logger.info("review_service.raw_received", raw_len=len(raw))

        return self._parse_result(raw)

    def _parse_result(self, raw: str) -> ReviewResult:
        json_str = self._extract_json(raw)
        if json_str is None:
            logger.error("review_service.parse_failed", raw=raw[:200])
            return ReviewResult(
                approved=False,
                summary=f"Failed to parse JSON from review response",
                issues=[],
                tests_present=False,
            )

        try:
            data = json.loads(json_str)
            return ReviewResult(**data)
        except (json.JSONDecodeError, Exception) as e:
            logger.error("review_service.json_decode_error", error=str(e))
            return ReviewResult(
                approved=False,
                summary=f"Invalid JSON in review response: {e}",
                issues=[],
                tests_present=False,
            )

    def _extract_json(self, text: str) -> str | None:
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if match:
            return match.group(1).strip()

        try:
            json.loads(text)
            return text
        except (json.JSONDecodeError, ValueError):
            pass

        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start != -1 and brace_end > brace_start:
            candidate = text[brace_start : brace_end + 1]
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                pass

        return None
