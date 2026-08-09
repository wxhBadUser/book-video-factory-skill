"""Real vision review: a multimodal model must look at the pixels.

Before this package, "the image matches the caption" was a JSON status field a
human or a template could set without anyone looking at the picture. Here the
only way to approve a NEW production image is a ``VisionEvidence`` record proved
by an allowlisted multimodal model that was handed the actual image bytes.

The package fails closed everywhere it can: an untrusted provider, an
image-generation provider, an empty image, a missing call id, a thin reasoning,
a drifted image/caption/prompt hash, or a missing provider (which surfaces
``blocked_by_missing_vision_provider`` rather than a fabricated pass).
"""

from __future__ import annotations

from .contracts import (
    PARITY_VERDICTS,
    PASSING_VERDICTS,
    PROVIDER_VERIFICATION_KEYS,
    MissingVisionEvidenceError,
    ParityResult,
    StaleVisionEvidenceError,
    UntrustedProviderError,
    VisionEvidence,
    VisionProviderError,
    VisionReviewError,
    register_provider_key,
)
from .evaluator import review_shot
from .evidence import (
    SCHEMA_VERSION,
    build_vision_evidence_document,
    load_vision_evidence_document,
    validate_review_decision,
    verify_evidence_current,
)
from .provider import (
    FORBIDDEN_VISION_PROVIDERS,
    VISION_PROVIDER_ALLOWLIST,
    VISION_REVIEW_PROVIDER_RELATIVE,
    VISION_REVIEW_PROVIDER_SCHEMA,
    BaseVisionProvider,
    ClaudeVisionProvider,
    LocalVisionProvider,
    NullVisionProvider,
    load_vision_review_provider,
    register_claude_provider,
    validate_vision_provider,
)

__all__ = [
    "PARITY_VERDICTS",
    "PASSING_VERDICTS",
    "SCHEMA_VERSION",
    "VISION_PROVIDER_ALLOWLIST",
    "VISION_REVIEW_PROVIDER_RELATIVE",
    "VISION_REVIEW_PROVIDER_SCHEMA",
    "FORBIDDEN_VISION_PROVIDERS",
    "PROVIDER_VERIFICATION_KEYS",
    "BaseVisionProvider",
    "LocalVisionProvider",
    "ClaudeVisionProvider",
    "NullVisionProvider",
    "load_vision_review_provider",
    "register_provider_key",
    "register_claude_provider",
    "ParityResult",
    "VisionEvidence",
    "VisionReviewError",
    "MissingVisionEvidenceError",
    "StaleVisionEvidenceError",
    "VisionProviderError",
    "UntrustedProviderError",
    "build_vision_evidence_document",
    "load_vision_evidence_document",
    "review_shot",
    "validate_review_decision",
    "validate_vision_provider",
    "verify_evidence_current",
]
