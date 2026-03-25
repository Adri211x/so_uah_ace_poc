"""Application entrypoint."""

import logging

logger = logging.getLogger(__name__)


def main() -> None:
    """Start the application."""
    logger.info("Application started")


if __name__ == "__main__":
    main()
