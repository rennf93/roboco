"""
RoboCo API

FastAPI application with WebSocket support for real-time communication.
"""

from roboco.api.app import app, create_app

__all__ = ["app", "create_app"]
