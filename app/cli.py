"""Administrative commands that must work without an API key.

Bootstraps the first key, and recovers access if every key was revoked:

    docker compose exec api python -m app.cli create-api-key --name admin
    docker compose exec api python -m app.cli list-api-keys
"""

import argparse
import asyncio
import sys

from app.core.database import SessionLocal, engine
from app.repositories.api_key_repository import ApiKeyRepository
from app.services.api_key_service import ApiKeyService


async def _create_api_key(name: str) -> int:
    async with SessionLocal() as session:
        api_key, raw = await ApiKeyService(ApiKeyRepository(session)).create(name)
        await session.commit()
    print(f"Created API key '{api_key.name}' ({api_key.prefix}..., id {api_key.id})")
    print("Store it now; it cannot be shown again:")
    print(raw)
    return 0


async def _list_api_keys() -> int:
    async with SessionLocal() as session:
        keys = await ApiKeyService(ApiKeyRepository(session)).list()
    if not keys:
        print("No API keys. Create one with: python -m app.cli create-api-key")
    for key in keys:
        state = f"revoked {key.revoked_at:%Y-%m-%d}" if key.revoked_at else "active"
        print(f"{key.prefix}...  {key.name:<24} {state:<18} {key.id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create-api-key", help="Create an API key")
    create.add_argument("--name", required=True, help="What the key is for")
    commands.add_parser("list-api-keys", help="List API keys (never the keys)")

    args = parser.parse_args(argv)

    async def run() -> int:
        try:
            if args.command == "create-api-key":
                return await _create_api_key(args.name)
            return await _list_api_keys()
        finally:
            await engine.dispose()

    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
