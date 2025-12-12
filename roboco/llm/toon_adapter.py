"""
TOON Adapter

Provides serialization/deserialization between Python objects and TOON format
for token-efficient LLM communication. TOON (Token-Oriented Object Notation)
achieves 30-60% fewer tokens than JSON while maintaining semantic clarity.

Usage:
    adapter = ToonAdapter()

    # Encode data for LLM prompt
    toon_str = adapter.encode({"name": "Alice", "age": 30})

    # Decode LLM response (falls back to JSON if TOON fails)
    data = adapter.decode(response_text)

    # Format for embedding in prompt
    prompt_section = adapter.format_for_prompt("Task Context", task_data)
"""

import json
from dataclasses import dataclass
from typing import Any

import structlog
import toon
from pydantic import BaseModel

logger = structlog.get_logger()


@dataclass
class ToonConfig:
    """Configuration for TOON encoding."""

    delimiter: str = ","
    indent: int = 2
    include_length: bool = True


class ToonAdapter:
    """
    Adapter for TOON serialization at LLM boundaries.

    Converts Python dicts/Pydantic models to TOON for sending to LLMs,
    and parses TOON responses back to Python objects. Falls back to
    JSON parsing if TOON decode fails.
    """

    def __init__(self, config: ToonConfig | None = None) -> None:
        """
        Initialize the TOON adapter.

        Args:
            config: Optional configuration for TOON encoding.
        """
        self.config = config or ToonConfig()
        self.log = logger.bind(component="toon_adapter")

    def encode(self, data: dict[str, Any] | list[Any] | BaseModel) -> str:
        """
        Convert Python object to TOON for LLM consumption.

        Args:
            data: Dictionary, list, or Pydantic model to encode.

        Returns:
            TOON-formatted string.
        """
        if isinstance(data, BaseModel):
            data = data.model_dump()

        return toon.encode(data, indent=self.config.indent)

    def decode(self, toon_str: str) -> dict[str, Any] | list[Any]:
        """
        Parse TOON response from LLM.

        Falls back to JSON parsing if TOON decode fails, logging a warning.

        Args:
            toon_str: TOON-formatted string from LLM response.

        Returns:
            Parsed Python dict or list.

        Raises:
            ValueError: If neither TOON nor JSON parsing succeeds.
        """
        # Try TOON first
        toon_err_msg = ""
        try:
            return toon.decode(toon_str)
        except Exception as toon_error:
            toon_err_msg = str(toon_error)
            self.log.warning(
                "TOON decode failed, trying JSON fallback",
                error=toon_err_msg,
            )

        # Fallback to JSON
        try:
            return json.loads(toon_str)
        except json.JSONDecodeError as json_error:
            self.log.error(
                "Both TOON and JSON decode failed",
                toon_error=toon_err_msg,
                json_error=str(json_error),
            )
            raise ValueError(
                f"Failed to decode response as TOON or JSON: {toon_str[:100]}..."
            ) from json_error

    def format_for_prompt(self, label: str, data: dict[str, Any]) -> str:
        """
        Format data with label for embedding in LLM prompt.

        Args:
            label: Section label (e.g., "Task Context", "Requirements").
            data: Data to encode.

        Returns:
            Formatted string suitable for prompt inclusion.
        """
        encoded = self.encode(data)
        return f"{label}:\n{encoded}"

    def format_tabular_request(
        self,
        fields: list[str],
        description: str,
        example_rows: list[list[str]] | None = None,
    ) -> str:
        """
        Format a request for tabular TOON response.

        Args:
            fields: Column names for the table.
            description: What the LLM should return.
            example_rows: Optional example data rows.

        Returns:
            Formatted instruction for LLM to return TOON tabular data.
        """
        fields_str = ",".join(fields)
        header = f"[N,]{{{fields_str}}}:"
        instruction = f"{description}\n\nFormat response as TOON tabular:\n{header}"

        if example_rows:
            instruction += "\n"
            for row in example_rows:
                instruction += f"{self.config.delimiter.join(row)}\n"

        return instruction

    def estimate_token_savings(
        self,
        data: dict[str, Any] | list[Any],
    ) -> tuple[int, int, float]:
        """
        Estimate token savings of TOON vs JSON for given data.

        Args:
            data: Data to compare.

        Returns:
            Tuple of (json_chars, toon_chars, savings_percent).
        """
        json_str = json.dumps(data, separators=(",", ":"))
        toon_str = self.encode(data)

        json_chars = len(json_str)
        toon_chars = len(toon_str)

        savings = (1 - toon_chars / json_chars) * 100 if json_chars > 0 else 0.0

        return json_chars, toon_chars, savings


# Module-level singleton holder
class _AdapterHolder:
    """Holder for singleton ToonAdapter instance."""

    instance: ToonAdapter | None = None


def get_toon_adapter() -> ToonAdapter:
    """Get the default TOON adapter singleton."""
    if _AdapterHolder.instance is None:
        _AdapterHolder.instance = ToonAdapter()
    return _AdapterHolder.instance
