"""
Optimal Brain Index Plugins

Each index type is implemented as a plugin following the BaseIndexPlugin interface.
This allows for consistent behavior across all index types while enabling
specialized chunking, metadata handling, and search strategies per type.
"""

from roboco.services.optimal_brain.indexes.base import BaseIndexPlugin, IndexConfig
from roboco.services.optimal_brain.indexes.code import CodeIndexPlugin
from roboco.services.optimal_brain.indexes.decisions import DecisionsIndexPlugin
from roboco.services.optimal_brain.indexes.docs import DocsIndexPlugin
from roboco.services.optimal_brain.indexes.errors import ErrorsIndexPlugin
from roboco.services.optimal_brain.indexes.journals import JournalsIndexPlugin
from roboco.services.optimal_brain.indexes.learnings import LearningsIndexPlugin
from roboco.services.optimal_brain.indexes.playbooks import PlaybooksIndexPlugin
from roboco.services.optimal_brain.indexes.reviews import ReviewsIndexPlugin
from roboco.services.optimal_brain.indexes.standards import StandardsIndexPlugin
from roboco.services.optimal_brain.indexes.vault_notes import VaultNotesIndexPlugin

__all__ = [
    "BaseIndexPlugin",
    "CodeIndexPlugin",
    "DecisionsIndexPlugin",
    "DocsIndexPlugin",
    "ErrorsIndexPlugin",
    "IndexConfig",
    "JournalsIndexPlugin",
    "LearningsIndexPlugin",
    "PlaybooksIndexPlugin",
    "ReviewsIndexPlugin",
    "StandardsIndexPlugin",
    "VaultNotesIndexPlugin",
]
