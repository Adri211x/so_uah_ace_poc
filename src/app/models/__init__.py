# Models define DATABASE TABLES using SQLAlchemy.
#
# Each model maps to one table in the database.
# They describe the columns, types, and relationships between tables.
#
# Example:
#   class User(Base):
#       __tablename__ = "users"
#       id = Column(Integer, primary_key=True)
#       email = Column(String, unique=True, nullable=False)
#       name = Column(String, nullable=False)
#
# Note: Models are NOT the same as Schemas.
#   - Model  = database structure (how data is STORED)
#   - Schema = API structure (how data is SENT/RECEIVED)
