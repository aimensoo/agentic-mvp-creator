import asyncio

import httpx

from utils.logger import get_logger

logger = get_logger(__name__)


class OpenClawClient:
    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        model: str = "openclaw/default",
        timeout: int = 120,
    ):
        self._model = model
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=timeout,
        )

    async def chat(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.7,
    ) -> str:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": temperature,
        }

        try:
            response = await self._client.post("/chat/completions", json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                logger.warning("openclaw.rate_limited")
                await asyncio.sleep(10)
                response = await self._client.post("/chat/completions", json=payload)
                response.raise_for_status()
            else:
                raise RuntimeError(f"OpenClaw HTTP error: {e}") from e
        except httpx.ConnectError as e:
            raise RuntimeError(f"OpenClaw connection error: {e}") from e
        except httpx.TimeoutException as e:
            raise RuntimeError(f"OpenClaw timeout: {e}") from e

        data = response.json()
        return data["choices"][0]["message"]["content"]

    async def close(self) -> None:
        await self._client.aclose()
        logger.info("openclaw.client_closed")
