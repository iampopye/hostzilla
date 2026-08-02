"""Gunicorn entrypoint for the Hostzilla panel.

Run in production:
    gunicorn -w 2 -b 127.0.0.1:2087 wsgi:app
"""

from app import app  # noqa: F401  (exposed for gunicorn as `wsgi:app`)
