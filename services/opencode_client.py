import json
import time

import httpx

from utils.logger import get_logger

logger = get_logger(__name__)


class OpenCodeClient:
    def __init__(
        self,
        base_url: str,
        password: str = "",
        timeout: int = 600,
    ):
        self._client = httpx.AsyncClient(
            base_url=base_url,
            auth=("opencode", password),
            timeout=timeout,
        )

    async def health(self) -> dict:
        response = await self._client.get("/global/health")
        return response.json()

    async def create_session(self, title: str, workspace_path: str | None = None) -> str:
        payload = {"title": title}
        params = {}
        if workspace_path:
            params["directory"] = str(workspace_path)

        response = await self._client.post("/session", params=params or None, json=payload)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if not workspace_path or status_code not in {400, 422}:
                raise

            fallback_payload = {"title": title, "directory": str(workspace_path)}
            response = await self._client.post("/session", json=fallback_payload)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as fallback_exc:
                fallback_status = fallback_exc.response.status_code if fallback_exc.response is not None else None
                if fallback_status not in {400, 422}:
                    raise

                legacy_payload = {"title": title, "path": str(workspace_path)}
                response = await self._client.post("/session", json=legacy_payload)
                response.raise_for_status()

        data = response.json()
        if isinstance(data, dict) and "data" in data:
            session_id = data["data"]["id"]
        else:
            session_id = data["id"]
        logger.info("opencode.session_created", session_id=session_id, workspace_path=workspace_path)
        return session_id

    async def send_message(self, session_id: str, text: str) -> dict:
        response = await self._client.post(
            f"/session/{session_id}/message",
            json={"parts": [{"type": "text", "text": text}]},
        )
        response.raise_for_status()
        result = response.json()
        logger.info("opencode.message_sent", session_id=session_id)
        return result

    async def send_prompt_async(self, session_id: str, text: str) -> None:
        response = await self._client.post(
            f"/session/{session_id}/prompt_async",
            json={"parts": [{"type": "text", "text": text}]},
        )
        response.raise_for_status()
        logger.info("opencode.prompt_async_sent", session_id=session_id)

    async def get_session_statuses(self) -> dict:
        response = await self._client.get("/session/status")
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and "data" in data:
            data = data["data"]
        return data if isinstance(data, dict) else {}

    async def is_session_busy(self, session_id: str) -> bool:
        statuses = await self.get_session_statuses()
        status = statuses.get(session_id)
        if status is None:
            return False
        return _is_busy_status(status)

    async def get_diff(self, session_id: str) -> list:
        response = await self._client.get(f"/session/{session_id}/diff")
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict):
            return data.get("data", [])
        return data

    async def get_session_messages(self, session_id: str) -> list:
        response = await self._client.get(f"/session/{session_id}/message")
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and "data" in data:
            data = data["data"]
        if isinstance(data, dict):
            if isinstance(data.get("messages"), list):
                return data["messages"]
            if isinstance(data.get("items"), list):
                return data["items"]
            return [data]
        return data if isinstance(data, list) else []

    async def read_events(self, timeout: float = 0.2, max_events: int = 25) -> list[dict]:
        events: list[dict] = []
        parser = _SseParser()
        request_timeout = httpx.Timeout(timeout, connect=timeout, read=timeout, write=timeout, pool=timeout)
        deadline = time.monotonic() + timeout
        try:
            async with self._client.stream("GET", "/event", timeout=request_timeout) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if time.monotonic() >= deadline:
                        break
                    event = parser.feed(line)
                    if event is not None:
                        events.append(event)
                        if len(events) >= max_events:
                            break
        except httpx.ReadTimeout:
            pass

        final_event = parser.close()
        if final_event is not None and len(events) < max_events:
            events.append(final_event)
        return events

    async def reply_permission(
        self,
        session_id: str,
        permission_id: str,
        response: str,
        remember: bool | None = None,
    ) -> bool:
        payload = {"response": response}
        if remember is not None:
            payload["remember"] = remember
        http_response = await self._client.post(
            f"/session/{session_id}/permissions/{permission_id}",
            json=payload,
        )
        http_response.raise_for_status()
        try:
            data = http_response.json()
        except ValueError:
            return True
        if isinstance(data, dict) and "data" in data:
            return bool(data["data"])
        return bool(data)

    async def abort_session(self, session_id: str) -> None:
        response = await self._client.post(f"/session/{session_id}/abort")
        response.raise_for_status()
        logger.info("opencode.session_aborted", session_id=session_id)

    async def delete_session(self, session_id: str) -> None:
        response = await self._client.delete(f"/session/{session_id}")
        response.raise_for_status()
        logger.info("opencode.session_deleted", session_id=session_id)

    async def close(self) -> None:
        await self._client.aclose()
        logger.info("opencode.client_closed")


def _is_busy_status(status) -> bool:
    if isinstance(status, bool):
        return status

    if isinstance(status, str):
        normalized = status.lower()
        if normalized in {"busy", "running", "active", "pending", "working"}:
            return True
        if normalized in {"idle", "done", "complete", "completed", "finished"}:
            return False
        return True

    if isinstance(status, dict):
        for key in ("status", "state", "type"):
            if key in status:
                return _is_busy_status(status[key])

        for key in ("busy", "running", "active", "pending", "working"):
            if key in status:
                return bool(status[key])

        for key in ("idle", "done", "complete", "completed", "finished"):
            if key in status:
                return not bool(status[key])

        return True

    return True


class _SseParser:
    def __init__(self):
        self._event_type = ""
        self._data_lines: list[str] = []

    def feed(self, line: str) -> dict | None:
        if line == "":
            return self._flush()
        if line.startswith(":"):
            return None
        if line.startswith("event:"):
            self._event_type = line.removeprefix("event:").strip()
            return None
        if line.startswith("data:"):
            self._data_lines.append(line.removeprefix("data:").strip())
            return None
        return None

    def close(self) -> dict | None:
        return self._flush()

    def _flush(self) -> dict | None:
        if not self._event_type and not self._data_lines:
            return None

        raw_data = "\n".join(self._data_lines)
        data = raw_data
        if raw_data:
            try:
                data = json.loads(raw_data)
            except json.JSONDecodeError:
                data = raw_data

        event = {"event": self._event_type, "data": data}
        self._event_type = ""
        self._data_lines = []
        return event
