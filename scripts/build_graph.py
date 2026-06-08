#!/usr/bin/env python3
"""CLI script to build the PyPI package relationship graph."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Add src to path
project_root = Path(__file__).parent.parent
src_path = project_root / "src"
sys.path.insert(0, str(src_path))

from package_doctor.graph.indexer import build_graph, rebuild_graph


def get_db_path() -> str:
    """Get the database path from environment or use default."""
    # Check environment variable first
    if env_path := os.environ.get("PACKAGE_DOCTOR_DB_PATH"):
        return env_path

    # Default path in project root
    return str(project_root / ".data" / "packages.kuzu")


def ensure_data_dir(db_path: str) -> None:
    """Ensure the data directory exists."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)


async def run_build(limit: int | None = None, rebuild: bool = False) -> None:
    """Run the graph build process."""
    db_path = get_db_path()
    ensure_data_dir(db_path)

    print(f"Using database: {db_path}")
    print(f"Limit: {limit or 'all'}")
    print(f"Rebuild: {rebuild}")
    print("-" * 50)

    api_key = os.environ.get("ANTHROPIC_API_KEY")

    if rebuild:
        results = await rebuild_graph(
            db_path=db_path,
            limit=limit or 10000,
            batch_size=20,
            api_key=api_key,
        )
    else:
        results = await build_graph(
            db_path=db_path,
            limit=limit or 10000,
            batch_size=20,
            api_key=api_key,
        )

    # Summary
    success_count = sum(1 for r in results if r.get("success", False))
    error_count = len(results) - success_count

    print("-" * 50)
    print(f"Completed: {success_count} success, {error_count} errors")

    if error_count > 0:
        print("\nErrors:")
        for r in results:
            if not r.get("success"):
                print(f"  - {r.get('name')}: {r.get('error', 'Unknown error')}")


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Build the PyPI package relationship graph"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of packages to process (default: 10000)",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Clear existing data and rebuild from scratch",
    )

    args = parser.parse_args()

    asyncio.run(run_build(limit=args.limit, rebuild=args.rebuild))


if __name__ == "__main__":
    main()
