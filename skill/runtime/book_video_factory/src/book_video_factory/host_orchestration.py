"""V2 Host orchestration ledgers: validate, register, verify, derive."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from book_video_factory.locked_script import verify_locked_script
from book_video_factory.manifests import safe_project_output, sha256_file


class HostActionError(ValueError):
    """A V2 host action or event is invalid or violates project boundaries."""


MAX_ATTEMPTS = 3
ACTION_TYPES = {"generate_image", "judge_visual_asset", "select_bgm", "judge_bgm_fit"}
_ACTIONS_REL = "manifests/host_orchestration/ACTIONS.v2.jsonl"
_EVENTS_REL = "manifests/host_orchestration/EVENTS.v2.jsonl"
_HASH = re.compile(r"^[0-9a-f]{64}$")
_ATTEMPT = re.compile(r"^(?P<task>.+):attempt-(?P<attempt>\d+)$")


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            raise HostActionError(f"ledger is corrupt, not JSON: {path}")
        if not isinstance(record, dict):
            raise HostActionError(f"ledger record is not an object: {path}")
        records.append(record)
    return records


def _str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise HostActionError(f"{label} must be a nonempty trimmed string")
    return value


def _int(value: Any, label: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise HostActionError(f"{label} must be an integer >= {minimum}")
    return value


def validate_host_action(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HostActionError("host action must be an object")
    if payload.get("schema_version") != "host-agent-action.v2":
        raise HostActionError("unsupported host action schema_version")
    action_type = _str(payload.get("action_type"), "action_type")
    if action_type not in ACTION_TYPES:
        raise HostActionError(f"unknown action_type: {action_type}")
    _str(payload.get("action_id"), "action_id")
    _str(payload.get("release_id"), "release_id")
    _str(payload.get("project_id"), "project_id")
    key = _str(payload.get("idempotency_key"), "idempotency_key")
    _int(payload.get("attempt"), "attempt")
    _int(payload.get("max_runtime_seconds"), "max_runtime_seconds")
    inputs = payload.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        raise HostActionError("inputs must be a nonempty list")
    for item in inputs:
        if not isinstance(item, dict) or not isinstance(item.get("kind"), str) or not item["kind"]:
            raise HostActionError("each input must carry a nonempty kind")
        for field, value in (("sha256", item.get("sha256")),):
            if value is not None and _HASH.fullmatch(str(value)) is None:
                raise HostActionError(f"input {field} must be a sha256")
    expected_output = payload.get("expected_output")
    if not isinstance(expected_output, dict):
        raise HostActionError("expected_output must be an object")
    if action_type == "judge_visual_asset":
        for field in (
            "covenant_approval_sha256",
            "visual_paragraph_sha256",
            "judge_policy_sha256",
            "asset_id",
            "asset_sha256",
        ):
            value = expected_output.get(field)
            if not isinstance(value, str) or not value:
                raise HostActionError(f"judge_visual_asset expected_output.{field} is required")
            if field.endswith("_sha256") and _HASH.fullmatch(value) is None:
                raise HostActionError(f"expected_output.{field} must be a sha256")
    return dict(payload)


def validate_host_event(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HostActionError("host event must be an object")
    if payload.get("schema_version") != "host-agent-event.v2":
        raise HostActionError("unsupported host event schema_version")
    _str(payload.get("action_id"), "action_id")
    _str(payload.get("event_type"), "event_type")
    status = _str(payload.get("status"), "status")
    if status not in {"succeeded", "failed"}:
        raise HostActionError("status must be succeeded or failed")
    _str(payload.get("idempotency_key"), "idempotency_key")
    _str(payload.get("provider"), "provider")
    _str(payload.get("tool_call_id"), "tool_call_id")
    if status == "succeeded":
        if not isinstance(payload.get("output_path"), str) or not payload["output_path"]:
            raise HostActionError("succeeded events require output_path")
        if _HASH.fullmatch(str(payload.get("output_sha256", ""))) is None:
            raise HostActionError("succeeded events require output_sha256")
    else:
        for field in ("failure_class", "retryable", "has_registered_output"):
            if field not in payload:
                raise HostActionError(f"failed events require {field}")
        if not isinstance(payload.get("failure_class"), str) or not payload["failure_class"]:
            raise HostActionError("failure_class must be a nonempty string")
        if not isinstance(payload.get("retryable"), bool):
            raise HostActionError("retryable must be a boolean")
        if not isinstance(payload.get("has_registered_output"), bool):
            raise HostActionError("has_registered_output must be a boolean")
    return dict(payload)


def _action_paths(root: Path) -> tuple[Path, Path]:
    actions = safe_project_output(root, Path(_ACTIONS_REL))
    events = safe_project_output(root, Path(_EVENTS_REL))
    return actions, events


def _parse_key(key: str) -> tuple[str, int]:
    match = _ATTEMPT.fullmatch(key)
    if match is None:
        return key, 1
    return match.group("task"), int(match.group("attempt"))


def register_action(project: Path, action: dict[str, Any]) -> dict[str, Any]:
    valid = validate_host_action(action)
    root = project.expanduser().resolve()
    if _str(valid.get("project_id"), "project_id") != root.name:
        raise HostActionError("action project_id does not match the project root")
    actions, _ = _action_paths(root)
    existing = _jsonl(actions)
    key = valid["idempotency_key"]
    if any(item.get("idempotency_key") == key for item in existing):
        return {"registered": False, "status": "unchanged"}
    records = [*existing, valid]
    _atomic_jsonl(actions, records)
    return {"registered": True, "status": "created"}


def _read_owned(root: Path) -> dict[str, Any]:
    actions, events = _action_paths(root)
    return {"actions": _jsonl(actions), "events": _jsonl(events)}


def verify_host_event(project: Path, event: dict[str, Any]) -> dict[str, Any]:
    valid = validate_host_event(event)
    root = project.expanduser().resolve()
    state = _read_owned(root)
    key = valid["idempotency_key"]
    actions = [
        item
        for item in state["actions"]
        if item.get("idempotency_key") == key
    ]
    if not actions:
        raise HostActionError(f"no host action registered for idempotency_key: {key}")
    if len(actions) > 1:
        raise HostActionError(f"ambiguous host actions for idempotency_key: {key}")
    action = actions[0]
    if valid["action_id"] != action["action_id"]:
        raise HostActionError("event action_id does not match the registered action")
    if _str(valid.get("idempotency_key"), "idempotency_key") != key:
        raise HostActionError("event idempotency_key does not match the registered action")
    terminal = [item for item in state["events"] if item.get("idempotency_key") == key]
    if terminal:
        raise HostActionError("a terminal event already exists for this action")
    if valid["status"] == "succeeded":
        try:
            output_path = safe_project_output(root, Path(valid["output_path"]))
        except (OSError, ValueError) as error:
            raise HostActionError(f"succeeded event output is not project-local: {error}") from error
        if output_path.is_symlink() or not output_path.is_file():
            raise HostActionError("succeeded event output is missing or symlinked")
        try:
            actual = sha256_file(output_path)
        except OSError as error:
            raise HostActionError(f"succeeded event output cannot be hashed: {error}") from error
        if actual != valid["output_sha256"]:
            raise HostActionError("succeeded event output sha256 does not match the file")
    return {
        "action": action,
        "event_type": valid["event_type"],
        "status": valid["status"],
        "has_registered_output": bool(valid.get("has_registered_output")),
    }


def register_host_event(project: Path, event: dict[str, Any]) -> dict[str, Any]:
    valid = validate_host_event(event)
    root = project.expanduser().resolve()
    _, events = _action_paths(root)
    existing = _jsonl(events)
    key = valid["idempotency_key"]
    if any(item.get("idempotency_key") == key for item in existing):
        return {"registered": False, "status": "unchanged"}
    verify_host_event(project, valid)
    _atomic_jsonl(events, [*existing, valid])
    return {"registered": True, "status": "recorded"}


def derive_next_action(project: Path, manifest_path: Path | None = None) -> dict[str, Any] | None:
    root = project.expanduser().resolve()
    lock_status = verify_locked_script(root)
    if lock_status.get("status") != "script_locked":
        return None
    state = _read_owned(root)
    actions = state["actions"]
    events = state["events"]
    terminal_keys = {item["idempotency_key"] for item in events}
    for action in actions:
        key = action["idempotency_key"]
        if key not in terminal_keys:
            return dict(action)
    pending = None
    failed_note = None
    for event in reversed(events):
        key = event["idempotency_key"]
        if event["status"] != "failed" or not event.get("retryable"):
            continue
        task, attempt = _parse_key(key)
        if attempt >= MAX_ATTEMPTS:
            continue
        for candidate in reversed(actions):
            if candidate["idempotency_key"] == key:
                pending = dict(candidate)
                failed_note = {
                    "failure_class": event.get("failure_class"),
                    "retryable": event.get("retryable"),
                    "has_registered_output": event.get("has_registered_output"),
                }
                break
        if pending is not None:
            break
    if pending is None:
        return None
    task, attempt = _parse_key(pending["idempotency_key"])
    next_attempt = attempt + 1
    retry = dict(pending)
    retry["idempotency_key"] = f"{task}:attempt-{next_attempt}"
    retry["attempt"] = next_attempt
    retry["action_id"] = f"{retry['action_id']}-a{next_attempt}"
    retry["retry_of"] = pending.get("action_id")
    retry["previous_failure"] = failed_note
    return retry


def _atomic_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    bytes_value = b"".join(
        json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        for item in records
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.parent / f".{path.name}.staging"
    staging.write_bytes(bytes_value)
    os.replace(staging, path)
