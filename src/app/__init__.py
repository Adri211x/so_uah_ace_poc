# This file makes "src.app" a Python package.
# The app package contains the entire application, organized in layers:
#
#   api/           -> HTTP endpoints (receives requests, returns responses)
#   services/      -> Business logic (the "brain" of the app)
#   repositories/  -> Data access (talks to the database)
#   models/        -> Database table definitions (SQLAlchemy)
#   schemas/       -> Data validation (Pydantic: what the API accepts/returns)
#   core/          -> Shared utilities (config, custom exceptions, etc.)
