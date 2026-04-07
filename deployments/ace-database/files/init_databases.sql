-- ============================================================================
-- DATABASE INITIALIZATION
-- Creates the required database for ACE platform
-- Tables and extensions are managed by Alembic migrations
-- ============================================================================

-- Create ace database
CREATE DATABASE ace;

-- Grant permissions to ace-admin user
GRANT ALL PRIVILEGES ON DATABASE ace TO "ace-admin";
