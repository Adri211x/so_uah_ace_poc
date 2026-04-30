# Development guide

## Workflow

### 1. Create a branch

```bash
git checkout -b feature/JIRA-123-short-description
```

Branch naming: `<prefix>/<JIRA-KEY>-<description>`

Valid prefixes: `feature`, `fix`, `hotfix`, `refactor`, `chore`, `docs`, `test`, `perf`

### 2. Write code

- All code, comments, docstrings, and logs **must be in English**
- Use `logging.getLogger(__name__)` instead of `print()`
- Add type hints to all functions
- Add docstrings to public functions (Google format)

### 3. Run checks before committing

```bash
just check        # Lint + format
just test         # Run tests
```

Or let pre-commit do it automatically when you commit (it runs on every `git commit`).

### 4. Commit

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```bash
git commit -m "feat(api): add user registration endpoint [JIRA-123]"
```

Format: `<type>(<scope>): <summary> [JIRA-KEY]`

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `chore`, `ci`, `build`

### 5. Push and create Merge Request

```bash
git push -u origin feature/JIRA-123-short-description
```

Then create a Merge Request in GitLab linking the Jira ticket.

## Pre-commit hooks

Pre-commit hooks run automatically on every `git commit` and will:

1. **Ruff lint** -- Checks code quality, auto-fixes what it can
2. **Ruff format** -- Formats code consistently
3. **Trailing whitespace** -- Removes trailing spaces
4. **End of file fixer** -- Ensures files end with a newline
5. **YAML/TOML/JSON check** -- Validates config file syntax
6. **Large file check** -- Blocks files > 1MB
7. **Gitleaks** -- Scans for passwords, API keys, or secrets

If any hook fails, the commit is blocked. Fix the issue and try again.

## CI/CD pipeline

When you push to GitLab, the pipeline runs:

1. **Lint** -- Ruff checks (must pass)
2. **Test** -- pytest with coverage (must pass)
3. **Security** -- Gitleaks, Trivy (vulnerability scan), Semgrep (SAST), Syft (SBOM)
4. **Report** -- Security and quality report
5. **Build** -- Docker image build and release

## Environment variables

Copy the template and fill in your values:

```bash
cp .env.example .env
```

Never commit `.env` files. They are in `.gitignore`.

## Conventions

| Rule | Details |
|------|---------|
| Language | All code in English (variables, comments, logs, docstrings) |
| No emojis | Never use emojis in code, logs, or documentation |
| No print() | Use `logging.getLogger(__name__)` |
| Type hints | Required for all function parameters and return types |
| Docstrings | Google format, for all public functions |
| Exceptions | Use specific exceptions, not generic `Exception` |
| Logging format | `logger.info("User %s logged in", user_id)` (no f-strings) |

For full details, see the `.cursor/rules/` directory.
