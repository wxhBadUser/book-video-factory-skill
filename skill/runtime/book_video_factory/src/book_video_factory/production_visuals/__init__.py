"""Phase 6 production scene asset registration, review, and approval."""

from .attempt_ledger import GenerationAttemptError, GenerationAttemptResult, record_generation_attempt
from .registry import SceneAssetError, SceneAssetResult, register_scene_asset
from .review import SceneReviewError, SceneReviewResult, approve_scene_assets, build_scene_asset_review
from .scheduler import (
    GenerationFinalizeResult,
    GenerationPlanResult,
    GenerationScheduleError,
    GenerationWaveResult,
    finalize_generation_wave,
    next_generation_wave,
    plan_generation_run,
)
from .sheet_validation import SheetValidationError, SheetValidationResult, validate_scene_sheet
from .staging import (
    AssetNormalizationError,
    AssetNormalizationResult,
    StagedAssetResolution,
    StagingResolutionError,
    normalize_scene_asset,
    resolve_staged_asset,
)

__all__ = [
    "GenerationAttemptError", "GenerationAttemptResult", "record_generation_attempt",
    "SceneAssetError", "SceneAssetResult", "register_scene_asset",
    "SceneReviewError", "SceneReviewResult", "build_scene_asset_review", "approve_scene_assets",
    "GenerationScheduleError", "GenerationPlanResult", "GenerationWaveResult", "GenerationFinalizeResult",
    "plan_generation_run", "next_generation_wave", "finalize_generation_wave",
    "SheetValidationError", "SheetValidationResult", "validate_scene_sheet",
    "StagingResolutionError", "StagedAssetResolution", "AssetNormalizationError", "AssetNormalizationResult",
    "resolve_staged_asset", "normalize_scene_asset",
]
