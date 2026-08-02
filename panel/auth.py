"""Authentication helpers for the Hostzilla panel.

Thin wrappers over werkzeug password hashing plus a Flask-Login UserMixin
adapter so routes can stay clean.
"""

from flask_login import UserMixin
from werkzeug.security import check_password_hash

import models


class PanelUser(UserMixin):
    """Flask-Login user adapter backed by the users table."""

    def __init__(self, row):
        self.id = str(row["id"])
        self.username = row["username"]
        self.role = row.get("role", "admin")
        self._password_hash = row["password_hash"]

    @classmethod
    def from_id(cls, user_id):
        try:
            row = models.get_user_by_id(int(user_id))
        except (TypeError, ValueError):
            return None
        return cls(row) if row else None

    @classmethod
    def from_username(cls, username):
        row = models.get_user_by_username(username)
        return cls(row) if row else None


def verify_credentials(username, password):
    """Return a PanelUser on valid credentials, else None."""
    if not username or not password:
        return None
    row = models.get_user_by_username(username)
    if not row:
        return None
    if check_password_hash(row["password_hash"], password):
        return PanelUser(row)
    return None
