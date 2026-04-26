"""Custom exception classes.

Create specific exceptions for your domain errors. This makes error handling
clearer and allows you to return proper HTTP status codes.

Hierarchy:
    AppError (base)
    ├── NotFoundError       -> 404
    ├── ValidationError     -> 422
    └── AuthenticationError -> 401
"""


class AppError(Exception):
    """Base exception for all application errors.

    All custom exceptions should inherit from this class.
    This allows you to catch all app errors with a single except clause.
    """


class NotFoundError(AppError):
    """Raised when a requested resource does not exist."""


class AuthenticationError(AppError):
    """Raised when authentication fails."""
