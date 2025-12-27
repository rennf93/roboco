"""
Optimal Brain Services

Plugin-based knowledge management system with support for multiple index types,
conversational RAG (mentor), action validation, and proactive knowledge injection.
"""

from roboco.services.optimal_brain.indexes.base import BaseIndexPlugin, IndexConfig
from roboco.services.optimal_brain.mentor import MentorService, get_mentor_service
from roboco.services.optimal_brain.reviewer import (
    ReviewCategory,
    ReviewerService,
    ReviewSeverity,
    get_reviewer_service,
)
from roboco.services.optimal_brain.validator import (
    ParsedRule,
    RuleSeverity,
    StandardsParser,
    ValidatorService,
    Violation,
    get_validator_service,
)

__all__ = [
    "BaseIndexPlugin",
    "IndexConfig",
    "MentorService",
    "ParsedRule",
    "ReviewCategory",
    "ReviewSeverity",
    "ReviewerService",
    "RuleSeverity",
    "StandardsParser",
    "ValidatorService",
    "Violation",
    "get_mentor_service",
    "get_reviewer_service",
    "get_validator_service",
]
