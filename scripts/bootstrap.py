from __future__ import annotations

import asyncio
import json

from app.services import get_services


async def main() -> None:
    services = get_services()
    await services.vector_store.initialize()
    checks = {
        "pinecone": await services.vector_store.health_check(),
        "openai": await services.embedder.health_check(),
        "redis": await services.cache.health_check(),
    }
    print(json.dumps(checks, indent=2))
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
