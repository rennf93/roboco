"""
LLM Communication Layer

Provides utilities for efficient communication with Large Language Models,
including TOON serialization for token-efficient data transfer.
"""

from roboco.llm.metrics import ToonMetrics
from roboco.llm.toon_adapter import ToonAdapter, ToonConfig

__all__ = [
    "ToonAdapter",
    "ToonConfig",
    "ToonMetrics",
]
