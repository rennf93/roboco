"""
LLM Communication Layer

Provides utilities for efficient communication with Large Language Models,
including TOON serialization for token-efficient data transfer.
"""

from roboco.llm.metrics import ToonMetrics, get_toon_metrics
from roboco.llm.toon_adapter import ToonAdapter, ToonConfig, get_toon_adapter

__all__ = [
    "ToonAdapter",
    "ToonConfig",
    "ToonMetrics",
    "get_toon_adapter",
    "get_toon_metrics",
]
