"""V2 Host ImageGen + Multimodal Judge autonomous loop (Stage 5, fail-closed).

The Python Runtime never calls Host ImageGen and never judges aesthetics. It
derives the next legal Host action, verifies real Host events and files,
recomputes SHA-256, applies multimodal judge verdicts bound by hash, and
advances the asset catalog. Every transition is idempotent by action key.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from book_video_factory.director_stage.contracts import (
    EDIT_DECISIONS_REL,
    VISUAL_PARAGRAPHS_REL,
    validate_edit_decisions,
    validate_visual_paragraphs,
)
from book_video_factory.host_orchestration import (
    MAX_ATTEMPTS,
    HostActionError,
    derive_next_action,
    read_ledgers,
    reconcile_prepared_completions,
    register_action,
    verify_host_event,
)
from book_video_factory.manifests import safe_project_output, sha256_file
from book_video_factory.transaction_lock import project_transaction_lock
from book_video_factory.visual_covenant import (
    CATALOG_REL,
    VisualCovenantError,
    load_asset_catalog,
    write_asset_catalog,
)


class ProductionOrchestrationError(RuntimeError):
    """A V2 production generation/judge loop violated its contract (fail-closed)."""


PRODUCTION_IMAGE_PLAN_REL = "04_director/PRODUCTION_IMAGE_PLAN.v2.json"
PLAN_SCHEMA_VERSION = "production-image-plan.v2"
GENERATE_RUNTIME_SECONDS = 900

# Canonical Judge Policy derived from Revision 2 section 5. The runtime only
# hashes this policy; the Host multimodal agent is the sole aesthetic judge.
_JUDGE_POLICY = {
    "schema_version": "multimodal-judge-policy.v1",
    "hard_failure_categories": [
        "visually_unusable",
        "visual_function_fails",
        "anachronistic_modern_infection",
        "period_or_place_error",
        "severe_style_drift",
        "garbled_text_watermark_or_ui",
        "severe_anatomy_error",
    ],
    "warn_not_fail": [
        "minor_facial_drift",
        "non_pixel_perfect_character",
        "minor_scene_layout_drift",
    ],
    "max_revision_attempts": 3,
    "retry_requires_changed_strategy": True,
}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def judge_policy_sha256() -> str:
    """Stable SHA-256 of the active multimodal judge policy."""
    return _sha_bytes(_canonical(_JUDGE_POLICY))


def _load_object(root: Path, relative: str, label: str) -> dict[str, Any]:
    path = safe_project_output(root, Path(relative))
    if path.is_symlink() or not path.is_file():
        raise ProductionOrchestrationError(f"{label} is missing or symlinked: {relative}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProductionOrchestrationError(f"{label} is unreadable: {error}") from error
    if not isinstance(value, dict):
        raise ProductionOrchestrationError(f"{label} must be a JSON object")
    return value


def _covenant_approval_sha(root: Path) -> str:
    try:
        status = _verify_asset_catalog_safe(root)
    except ProductionOrchestrationError as error:
        raise error
    approval = status.get("covenant_approval_sha256")
    if not isinstance(approval, str) or not approval:
        raise ProductionOrchestrationError("asset catalog is missing its covenant approval sha256")
    return approval


def _verify_asset_catalog_safe(root: Path) -> dict[str, Any]:
    from book_video_factory.visual_covenant import verify_asset_catalog

    status = verify_asset_catalog(root)
    if status.get("status") != "asset_catalog_verified":
        raise ProductionOrchestrationError(
            f"asset catalog is not verified: {status.get('status')}"
        )
    return status


@lru_cache(maxsize=1)
def _judge_validator() -> Draft202012Validator:
    schema_path = Path(__file__).resolve().parents[2] / "schemas" / "multimodal_visual_judge.v2.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProductionOrchestrationError(f"judge schema is unavailable: {error}") from error
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _schema_error(error: Any) -> str:
    return ".".join(str(item) for item in error.absolute_path) or "$"


def verify_technical_diagnostics(project: Path, path: str, expected_sha256: str) -> dict[str, Any]:
    """Re-verify a Host asset path and recompute SHA-256, decoding it as an image."""
    root = project.expanduser().resolve()
    target = safe_project_output(root, Path(path))
    if target.is_symlink() or not target.is_file():
        raise ProductionOrchestrationError(f"asset is missing or symlinked: {path}")
    actual = sha256_file(target)
    if actual != expected_sha256:
        raise ProductionOrchestrationError(f"asset hash is stale: {path}")
    try:
        from PIL import Image

        with Image.open(target) as opened:
            width, height = opened.size
            fmt = opened.format or ""
            mode = opened.mode
            opened.load()
    except Exception as error:  # noqa: BLE001 - any decode failure is fail-closed
        raise ProductionOrchestrationError(f"asset is not a decodable image: {error}") from error
    return {
        "path": path,
        "width": width,
        "height": height,
        "format": fmt,
        "mode": mode,
        "landscape_16_9": abs(width / height - 16 / 9) <= 0.02,
        "sha256": actual,
        "bytes": target.stat().st_size,
    }


def _load_plan(root: Path) -> dict[str, Any] | None:
    plan_path = safe_project_output(root, Path(PRODUCTION_IMAGE_PLAN_REL))
    if plan_path.is_symlink() or not plan_path.is_file():
        return None
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ProductionOrchestrationError(f"{PRODUCTION_IMAGE_PLAN_REL} is unreadable")
    if not isinstance(plan, dict) or plan.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise ProductionOrchestrationError(f"{PRODUCTION_IMAGE_PLAN_REL} schema_version is invalid")
    assets = plan.get("assets")
    if not isinstance(assets, list):
        raise ProductionOrchestrationError(f"{PRODUCTION_IMAGE_PLAN_REL} requires a nonempty assets list")
    by_id: dict[str, dict[str, Any]] = {}
    for item in assets:
        if not isinstance(item, dict):
            raise ProductionOrchestrationError(f"{PRODUCTION_IMAGE_PLAN_REL} asset is not an object")
        asset_id = item.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id:
            raise ProductionOrchestrationError(f"{PRODUCTION_IMAGE_PLAN_REL} asset requires asset_id")
        if asset_id in by_id:
            raise ProductionOrchestrationError(f"{PRODUCTION_IMAGE_PLAN_REL} duplicate asset_id: {asset_id}")
        for field in ("asset_family", "visual_function", "bound_paragraph_id"):
            if not isinstance(item.get(field), str) or not item.get(field):
                raise ProductionOrchestrationError(
                    f"{PRODUCTION_IMAGE_PLAN_REL} asset {asset_id} requires {field}"
                )
        reference = item.get("reference_asset_id")
        if reference is not None and (not isinstance(reference, str) or not reference):
            raise ProductionOrchestrationError(
                f"{PRODUCTION_IMAGE_PLAN_REL} asset {asset_id} reference_asset_id is invalid"
            )
        by_id[asset_id] = item
    return plan


def _visual_paragraph_sha(project: Path, paragraph_id: str) -> str:
    root = project.expanduser().resolve()
    payload = _load_object(root, VISUAL_PARAGRAPHS_REL, "visual paragraphs")
    validated = validate_visual_paragraphs(payload)
    targets = [item for item in validated["paragraphs"] if item["paragraph_id"] == paragraph_id]
    if not targets:
        raise ProductionOrchestrationError(f"visual paragraph not found: {paragraph_id}")
    return _sha_bytes(_canonical(targets[0]))


def _read_catalog_raw(root: Path) -> list[dict[str, Any]]:
    path = safe_project_output(root, Path(CATALOG_REL))
    if path.is_symlink() or not path.is_file():
        raise ProductionOrchestrationError(f"{CATALOG_REL} is missing or symlinked")
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProductionOrchestrationError(f"{CATALOG_REL} is unreadable: {error}") from error
    if not isinstance(catalog, dict) or not isinstance(catalog.get("assets"), list):
        raise ProductionOrchestrationError(f"{CATALOG_REL} is malformed")
    return list(catalog["assets"])


def _write_catalog(root: Path, assets: list[dict[str, Any]], approval_sha: str) -> None:
    path = safe_project_output(root, Path(CATALOG_REL))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProductionOrchestrationError(f"{CATALOG_REL} is unreadable: {error}") from error
    release_id = str(payload.get("release_id", ""))
    if not release_id:
        raise ProductionOrchestrationError(f"{CATALOG_REL} is missing release_id")
    write_asset_catalog(root, release_id=release_id, approval_sha=approval_sha, assets=assets)


def _release_id(root: Path) -> str:
    decisions = _load_object(root, EDIT_DECISIONS_REL, "edit decisions")
    release_id = decisions.get("release_id")
    if not isinstance(release_id, str) or not release_id:
        raise ProductionOrchestrationError("edit decisions is missing release_id")
    return release_id


def _load_decisions(root: Path) -> tuple[dict[str, Any], str]:
    payload = _load_object(root, EDIT_DECISIONS_REL, "edit decisions")
    validated = validate_edit_decisions(payload)
    return validated, _sha_bytes(
        _canonical(_load_object(root, EDIT_DECISIONS_REL, "edit decisions"))
    )


def _parse_attempt(key: str, prefix: str) -> tuple[str, int]:
    marker = f":{prefix}_"
    if marker not in key:
        raise ProductionOrchestrationError(f"idempotency_key is not a {prefix} action: {key}")
    _, tail = key.split(marker, 1)
    if ":attempt-" not in tail:
        raise ProductionOrchestrationError(f"idempotency_key lacks attempt suffix: {key}")
    asset_id, attempt_text = tail.split(":attempt-", 1)
    if not asset_id or not attempt_text.isdigit():
        raise ProductionOrchestrationError(f"idempotency_key is malformed: {key}")
    return asset_id, int(attempt_text)


def _catalog_asset_map(assets: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("asset_id", "")): item for item in assets if isinstance(item, dict)}


def _succeeded_generate_events(state: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    events_by_key = {str(item["idempotency_key"]): item for item in state["events"]}
    result: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for action in state["actions"]:
        if action.get("action_type") != "generate_image":
            continue
        key = action.get("idempotency_key")
        event = events_by_key.get(str(key))
        if event is not None and event.get("status") == "succeeded":
            result.append((action, event))
    return result


def _ensure_generate_actions_locked(project: Path) -> int:
    """Register generate_image actions for assets needing a new or revised attempt."""
    root = project.expanduser().resolve()
    release_id = _release_id(root)
    decisions, _ = _load_decisions(root)
    plan = _load_plan(root)
    approval_sha = _covenant_approval_sha(root)
    assets = _read_catalog_raw(root)
    asset_map = _catalog_asset_map(assets)
    state = read_ledgers(root)
    action_keys = {str(item.get("idempotency_key")) for item in state["actions"]}
    terminal_keys = {str(item.get("idempotency_key")) for item in state["events"]}
    registered = 0

    generate_ids = [
        str(item["asset_id"])
        for item in decisions["decisions"]
        if item["decision"] == "generate_new"
    ]
    for asset_id in generate_ids:
        entry = asset_map.get(asset_id)
        if entry is not None and entry.get("status") == "approved_production_asset":
            continue
        if entry is not None and entry.get("status") == "blocked_quality_failure":
            continue
        if plan is None or asset_id not in {
            str(item.get("asset_id")) for item in plan.get("assets", [])
        }:
            raise ProductionOrchestrationError(
                f"generate_new asset {asset_id} has no PRODUCTION_IMAGE_PLAN entry"
            )
        plan_entry = next(
            item
            for item in plan["assets"]
            if str(item.get("asset_id")) == asset_id
        )
        family = str(plan_entry["asset_family"])
        func = str(plan_entry["visual_function"])
        reference = plan_entry.get("reference_asset_id")
        retry_strategy = None
        attempt = 1
        if entry is not None and entry.get("status") == "pending_generation":
            attempt = int(entry.get("generation_attempt", 1)) + 1
            pending_retry = entry.get("pending_retry")
            if isinstance(pending_retry, dict):
                retry_strategy = pending_retry.get("retry_strategy")
            if attempt > MAX_ATTEMPTS:
                continue
        key = f"{release_id}:GEN_{asset_id}:attempt-{attempt}"
        if key in action_keys:
            continue
        if key in terminal_keys:
            continue
        action = {
            "schema_version": "host-agent-action.v2",
            "action_id": f"GEN_{asset_id}_{attempt:04d}",
            "release_id": release_id,
            "project_id": root.name,
            "action_type": "generate_image",
            "idempotency_key": key,
            "attempt": attempt,
            "inputs": [{
                "kind": "visual_paragraph",
                "path": VISUAL_PARAGRAPHS_REL,
                "sha256": _sha_bytes(_canonical(
                    {"bound_paragraph_id": plan_entry["bound_paragraph_id"]}
                )),
            }],
            "expected_output": {
                "kind": "production_image",
                "asset_id": asset_id,
                "asset_family": family,
                "visual_function": func,
                "reference_asset_id": reference,
                "retry_strategy": retry_strategy,
            },
            "max_runtime_seconds": GENERATE_RUNTIME_SECONDS,
        }
        if isinstance(reference, str) and reference in asset_map:
            ref_entry = asset_map[reference]
            if ref_entry.get("status") == "approved_production_asset":
                action["inputs"].append({
                    "kind": "reference_asset",
                    "asset_id": reference,
                    "path": str(ref_entry.get("path", "")),
                    "sha256": str(ref_entry.get("file_sha256", "")),
                })
        result = register_action(root, action)
        if result.get("registered"):
            registered += 1
    return registered


def ensure_generate_actions(project: Path) -> int:
    with project_transaction_lock(project) as root:
        try:
            reconcile_prepared_completions(root)
        except HostActionError as error:
            raise ProductionOrchestrationError(str(error)) from error
        return _ensure_generate_actions_locked(root)


def incorporate_generated_assets(project: Path) -> int:
    """Reconcile prepared completions before promoting generated assets."""
    with project_transaction_lock(project) as root:
        try:
            reconcile_prepared_completions(root)
        except HostActionError as error:
            raise ProductionOrchestrationError(str(error)) from error
        return _incorporate_generated_assets_locked(root)


def _incorporate_generated_assets_locked(project: Path) -> int:
    """Verify real generated files and promote them into the catalog as pending_judge."""
    root = project.expanduser().resolve()
    approval_sha = _covenant_approval_sha(root)
    assets = _read_catalog_raw(root)
    asset_map = _catalog_asset_map(assets)
    state = read_ledgers(root)
    candidates = _succeeded_generate_events(state)
    by_asset: dict[str, tuple[int, dict[str, Any], dict[str, Any]]] = {}
    for action, event in candidates:
        asset_id, attempt = _parse_attempt(str(action.get("idempotency_key")), "GEN")
        current = by_asset.get(asset_id)
        if current is None or attempt > current[0]:
            by_asset[asset_id] = (attempt, action, event)

    plan = _load_plan(root)
    plan_by_id: dict[str, dict[str, Any]] = {}
    if plan is not None:
        plan_by_id = {
            str(item.get("asset_id")): item
            for item in plan.get("assets", [])
            if isinstance(item, dict)
        }
    changed = 0
    for asset_id, (attempt, action, event) in sorted(by_asset.items()):
        plan_entry = plan_by_id.get(asset_id)
        if plan_entry is None:
            raise ProductionOrchestrationError(
                f"generated asset {asset_id} has no PRODUCTION_IMAGE_PLAN entry"
            )
        output_path = str(event.get("output_path", ""))
        output_sha = str(event.get("output_sha256", ""))
        diagnostics = verify_technical_diagnostics(root, output_path, output_sha)
        existing = asset_map.get(asset_id)
        if existing is not None and existing.get("path") == output_path and existing.get("file_sha256") == output_sha:
            continue
        now = _utc_now()
        entry = {
            "asset_id": asset_id,
            "asset_family": str(plan_entry["asset_family"]),
            "visual_function": str(plan_entry["visual_function"]),
            "origin": "production_generation",
            "status": "pending_judge",
            "path": output_path,
            "file_sha256": output_sha,
            "covenant_approval_sha256": approval_sha,
            "provenance": {
                "provider": str(event.get("provider", "")),
                "tool_call_id": str(event.get("tool_call_id", "")),
            },
            "promoted_at": now,
            "generation_attempt": attempt,
            "generation_diagnostics": diagnostics,
        }
        if existing is not None:
            new_assets: list[dict[str, Any]] = []
            for item in assets:
                if str(item.get("asset_id")) == asset_id:
                    new_assets.append(entry)
                else:
                    new_assets.append(item)
            assets = new_assets
            asset_map[asset_id] = entry
        else:
            assets.append(entry)
            asset_map[asset_id] = entry
        changed += 1
    if changed:
        _write_catalog(root, assets, approval_sha)
    return changed


def _ensure_judge_actions_locked(project: Path) -> int:
    """Register judge_visual_asset actions for every verified pending_judge asset."""
    root = project.expanduser().resolve()
    release_id = _release_id(root)
    approval_sha = _covenant_approval_sha(root)
    assets = _read_catalog_raw(root)
    state = read_ledgers(root)
    action_keys = {str(item.get("idempotency_key")) for item in state["actions"]}
    terminal_keys = {str(item.get("idempotency_key")) for item in state["events"]}
    registered = 0
    for entry in assets:
        if entry.get("status") != "pending_judge":
            continue
        asset_id = str(entry.get("asset_id", ""))
        attempt = int(entry.get("generation_attempt", 1))
        key = f"{release_id}:JUDGE_{asset_id}:attempt-{attempt}"
        if key in action_keys or key in terminal_keys:
            continue
        bound_paragraph = str(plan_bound_paragraph(root, asset_id))
        action = {
            "schema_version": "host-agent-action.v2",
            "action_id": f"JUDGE_{asset_id}_{attempt:04d}",
            "release_id": release_id,
            "project_id": root.name,
            "action_type": "judge_visual_asset",
            "idempotency_key": key,
            "attempt": attempt,
            "inputs": [{
                "kind": "asset",
                "asset_id": asset_id,
                "path": str(entry.get("path", "")),
                "sha256": str(entry.get("file_sha256", "")),
            }],
            "expected_output": {
                "covenant_approval_sha256": approval_sha,
                "visual_paragraph_sha256": _visual_paragraph_sha(root, bound_paragraph),
                "judge_policy_sha256": judge_policy_sha256(),
                "asset_id": asset_id,
                "asset_sha256": str(entry.get("file_sha256", "")),
            },
            "max_runtime_seconds": GENERATE_RUNTIME_SECONDS,
        }
        result = register_action(root, action)
        if result.get("registered"):
            registered += 1
    return registered


def ensure_judge_actions(project: Path) -> int:
    with project_transaction_lock(project) as root:
        try:
            reconcile_prepared_completions(root)
        except HostActionError as error:
            raise ProductionOrchestrationError(str(error)) from error
        return _ensure_judge_actions_locked(root)


def plan_bound_paragraph(root: Path, asset_id: str) -> str:
    """Return the bound paragraph id for a production asset from the image plan."""
    plan = _load_plan(root)
    if plan is None:
        raise ProductionOrchestrationError("PRODUCTION_IMAGE_PLAN is missing")
    for item in plan.get("assets", []):
        if str(item.get("asset_id")) == asset_id:
            return str(item["bound_paragraph_id"])
    raise ProductionOrchestrationError(f"PRODUCTION_IMAGE_PLAN has no asset {asset_id}")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.parent / f".{path.name}.staging"
    bytes_value = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    staging.write_bytes(bytes_value)
    os.replace(staging, path)


def _load_verdict(
    root: Path,
    action: dict[str, Any],
    event: dict[str, Any],
    entry: dict[str, Any],
    asset_id: str,
    attempt: int,
) -> dict[str, Any]:
    """Load a judge verdict, re-verify its file/hash, and bind every required hash."""
    output_path = str(event.get("output_path", ""))
    target = safe_project_output(root, Path(output_path))
    if target.is_symlink() or not target.is_file():
        raise ProductionOrchestrationError(f"judge verdict is missing or symlinked: {output_path}")
    if sha256_file(target) != str(event.get("output_sha256", "")):
        raise ProductionOrchestrationError(f"judge verdict hash is stale: {output_path}")
    try:
        verdict = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProductionOrchestrationError(f"judge verdict is unreadable: {error}") from error
    if not isinstance(verdict, dict):
        raise ProductionOrchestrationError("judge verdict must be a JSON object")
    errors = sorted(_judge_validator().iter_errors(verdict), key=lambda item: str(item.message))
    if errors:
        raise ProductionOrchestrationError(
            f"judge verdict failed validation at {_schema_error(errors[0])}: {errors[0].message}"
        )
    expected = action.get("expected_output") or {}
    bound_paragraph = plan_bound_paragraph(root, asset_id)
    required = {
        "asset_id": str(asset_id),
        "asset_sha256": str(entry.get("file_sha256", "")),
        "covenant_approval_sha256": str(expected.get("covenant_approval_sha256", "")),
        "visual_paragraph_sha256": _visual_paragraph_sha(root, bound_paragraph),
        "judge_policy_sha256": judge_policy_sha256(),
    }
    for field, expected_value in required.items():
        if str(verdict.get(field, "")) != str(expected_value):
            raise ProductionOrchestrationError(
                f"judge verdict {field} binding does not match the current contract"
            )
    if str(verdict.get("action_id", "")) != str(action.get("action_id", "")):
        raise ProductionOrchestrationError("judge verdict action_id does not match the registered action")
    return verdict


def _find_fallback(assets: list[dict[str, Any]], entry: dict[str, Any]) -> dict[str, Any] | None:
    family = str(entry.get("asset_family", ""))
    func = str(entry.get("visual_function", ""))
    asset_id = str(entry.get("asset_id", ""))
    candidates = [
        item
        for item in assets
        if isinstance(item, dict)
        and item.get("status") == "approved_production_asset"
        and str(item.get("asset_id", "")) != asset_id
        and str(item.get("asset_family", "")) == family
        and str(item.get("visual_function", "")) == func
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: str(item.get("asset_id", "")))[0]


def _rewrite_decisions_generate_to_reuse(
    root: Path, failed_asset_id: str, fallback_asset_id: str
) -> None:
    decisions_path = safe_project_output(root, Path(EDIT_DECISIONS_REL))
    if decisions_path.is_symlink() or not decisions_path.is_file():
        raise ProductionOrchestrationError("EDIT_DECISIONS is missing or symlinked")
    try:
        raw = json.loads(decisions_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProductionOrchestrationError(f"EDIT_DECISIONS is unreadable: {error}") from error
    if not isinstance(raw, dict):
        raise ProductionOrchestrationError("EDIT_DECISIONS must be a JSON object")
    changed = False
    decisions = raw.get("decisions")
    if isinstance(decisions, list):
        for item in decisions:
            if (
                isinstance(item, dict)
                and item.get("decision") == "generate_new"
                and str(item.get("asset_id", "")) == failed_asset_id
            ):
                item["decision"] = "reuse_asset"
                item["asset_id"] = fallback_asset_id
                changed = True
    if not changed:
        raise ProductionOrchestrationError(f"no generate_new decision references {failed_asset_id}")
    validate_edit_decisions(raw)
    _atomic_json(decisions_path, raw)


def _apply_verdict(
    root: Path,
    assets: list[dict[str, Any]],
    entry: dict[str, Any],
    verdict: dict[str, Any],
    attempt: int,
) -> dict[str, Any]:
    asset_id = str(entry.get("asset_id", ""))
    outcome = verdict["verdict"]
    updated = dict(entry)
    judge_record = {
        "verdict": outcome,
        "judge_model": str(verdict.get("judge_model", "")),
        "judge_call_id": str(verdict.get("judge_call_id", "")),
        "judge_policy_sha256": str(verdict.get("judge_policy_sha256", "")),
        "judged_at": _utc_now(),
    }
    if outcome in {"pass", "warning"}:
        updated["status"] = "approved_production_asset"
        updated["warnings"] = (
            [str(item) for item in verdict.get("warnings", [])] if outcome == "warning" else []
        )
        updated["judge"] = judge_record
        updated.pop("pending_retry", None)
        return updated

    retry_strategy = verdict.get("retry_strategy")
    can_retry = (
        attempt < MAX_ATTEMPTS
        and isinstance(retry_strategy, dict)
        and bool(retry_strategy)
    )
    if can_retry:
        updated["status"] = "pending_generation"
        # generation_attempt stays the attempt that produced the current rejected
        # file; ensure_generate_actions derives the next attempt as +1.
        updated["generation_attempt"] = attempt
        updated["pending_retry"] = {
            "retry_strategy": retry_strategy,
            "reason": str(verdict.get("reason", "")),
            "hard_failures": [str(item) for item in verdict.get("hard_failures", [])],
            "retry_of_attempt": attempt,
        }
        return updated

    updated["status"] = "blocked_quality_failure"
    updated["blocked_reason"] = str(verdict.get("reason", ""))
    updated["hard_failures"] = [str(item) for item in verdict.get("hard_failures", [])]
    updated["judge"] = judge_record
    fallback = _find_fallback(assets, entry)
    if fallback is not None:
        fallback_asset_id = str(fallback.get("asset_id", ""))
        _rewrite_decisions_generate_to_reuse(root, asset_id, fallback_asset_id)
        updated["fallback_asset_id"] = fallback_asset_id
        updated["fallback_note"] = "approved compatible asset reused via EDIT_DECISIONS rewrite"
    return updated


def _apply_verdicts_locked(project: Path) -> int:
    """Attach terminal judge verdicts to pending_judge catalog assets (idempotent)."""
    root = project.expanduser().resolve()
    release_id = _release_id(root)
    approval_sha = _covenant_approval_sha(root)
    assets = _read_catalog_raw(root)
    state = read_ledgers(root)
    events_by_key = {str(item.get("idempotency_key")): item for item in state["events"]}
    actions_by_key = {str(item.get("idempotency_key")): item for item in state["actions"]}
    changed = 0
    for idx, entry in enumerate(assets):
        if entry.get("status") != "pending_judge":
            continue
        asset_id = str(entry.get("asset_id", ""))
        attempt = int(entry.get("generation_attempt", 1))
        key = f"{release_id}:JUDGE_{asset_id}:attempt-{attempt}"
        event = events_by_key.get(key)
        if event is None or event.get("status") != "succeeded":
            continue
        action = actions_by_key.get(key)
        if action is None or action.get("action_type") != "judge_visual_asset":
            raise ProductionOrchestrationError(f"judge event has no registered action: {key}")
        verdict = _load_verdict(root, action, event, entry, asset_id, attempt)
        assets[idx] = _apply_verdict(root, assets, entry, verdict, attempt)
        changed += 1
    if changed:
        _write_catalog(root, assets, approval_sha)
    return changed


def apply_verdicts(project: Path) -> int:
    """Reconcile prepared completions before applying judge results to the catalog."""
    with project_transaction_lock(project) as root:
        try:
            reconcile_prepared_completions(root)
        except HostActionError as error:
            raise ProductionOrchestrationError(str(error)) from error
        return _apply_verdicts_locked(root)


def _production_status_locked(project: Path) -> dict[str, Any] | None:
    """Advance the Host generation/judge loop and report the V2 production state.

    Returns None when the Director edit decisions are not yet present, so the
    legacy pipeline status path remains unchanged below the Director gate.
    """
    root = project.expanduser().resolve()
    decisions_path = safe_project_output(root, Path(EDIT_DECISIONS_REL))
    if decisions_path.is_symlink() or not decisions_path.is_file():
        return None
    status = _verify_asset_catalog_safe(root)
    release_id = _release_id(root)
    apply_verdicts(root)
    incorporate_generated_assets(root)
    ensure_generate_actions(root)
    ensure_judge_actions(root)
    action = derive_next_action(root)
    if action is not None:
        return {
            "release_id": release_id,
            "stage": "host_orchestration",
            "status": "host_action_pending",
            "human_review_required": False,
            "next_action": f"execute Host action {action['action_id']} and register its terminal event",
            "command": None,
            "host_action": action,
        }
    assets = _read_catalog_raw(root)
    # A hard failure resolved by a compatible approved fallback (recorded via
    # fallback_asset_id and an EDIT_DECISIONS rewrite) is not a pipeline block.
    blocked = [
        item
        for item in assets
        if item.get("status") == "blocked_quality_failure"
        and not item.get("fallback_asset_id")
    ]
    if blocked:
        return {
            "release_id": release_id,
            "stage": "production_quality",
            "status": "blocked_quality_failure",
            "human_review_required": False,
            "next_action": "resolve the blocked production asset or author a compatible reuse before rendering",
            "command": None,
            "blocked_asset_ids": [item.get("asset_id") for item in blocked],
        }
    pending = [
        item for item in assets if item.get("status") in {"pending_generation", "pending_judge"}
    ]
    if pending:
        return {
            "release_id": release_id,
            "stage": "host_orchestration",
            "status": "awaiting_host_generation",
            "human_review_required": False,
            "next_action": "await the next logical Host generation or judge action",
            "command": None,
        }
    return {
        "release_id": release_id,
        "stage": "asset_catalog",
        "status": "asset_catalog_ready",
        "human_review_required": False,
        "next_action": "production assets approved; resolve the EDIT_TIMELINE for rendering",
        "command": None,
    }


def production_status(project: Path) -> dict[str, Any] | None:
    """Reconcile prepared completions before consuming production catalogs."""
    with project_transaction_lock(project) as root:
        try:
            reconcile_prepared_completions(root)
        except HostActionError as error:
            raise ProductionOrchestrationError(str(error)) from error
        return _production_status_locked(root)
