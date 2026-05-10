"""ASGI wrapper to run the existing Flask (WSGI) app under an ASGI server like uvicorn.

Usage:
  uvicorn asgi_wrapper:asgi_app --host 0.0.0.0 --port 5000 --reload

This preserves all existing Flask routes while letting you run via uvicorn.
"""
from asgiref.wsgi import WsgiToAsgi

from app.server.app import create_app


def _create_asgi_app():
    # create_app() initializes the Flask app and registers blueprints
    flask_app = create_app()
    return WsgiToAsgi(flask_app)


asgi_app = _create_asgi_app()
