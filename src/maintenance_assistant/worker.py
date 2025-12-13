"""Command-line entry point for the persistent ingestion worker."""

from __future__ import annotations

from argparse import ArgumentParser

from maintenance_assistant.config import Settings
from maintenance_assistant.jobs import run_worker


def main() -> None:
    parser = ArgumentParser(description="Process queued maintenance-manual ingestion jobs")
    parser.add_argument("--once", action="store_true", help="Process at most one job and exit")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    arguments = parser.parse_args()
    if arguments.poll_seconds <= 0:
        parser.error("--poll-seconds must be greater than zero")
    run_worker(Settings.from_environment(), arguments.poll_seconds, arguments.once)


if __name__ == "__main__":
    main()
