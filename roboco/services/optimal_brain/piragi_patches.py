"""Runtime patches for piragi 0.7.9.

Importing this module applies module-level monkey-patches that correct
behaviors in the installed piragi version:

1. `piragi.chunking.Chunker` hardcodes
   `tokenizer_name="nvidia/llama-embed-nemotron-8b"` as its default, and
   that model requires `trust_remote_code=True` at load time. In non-TTY
   containers the `Do you wish to run the custom code? [y/N]` prompt is
   answered "N", so `AutoTokenizer.from_pretrained` fails and every index
   plugin init raises, leaving the whole RAG layer disabled. We don't need
   a Qwen-accurate tokenizer for chunking — chunk size is only an
   approximation — so we swap in a public tokenizer that loads without
   remote-code prompts.

Apply once, near the top of any module that imports piragi, by doing
`import roboco.services.optimal_brain.piragi_patches  # noqa: F401`.
"""

from __future__ import annotations

from typing import Any

from roboco.logging import get_logger

logger = get_logger(__name__)

# Safe default — bert-base-uncased is widely cached, public, and doesn't
# require trust_remote_code. Token counts won't match qwen3 exactly, but
# chunk_size is treated as a target, not a hard limit.
_SAFE_TOKENIZER = "bert-base-uncased"


class _PatchState:
    """Holds apply-once flag without a module-level global statement."""

    applied: bool = False


def apply_patches() -> None:
    """Apply all piragi runtime patches. Idempotent."""
    if _PatchState.applied:
        return

    try:
        import piragi.chunking as _chunking
        import piragi.semantic_chunking as _semantic

        original_chunker_init = _chunking.Chunker.__init__

        def patched_chunker_init(
            self: Any,
            chunk_size: int = 512,
            chunk_overlap: int = 50,
            tokenizer_name: str = _SAFE_TOKENIZER,
        ) -> None:
            # Always swap the nvidia default for the safe one unless the
            # caller explicitly passed something else.
            effective = (
                _SAFE_TOKENIZER
                if tokenizer_name == "nvidia/llama-embed-nemotron-8b"
                else tokenizer_name
            )
            original_chunker_init(
                self,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                tokenizer_name=effective,
            )

        _chunking.Chunker.__init__ = patched_chunker_init

        # semantic_chunking.Chunker is the same class, but this guards
        # against piragi ever adding an import-time bind of the original.
        if hasattr(_semantic, "Chunker") and _semantic.Chunker is _chunking.Chunker:
            _semantic.Chunker = _chunking.Chunker

        _PatchState.applied = True
        logger.info(
            "Piragi chunker tokenizer default patched",
            safe_tokenizer=_SAFE_TOKENIZER,
        )
    except Exception as e:
        logger.error("Failed to apply piragi patches", error=str(e))


apply_patches()
