"""Gunicorn entry point. Runs schema setup once (gunicorn --preload) before workers fork."""
from app import app, init_db

init_db()
