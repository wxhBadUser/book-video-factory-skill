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
    CAPTION_TEXT_JOINER,
    IDENTITY_REVIEW_STATUSES,
    PARITY_VERDICTS,
    PASSING_VERDICTS,
    PROVIDER_VERIFICATION_KEYS,
    REVIEW_STATUSES,
    CurrentReviewResult,
    CurrentVisionEvidence,
    MissingVisionEvidenceError,
    ParityResult,
    StaleVisionEvidenceError,
    UntrustedProviderError,
    VisionEvidence,
    VisionProviderError,
    VisionReviewError,
    canonical_caption_text_payload,
    caption_text_set_sha256,
    register_provider_key,
)
from .evaluator import review_current_shot, review_shot
from .evidence import (
    CURRENT_SCHEMA_VERSION,
    SCHEMA_VERSION,
    build_current_vision_evidence_document,
    build_vision_evidence_document,
    load_current_vision_evidence_document,
    load_vision_evidence_document,
    validate_review_decision,
    verify_current_evidence,
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
    "REVIEW_STATUSES",
    "IDENTITY_REVIEW_STATUSES",
    "SCHEMA_VERSION",
    "CURRENT_SCHEMA_VERSION",
    "VISION_PROVIDER_ALLOWLIST",
    "VISION_REVIEW_PROVIDER_RELATIVE",
    "VISION_REVIEW_PROVIDER_SCHEMA",
    "FORBIDDEN_VISION_PROVIDERS",
    "PROVIDER_VERIFICATION_KEYS",
    "CAPTION_TEXT_JOINER",
    "canonical_caption_text_payload",
    "caption_text_set_sha256",
    "BaseVisionProvider",
    "LocalVisionProvider",
    "ClaudeVisionProvider",
    "NullVisionProvider",
    "load_vision_review_provider",
    "register_provider_key",
    "register_claude_provider",
    "ParityResult",
    "CurrentReviewResult",
    "VisionEvidence",
    "CurrentVisionEvidence",
    "VisionReviewError",
    "MissingVisionEvidenceError",
    "StaleVisionEvidenceError",
    "VisionProviderError",
    "UntrustedProviderError",
    "build_vision_evidence_document",
    "build_current_vision_evidence_document",
    "load_vision_evidence_document",
    "load_current_vision_evidence_document",
    "review_shot",
    "review_current_shot",
    "validate_review_decision",
    "validate_vision_provider",
    "verify_evidence_current",
    "verify_current_evidence",
]
