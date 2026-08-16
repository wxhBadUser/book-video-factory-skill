"""Visual Foundation: approved book style masters, character identity pack, location anchors,
and the reference-conditioned generation contract that binds every scene task to concrete
approved images before it enters a generation wave."""

from .approval import (
    VisualFoundationApproval,
    approve_visual_foundation,
    verify_visual_foundation_approval,
    visual_foundation_status,
)
from .character_registry import (
    CharacterEntry,
    CharacterRegistry,
    CharacterRegistryError,
    parse_character_registry,
    resolve_visible_characters,
    verify_registry_covers_characters,
)
from .contracts import (
    CharacterIdentity,
    LifeStageIdentity,
    LocationAnchor,
    StyleMaster,
    VisualFoundationError,
)
from .manifests import (
    build_visual_foundation,
    load_foundation,
    verify_visual_foundation,
)
from .resolver import (
    REFERENCE_PACK_SCHEMA,
    ReferenceInput,
    resolve_reference_pack,
)
from .runner import (
    ReferenceGenerationAttempt,
    ReferenceRunnerError,
    build_cli_edit_runner,
    build_final_generation_prompt,
    execute_reference_conditioned,
    reference_inputs_from_pack,
    verify_reference_inputs_match_pack,
)

__all__ = [
    "CharacterIdentity", "LifeStageIdentity", "LocationAnchor", "StyleMaster",
    "VisualFoundationError", "VisualFoundationApproval",
    "approve_visual_foundation", "verify_visual_foundation_approval", "visual_foundation_status",
    "CharacterEntry", "CharacterRegistry", "CharacterRegistryError",
    "parse_character_registry", "resolve_visible_characters", "verify_registry_covers_characters",
    "build_visual_foundation", "load_foundation", "verify_visual_foundation",
    "REFERENCE_PACK_SCHEMA", "ReferenceInput", "resolve_reference_pack",
    "ReferenceGenerationAttempt", "ReferenceRunnerError",
    "build_cli_edit_runner", "build_final_generation_prompt", "execute_reference_conditioned",
    "reference_inputs_from_pack", "verify_reference_inputs_match_pack",
]
