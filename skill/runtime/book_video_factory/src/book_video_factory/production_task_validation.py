"""Authoritative Schema and semantic validation for production image tasks."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from book_video_factory.semantic_alignment.contract_bindings import (
    ContractBindingError,
    aggregate_contract_bindings_sha256,
    normalize_contract_bindings,
)
from book_video_factory.semantic_alignment.models import VisualProposition
from book_video_factory.semantic_alignment.prompting import (
    PromptBindingError,
    verify_prompt_binding,
)


class ProductionTaskValidationError(ValueError):
    """A current production image task violates its public contract."""


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    schema_path = Path(__file__).resolve().parents[2] / "schemas" / "production_image_task.v1.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProductionTaskValidationError(
            f"production image task Schema is unavailable: {error}"
        ) from error
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _schema_path(error: Any) -> str:
    return ".".join(str(item) for item in error.absolute_path) or "$"


def _validate_reference_contract(contract: Mapping[str, Any]) -> None:
    """Structural validation for the optional visual reference contract on a scene task."""
    if not isinstance(contract, Mapping):
        raise ProductionTaskValidationError("reference_contract must be an object")
    if contract.get("schema_version") != "visual-reference-contract.v1":
        raise ProductionTaskValidationError("reference_contract schema_version is invalid")
    style = contract.get("style")
    if not isinstance(style, Mapping) or style.get("required") is not True:
        raise ProductionTaskValidationError("reference_contract.style.required must be true")
    identity = contract.get("identity")
    if not isinstance(identity, Mapping):
        raise ProductionTaskValidationError("reference_contract.identity must be an object")
    characters = identity.get("characters", [])
    if not isinstance(characters, list):
        raise ProductionTaskValidationError("reference_contract.identity.characters must be an array")
    for item in characters:
        if not isinstance(item, Mapping) or not isinstance(item.get("character_id"), str) or not item["character_id"]:
            raise ProductionTaskValidationError("reference_contract.identity.characters entries must declare character_id")
        for field in ("apparent_age_range", "wardrobe_state", "narrative_role", "gender_presentation"):
            if field in item and not isinstance(item[field], str):
                raise ProductionTaskValidationError(f"reference_contract.identity.characters.{field} must be a string")
    location = contract.get("location")
    if not isinstance(location, Mapping):
        raise ProductionTaskValidationError("reference_contract.location must be an object")
    location_type = location.get("location_reference_type")
    if location_type not in {"persistent_location_anchor", "generic_environment_reference", "none"}:
        raise ProductionTaskValidationError(
            "reference_contract.location.location_reference_type must be persistent_location_anchor, "
            "generic_environment_reference, or none"
        )
    if not isinstance(location.get("required_persistent", False), bool):
        raise ProductionTaskValidationError("reference_contract.location.required_persistent must be a boolean")
    continuity = contract.get("continuity")
    if not isinstance(continuity, Mapping) or not isinstance(continuity.get("use_previous_scene"), bool):
        raise ProductionTaskValidationError("reference_contract.continuity must declare use_previous_scene")


def _validate_participant_agreement(task: Mapping[str, Any], contract: Mapping[str, Any]) -> None:
    """S4: when unlisted characters are forbidden, the declared identity characters must
    exactly equal the expected visible character set (cardinality reaches scene generation)."""
    participant = task.get("participant_constraint")
    if not isinstance(participant, Mapping):
        return
    if participant.get("allow_unlisted_narrative_characters") is False:
        expected = {str(item) for item in participant.get("expected_visible_character_ids", []) if str(item).strip()}
        identity = contract.get("identity") or {}
        declared = [
            str(item.get("character_id", "")).strip()
            for item in identity.get("characters", [])
            if isinstance(item, Mapping) and str(item.get("character_id", "")).strip()
        ]
        if sorted(expected) != sorted(declared):
            raise ProductionTaskValidationError(
                "participant cardinality mismatch: expected visible characters "
                f"{sorted(expected)} do not match declared identity characters {sorted(declared)}"
            )


def validate_production_image_task(task: Mapping[str, Any]) -> None:
    """Fail closed on Schema, mode, hash, aggregate, or Prompt Binding drift."""
    raw_contract = task.get("reference_contract")
    if raw_contract is not None:
        _validate_reference_contract(raw_contract)
        _validate_participant_agreement(task, raw_contract)

    if not isinstance(task, Mapping):
        raise ProductionTaskValidationError("production image task must be an object")
    raw_binding = task.get("prompt_binding")
    if isinstance(raw_binding, Mapping):
        raw_children = raw_binding.get("caption_contract_bindings")
        if isinstance(raw_children, list) and any(
            isinstance(item, Mapping) and "caption_visual_contract_sha256" in item
            for item in raw_children
        ):
            raise ProductionTaskValidationError(
                "legacy caption_visual_contract_sha256 child keys are forbidden"
            )
    errors = sorted(
        _validator().iter_errors(dict(task)),
        key=lambda error: (list(error.absolute_path), error.message),
    )
    if errors:
        error = errors[0]
        mode = ""
        proposition_raw = task.get("visual_proposition")
        if isinstance(proposition_raw, Mapping):
            mode = f" for {proposition_raw.get('mode', '?')} proposition"
        raise ProductionTaskValidationError(
            f"production image task Schema violation{mode} at {_schema_path(error)}: {error.message}"
        )

    caption_ids = [str(item) for item in task["caption_ids"]]
    beat_ids = [str(item) for item in task["source_beat_ids"]]
    raw_binding = task["prompt_binding"]
    try:
        contract_bindings = normalize_contract_bindings(
            raw_binding["caption_contract_bindings"],
            expected_caption_ids=caption_ids,
        )
        aggregate_sha = aggregate_contract_bindings_sha256(
            contract_bindings,
            expected_caption_ids=caption_ids,
        )
    except ContractBindingError as error:
        raise ProductionTaskValidationError(str(error)) from error
    if task["caption_visual_contract_sha256"] != aggregate_sha:
        raise ProductionTaskValidationError(
            "production image task aggregate SHA does not match ordered Contract bindings"
        )

    proposition = VisualProposition.from_mapping(task["visual_proposition"])
    if proposition.mode == "Literal" and not any(
        item.must_be_visible for item in proposition.entity_visibility
    ):
        raise ProductionTaskValidationError(
            "production image task Literal proposition requires visible entities"
        )
    if proposition.mode == "Symbolic" and not proposition.surrogate_objects:
        raise ProductionTaskValidationError(
            "production image task Symbolic proposition requires surrogate objects"
        )
    if proposition.mode == "Abstract" and any(
        item.must_be_visible for item in proposition.entity_visibility
    ):
        raise ProductionTaskValidationError(
            "production image task Abstract proposition cannot require visible entities"
        )

    prompt_sha = hashlib.sha256(task["prompt"].encode("utf-8")).hexdigest()
    if task["prompt_sha256"] != prompt_sha:
        raise ProductionTaskValidationError(
            "production image task prompt_sha256 does not match its prompt"
        )
    try:
        verify_prompt_binding(
            raw_binding,
            caption_text=task["caption_text"],
            proposition=proposition,
            prompt=task["prompt"],
            caption_visual_contract_sha256=aggregate_sha,
            group_id=raw_binding["group_id"],
            caption_contract_bindings=contract_bindings,
            caption_group_sha256=raw_binding["caption_group_sha256"],
            caption_ids=caption_ids,
            scene_id=task["scene_id"],
            shot_id=task["shot_id"],
            beat_ids=beat_ids,
        )
    except (PromptBindingError, KeyError, TypeError, ValueError) as error:
        raise ProductionTaskValidationError(
            f"production image task prompt binding is stale: {error}"
        ) from error


__all__ = ["ProductionTaskValidationError", "validate_production_image_task"]
