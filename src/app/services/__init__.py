# Services contain the BUSINESS LOGIC of the application.
# They are the "brain" -- they decide WHAT to do, not HOW to get data.
#
# Rules:
#   - Services call repositories to get/save data
#   - Services NEVER access the database directly
#   - Services NEVER know about HTTP (no Request/Response objects here)
#   - One service per domain concept (UserService, OrderService, etc.)
#
# Example: see health.py in this folder.
