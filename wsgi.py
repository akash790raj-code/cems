#!/usr/bin/env python3
"""
WSGI entry point for production deployment
Usage with Gunicorn: gunicorn --bind 0.0.0.0:8000 wsgi:app
"""

from app import app

if __name__ == "__main__":
    app.run()