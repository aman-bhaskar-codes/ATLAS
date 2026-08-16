"""Diagnostic: Check which routes are registered when app is created."""

import asyncio

from atlas.interfaces.api.app import create_app


async def main():
    app = create_app()

    print("=== FastAPI App Created ===")
    print(f"Title: {app.title}")
    print(f"Version: {app.version}")

    # Count routes
    all_routes = []
    for route in app.routes:
        if hasattr(route, "path"):
            all_routes.append(route.path)

    print(f"\nTotal routes: {len(all_routes)}")

    # Check for memory routes
    memory_routes = [r for r in all_routes if "memory" in r]
    print(f"\nMemory routes ({len(memory_routes)}):")
    for r in sorted(memory_routes):
        print(f"  {r}")

    # Check for trajectory routes
    trajectory_routes = [r for r in all_routes if "trajectory" in r]
    print(f"\nTrajectory routes ({len(trajectory_routes)}):")
    for r in sorted(trajectory_routes):
        print(f"  {r}")

    # Check for missing routes
    expected_memory = [
        "/api/v1/memory/episodes",
        "/api/v1/memory/facts",
        "/api/v1/memory/knowledge",
        "/api/v1/memory/preferences",
        "/api/v1/memory/stats",
    ]

    missing_memory = [r for r in expected_memory if r not in all_routes]
    if missing_memory:
        print("\n⚠️  Missing memory routes:")
        for r in missing_memory:
            print(f"  {r}")

    expected_trajectory = [
        "/api/v1/trajectory/recent",
        "/api/v1/trajectory/stats",
    ]

    missing_trajectory = [r for r in expected_trajectory if r not in all_routes]
    if missing_trajectory:
        print("\n⚠️  Missing trajectory routes:")
        for r in missing_trajectory:
            print(f"  {r}")

    print("\n=== Router Import Test ===")
    try:
        from atlas.interfaces.api import routes_memory

        print(f"✓ routes_memory: {len(routes_memory.router.routes)} routes defined")
    except Exception as e:
        print(f"✗ routes_memory failed: {e}")

    try:
        from atlas.interfaces.api import routes_trajectory

        print(f"✓ routes_trajectory: {len(routes_trajectory.router.routes)} routes defined")
    except Exception as e:
        print(f"✗ routes_trajectory failed: {e}")


if __name__ == "__main__":
    asyncio.run(main())
