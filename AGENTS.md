# AGENTS.md

## Project overview

Python project managed with uv. Follows SmartOps conventions for code quality, testing, and documentation.

## Setup commands

- Install deps: `uv sync`
- Run app: `uv run python -m src.app.main`
- Run tests: `uv run pytest`
- Lint: `uv run ruff check --fix .`
- Format: `uv run ruff format .`
- Pre-commit install: `pre-commit install`

## Code style

- Python 3.12
- Use type hints for all public functions and return types
- Follow PEP 8 conventions
- Use Ruff for linting and formatting (line-length: 100)
- All code, comments, docstrings, logs, and error messages MUST be in English
- Use logging.getLogger(__name__) instead of print()
- DO NOT use f-strings with logging (use % formatting: `logger.info("User %s logged in", user_id)`)
- Use Google-style docstrings
- Use pathlib for file paths
- Use f-strings for general string formatting (NOT in logging)

## File generation rules

- DO NOT automatically generate README.md, CHANGELOG.md, or documentation files
- DO NOT include icons or emojis in code, logs, comments, or documentation
- Only create documentation files when explicitly requested
- Prefer updating existing documentation over creating new files
- If new documentation is needed, ask the user first

## File naming

- File and directory names must be in lowercase
- Use underscores to separate words in Python files (e.g., user_service.py)
- Use hyphens in documentation files (e.g., git-conventions.md)
- Code files follow Python conventions: snake_case

## Architecture patterns

- Use dependency injection
- Separate business logic from controllers (service layer pattern)
- Validate inputs with Pydantic schemas
- Follow repository pattern for data access
- Keep functions focused and single-purpose
- Avoid deep nesting (max 3-4 levels)

## Error handling

- Use specific exceptions, not generic Exception
- Include descriptive error messages in English
- Log errors with appropriate context
- Do not silently catch and ignore exceptions
- Create custom exception classes when appropriate

## Testing

- Use pytest as testing framework
- Write tests for all new features
- Aim for >80% code coverage
- Use fixtures for test data setup
- Test both happy paths and edge cases
- Run `uv run pytest` before committing

## Package manager

- Use uv to manage dependencies and virtual environments
- Use `uv add` to add new dependencies
- Use `uv add --dev` for development dependencies
- Use `uv sync` to install dependencies
- Do not use pip directly, use `uv pip` if necessary
- Lock dependencies with `uv lock`

## Git conventions

- Follow Conventional Commits format: `<type>(<scope>): <summary> [JIRA-KEY]`
- Valid types: feat, fix, docs, style, refactor, perf, test, chore, ci, build
- Commits should be atomic and focused
- Write commit messages in English
- Branch format: `<prefix>/<JIRA-KEY>-<short-descriptive-name>`

## Code review and supervision

- All AI-generated code must be reviewed by the developer before acceptance
- Developers are responsible for understanding and testing all generated code
- Break down large features into smaller, reviewable modules
- Never commit code without thorough review and testing
- Use AI as an assistant, not as a replacement for developer expertise

## Memory bank

If the `memory-bank/` directory exists:
- Read ALL files at the start of a session
- Update activeContext.md with current changes
- Document technical decisions in systemPatterns.md
- Keep progress.md updated
- When "update memory bank" is requested, review all files

## Project conventions

- See `docs/` for detailed convention documents
- Pre-commit hooks are configured for code quality enforcement
- Gitleaks is configured to prevent secret leaks
