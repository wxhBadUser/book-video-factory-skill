"""Narration providers (M4A expressive narration)."""

from .base import (
    MINIMAX_HD_MODEL,
    NarrationChunkRequest,
    NarrationChunkResult,
    NarrationCredentialError,
    NarrationProvider,
    NarrationProviderError,
    PROVIDER_EDGE,
    PROVIDER_MINIMAX,
    PROVIDER_POLICIES,
    PROVIDER_POLICY_LEGACY_EDGE,
    PROVIDER_POLICY_MINIMAX_REQUIRED,
    SUBTITLE_TIMESTAMPS_SENTENCE,
)
from .edge import EdgeNarrationProvider
from .minimax import MiniMaxNarrationProvider

__all__ = [
    "MINIMAX_HD_MODEL",
    "NarrationChunkRequest",
    "NarrationChunkResult",
    "NarrationCredentialError",
    "NarrationProvider",
    "NarrationProviderError",
    "PROVIDER_EDGE",
    "PROVIDER_MINIMAX",
    "PROVIDER_POLICIES",
    "PROVIDER_POLICY_LEGACY_EDGE",
    "PROVIDER_POLICY_MINIMAX_REQUIRED",
    "SUBTITLE_TIMESTAMPS_SENTENCE",
    "EdgeNarrationProvider",
    "MiniMaxNarrationProvider",
]
