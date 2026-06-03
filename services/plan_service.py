from utils.logger import get_logger
from utils.prompts import load_prompt
from services.openclaw_client import OpenClawClient

logger = get_logger(__name__)


class PlanService:
    def __init__(self, client: OpenClawClient):
        self._client = client

    async def generate(
        self,
        spec_text: str,
        *,
        feedback: str | None = None,
        previous_plan: str | None = None,
    ) -> str:
        system_prompt = load_prompt("plan_system")
        logger.info("plan_service.generating", spec_len=len(spec_text))
        user_message = spec_text
        if feedback:
            user_message = (
                "# Technical Specification\n\n"
                f"{spec_text}\n\n"
                "# Previous Technical Plan\n\n"
                f"{previous_plan or '<none>'}\n\n"
                "# Human Revision Feedback\n\n"
                f"{feedback}\n\n"
                "Revise the technical plan to address the human feedback. Keep the same output structure, "
                "including an authoritative Backend API Contract."
            )
        result = await self._client.chat(system_prompt, user_message)
        logger.info("plan_service.generated", result_len=len(result))
        return result
