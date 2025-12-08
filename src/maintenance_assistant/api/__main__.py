"""Command-line entry point for the local application API."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

import uvicorn


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the API with local-development defaults."""

    parser = argparse.ArgumentParser(description="Run the AI Maintenance Assistant API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parsed = parser.parse_args(arguments)
    uvicorn.run(
        "maintenance_assistant.api.app:app",
        host=parsed.host,
        port=parsed.port,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the script entry point
    raise SystemExit(main())
