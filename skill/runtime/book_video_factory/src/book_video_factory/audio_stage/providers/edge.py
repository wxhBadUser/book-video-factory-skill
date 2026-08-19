"""Legacy Edge-TTS narration provider (explicit legacy only).

Edge-TTS remains the legacy narration engine. New expressive production uses
MiniMax; the contract forbids Edge as an implicit fallback. The real Edge
synthesis implementation is the existing HBG ``build_narration.mjs`` path
(``audio_stage/hbg_adapter.py`` + ``audio_stage/compiler.py``); this provider
exists so the provider boundary and the ``legacy_edge`` policy are explicit,
and to reject any attempt to run Edge under ``minimax_required``.
"""

from __future__ import annotations

from ..providers.base import (
    NarrationChunkRequest,
    NarrationChunkResult,
    NarrationCredentialError,
    NarrationProvider,
    NarrationProviderError,
    PROVIDER_EDGE,
    PROVIDER_POLICY_LEGACY_EDGE,
    PROVIDER_POLICY_MINIMAX_REQUIRED,
)


class EdgeNarrationProvider(NarrationProvider):
    provider_id = PROVIDER_EDGE
    policy = PROVIDER_POLICY_LEGACY_EDGE

    def __init__(self, *, policy: str = PROVIDER_POLICY_LEGACY_EDGE) -> None:
        if policy != PROVIDER_POLICY_LEGACY_EDGE:
            raise NarrationProviderError(
                "Edge-TTS is legacy-only; it cannot serve "
                f"provider_policy={policy!r} (must be {PROVIDER_POLICY_LEGACY_EDGE!r})"
            )
        self._policy = policy

    def preflight(self) -> None:
        # Edge synthesis is implemented by the legacy HBG compiler path. The
        # provider boundary only asserts the explicit legacy policy; actual
        # Edge audio production is out of scope for the new provider pipeline.
        return None

    def synthesize(self, request: NarrationChunkRequest) -> NarrationChunkResult:
        raise NarrationProviderError(
            "Edge-TTS synthesis is provided by the legacy HBG audio stage; "
            "the new provider pipeline does not synthesize Edge chunks. "
            f"refusing chunk {request.chunk_id!r}"
        )
