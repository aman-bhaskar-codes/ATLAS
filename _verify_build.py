import asyncio
import sys

sys.path.insert(0, "src")

from atlas.app import build


async def main() -> None:
    atlas = await build()
    print("Build OK")
    await atlas.start()
    print("Start OK")
    await atlas.close()
    print("Close OK")


asyncio.run(main())
