# Tests are organized in two folders:
#
#   unit/        -> Fast tests that test ONE thing in isolation.
#                   No database, no network, no external services.
#                   These should run in milliseconds.
#
#   integration/ -> Tests that verify multiple components work together.
#                   May need a database, external APIs, etc.
#                   These are slower but catch real-world bugs.
#
# Run all:           just test
# Run unit only:     just test-unit
# Run integration:   just test-integration
# Run with coverage: just test-cov
