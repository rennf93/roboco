"""
Agent SDK Server for A2A Communication.

This module provides a lightweight FastAPI server that runs alongside Claude Code
in each agent container, enabling true peer-to-peer agent communication.

Components:
- models.py: Pydantic models for A2A messages
- server.py: FastAPI application with endpoints for send/receive/poll
"""
