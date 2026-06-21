"""Structured agent-content schema — the RoboCo content standard.

Public surface: the content models, the ``CONTENT_MODELS`` registry, the
``validate_content`` entry point, and the ``ContentValidationError`` raised on
failure. See :mod:`.models` for the per-type schemas.
"""

from __future__ import annotations

from .enums import Severity, Verdict
from .models import (
    CONTENT_MODELS,
    AcVerdict,
    AuditorNote,
    DeveloperNote,
    DocNote,
    Finding,
    PrReviewContent,
    QaNote,
    ResumptionNote,
    TaskDescription,
    WorkUnit,
    required_shape,
    validate_content,
)
from .models import _Content as ContentModel
from .validators import ContentValidationError

__all__ = [
    "CONTENT_MODELS",
    "AcVerdict",
    "AuditorNote",
    "ContentModel",
    "ContentValidationError",
    "DeveloperNote",
    "DocNote",
    "Finding",
    "PrReviewContent",
    "QaNote",
    "ResumptionNote",
    "Severity",
    "TaskDescription",
    "Verdict",
    "WorkUnit",
    "required_shape",
    "validate_content",
]
