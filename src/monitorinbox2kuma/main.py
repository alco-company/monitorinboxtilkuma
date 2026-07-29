from __future__ import annotations

import argparse
import logging

from .config import load_settings
from .service import BackupMonitorService


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Watch a Microsoft 365 mailbox for Synology backup emails and push the result to Uptime Kuma."
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one polling cycle and exit.",
    )
    args = parser.parse_args()

    settings = load_settings(once=args.once)
    configure_logging(settings.log_level)
    service = BackupMonitorService(settings)
    service.run()


if __name__ == "__main__":
    main()
