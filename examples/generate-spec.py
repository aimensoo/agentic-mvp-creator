import asyncio

from app.config import settings
from services.openclaw_client import OpenClawClient
from services.spec_service import SpecService


async def main():
    client = OpenClawClient(
        base_url=settings.OPENCLAW_API_URL,
        api_key=settings.OPENCLAW_API_KEY,
        model=settings.OPENCLAW_MODEL,
    )
    spec = SpecService(client)
    request_text = """
    Build a simple CRM dashboard for a small team. Users should create leads,
    update lead statuses, add notes, and see a kanban-style overview.
    """

    result = await spec.generate(request_text)
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
