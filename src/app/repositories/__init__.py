# Repositories handle DATA ACCESS -- they talk to the database.
#
# Each repository is responsible for ONE database table/entity.
# They provide methods like: get_by_id(), list_all(), create(), delete()
#
# Rules:
#   - Repositories ONLY do database operations (queries, inserts, updates)
#   - They NEVER contain business logic
#   - They return model objects, not HTTP responses
#   - Services call repositories, not the other way around
#
# Example:
#   class UserRepository:
#       def get_by_id(self, user_id: int) -> User | None:
#           return self.session.query(User).filter(User.id == user_id).first()
