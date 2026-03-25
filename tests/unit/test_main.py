"""Tests for the application entrypoint."""

from src.app.main import main


def test_main_runs() -> None:
    """Verify that main executes without errors."""
    main()
