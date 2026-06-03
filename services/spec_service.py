from utils.logger import get_logger
from utils.prompts import load_prompt
from services.openclaw_client import OpenClawClient

logger = get_logger(__name__)


class SpecService:
    def __init__(self, client: OpenClawClient):
        self._client = client

    async def generate(self, request_text: str) -> str:
        system_prompt = load_prompt("spec_system")
        logger.info("spec_service.generating", request_len=len(request_text))
        result = await self._client.chat(system_prompt, request_text)
        logger.info("spec_service.generated", result_len=len(result))
        return result
