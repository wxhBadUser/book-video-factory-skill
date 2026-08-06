"""Phase 7 HBG opening preview, render workspace, execution, and encoded-video QA."""

from .compiler import (
    OpeningPreviewResult, RenderStageError, RenderStageResult,
    execute_render_stage, generate_opening_preview, prepare_render_stage,
)
from .mix_calibration import (
    MixApprovalResult,
    MixCalibrationError,
    MixCalibrationResult,
    approve_opening_mix,
    calibrate_opening_mix,
    opening_mix_status,
    verify_opening_mix_approval,
)
from .qa import FinalVideoQaError, evaluate_final_video
from .preflight import RenderPreflightError, RenderPreflightResult, preflight_render, verify_render_preflight
from .encoded_visual_qa import (
    EncodedFramePlanResult,
    EncodedVisualQaError,
    EncodedVisualReviewResult,
    build_encoded_frame_plan,
    evaluate_caption_parity_contract,
    review_encoded_master,
    verify_encoded_frame_plan,
)

__all__ = [
    "OpeningPreviewResult", "RenderStageError", "RenderStageResult",
    "generate_opening_preview", "prepare_render_stage", "execute_render_stage",
    "MixCalibrationError", "MixCalibrationResult", "MixApprovalResult",
    "calibrate_opening_mix", "approve_opening_mix", "verify_opening_mix_approval", "opening_mix_status",
    "FinalVideoQaError", "evaluate_final_video",
    "RenderPreflightError", "RenderPreflightResult", "preflight_render", "verify_render_preflight",
    "EncodedVisualQaError", "EncodedFramePlanResult", "EncodedVisualReviewResult",
    "build_encoded_frame_plan", "verify_encoded_frame_plan", "review_encoded_master", "evaluate_caption_parity_contract",
]
