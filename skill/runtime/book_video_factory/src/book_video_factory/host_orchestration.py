"""V2 Host orchestration ledgers: validate, register, verify, derive."""
from __future__ import annotations

import json
import hashlib
import os
import re
import base64
import binascii
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from book_video_factory.locked_script import verify_locked_script
from book_video_factory.manifests import safe_project_output, sha256_file
from book_video_factory.transaction_lock import project_transaction_lock


class HostActionError(ValueError):
    """A V2 host action or event is invalid or violates project boundaries."""


MAX_ATTEMPTS = 3
ACTION_TYPES = {
    "plan_visual_covenant",
    "generate_image",
    "judge_visual_asset",
    "select_bgm",
    "judge_bgm_fit",
    "plan_literary_director",
}
_ACTIONS_REL = "manifests/host_orchestration/ACTIONS.v2.jsonl"
_EVENTS_REL = "manifests/host_orchestration/EVENTS.v2.jsonl"
_COMPLETIONS_REL = "manifests/host_orchestration/completions"
_HASH = re.compile(r"^[0-9a-f]{64}$")
_ATTEMPT = re.compile(r"^(?P<task>.+):attempt-(?P<attempt>\d+)$")


@dataclass(frozen=True)
class CompletionPlan:
    completion_id: str
    action: dict[str, Any]
    event: dict[str, Any]
    artifacts: tuple[dict[str, Any], ...]
    actions: tuple[dict[str, Any], ...]


def _failure_classification(event: dict[str, Any]) -> str:
    failure_class = str(event.get("failure_class", "")).strip().lower()
    if failure_class == "provider_transient" or failure_class.startswith(("provider_", "transport_")):
        return "provider_transient"
    if failure_class == "generation_quality_failure" or "quality" in failure_class:
        return "generation_quality_failure"
    return "contract_integrity_failure"


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


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def register_action(project: Path, action: dict[str, Any]) -> dict[str, Any]:
    valid = validate_host_action(action)
    with project_transaction_lock(project) as root:
        if _str(valid.get("project_id"), "project_id") != root.name:
            raise HostActionError("action project_id does not match the project root")
        actions, _ = _action_paths(root)
        existing = _jsonl(actions)
        key = valid["idempotency_key"]
        matching = [item for item in existing if item.get("idempotency_key") == key]
        if matching:
            if _canonical_bytes(matching[0]) != _canonical_bytes(valid):
                raise HostActionError("idempotency_key is already bound to different action bytes")
            return {"registered": False, "status": "unchanged"}
        records = [*existing, valid]
        _atomic_jsonl(actions, records)
        return {"registered": True, "status": "created"}


def _read_owned(root: Path) -> dict[str, Any]:
    actions, events = _action_paths(root)
    return {"actions": _jsonl(actions), "events": _jsonl(events)}


def read_ledgers(project: Path) -> dict[str, Any]:
    """Return the raw actions/events ledgers for a V2 project (fail-closed on corruption)."""
    root = project.expanduser().resolve()
    return _read_owned(root)


def _verify_host_event_without_commit(
    root: Path,
    event: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    valid = validate_host_event(event)
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
    if valid["event_type"] != action["action_type"]:
        raise HostActionError("event_type does not match the registered action_type")
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
            raise HostActionError("succeeded event output hash is stale or does not match the file")
    return {
        "action": action,
        "event_type": valid["event_type"],
        "status": valid["status"],
        "has_registered_output": bool(valid.get("has_registered_output")),
    }


def verify_host_event_without_commit(project: Path, event: dict[str, Any]) -> dict[str, Any]:
    """Validate a Host event and its output without mutating any ledger."""
    with project_transaction_lock(project) as root:
        return _verify_host_event_without_commit(root, event, _read_owned(root))


def verify_host_event(project: Path, event: dict[str, Any]) -> dict[str, Any]:
    """Backward-compatible name for the non-committing validation path."""
    return verify_host_event_without_commit(project, event)


def register_host_event(project: Path, event: dict[str, Any]) -> dict[str, Any]:
    valid = validate_host_event(event)
    if valid["status"] == "succeeded":
        result = complete_host_action(project, valid)
        return {
            **result,
            "registered": result.get("status") == "committed",
            "status": "recorded" if result.get("status") == "committed" else "unchanged",
        }
    with project_transaction_lock(project) as root:
        state = _read_owned(root)
        key = valid["idempotency_key"]
        existing = [item for item in state["events"] if item.get("idempotency_key") == key]
        if existing:
            if _canonical_bytes(existing[0]) != _canonical_bytes(valid):
                raise HostActionError("idempotency_key is already bound to different event bytes")
            return {"registered": False, "status": "unchanged"}
        _verify_host_event_without_commit(root, valid, state)
        _, events = _action_paths(root)
        _atomic_jsonl(events, [*state["events"], valid])
        return {"registered": True, "status": "recorded"}


def derive_next_action(project: Path, manifest_path: Path | None = None) -> dict[str, Any] | None:
    with project_transaction_lock(project) as root:
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
            classification = _failure_classification(event)
            if classification == "contract_integrity_failure":
                continue
            if classification == "generation_quality_failure":
                strategy = event.get("retry_strategy")
                if not isinstance(strategy, dict) or not strategy:
                    continue
            task, attempt = _parse_key(key)
            if attempt >= MAX_ATTEMPTS:
                continue
            for candidate in reversed(actions):
                if candidate["idempotency_key"] == key:
                    pending = dict(candidate)
                    failed_note = {
                        "classification": classification,
                        "failure_class": event.get("failure_class"),
                        "retryable": event.get("retryable"),
                        "has_registered_output": event.get("has_registered_output"),
                    }
                    if isinstance(event.get("retry_strategy"), dict):
                        failed_note["retry_strategy"] = dict(event["retry_strategy"])
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
        retry["retry_classification"] = failed_note["classification"]
        retry["retry_log"] = {
            "retry_of": pending.get("action_id"),
            "previous_failure": failed_note,
            "attempt": next_attempt,
        }
        if failed_note["classification"] == "generation_quality_failure":
            strategy = failed_note.get("retry_strategy")
            strategy_bytes = _canonical_bytes(strategy)
            retry["retry_strategy"] = strategy
            retry["inputs"] = [
                *list(retry.get("inputs", [])),
                {
                    "kind": "retry_strategy",
                    "sha256": _sha_bytes(strategy_bytes),
                },
            ]
        register_action(root, retry)
        return retry


def _completion_root(root: Path) -> Path:
    return safe_project_output(root, Path(_COMPLETIONS_REL))


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)


def _completion_dir(root: Path, action_id: str, completion_id: str) -> Path:
    return _completion_root(root) / _safe_component(action_id) / _safe_component(completion_id)


def _atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # The project lock serializes all writers.  Keep the staging name short so
    # Windows MAX_PATH cannot turn a valid completion directory into a false
    # missing-file error.
    staging = path.parent / ".transaction.staging"
    if staging.is_file() and staging.read_bytes() != value:
        raise HostActionError(f"staging path contains conflicting bytes: {staging}")
    staging.write_bytes(value)
    os.replace(staging, path)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    _atomic_bytes(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n")


def _completion_payload(event: dict[str, Any], field: str) -> list[Any]:
    direct = event.get(field)
    if direct is not None:
        return list(direct) if isinstance(direct, list) else []
    nested = event.get("completion")
    if isinstance(nested, dict) and isinstance(nested.get(field.removeprefix("completion_")), list):
        return list(nested[field.removeprefix("completion_")])
    return []


def _artifact_bytes(root: Path, item: Any) -> bytes:
    if not isinstance(item, dict):
        raise HostActionError("completion artifact must be an object")
    if "content" in item:
        content = item["content"]
        if isinstance(content, bytes):
            return content
        if isinstance(content, (dict, list, int, float, bool)) or content is None:
            return _canonical_bytes(content) + b"\n"
        if isinstance(content, str):
            return content.encode("utf-8")
    if "bytes_base64" in item and isinstance(item["bytes_base64"], str):
        try:
            return base64.b64decode(item["bytes_base64"], validate=True)
        except (ValueError, binascii.Error) as error:
            raise HostActionError("completion artifact bytes_base64 is invalid") from error
    source = item.get("source_path")
    if isinstance(source, str) and source:
        try:
            source_path = safe_project_output(root, Path(source))
        except (OSError, ValueError) as error:
            raise HostActionError(f"completion artifact source is not project-local: {error}") from error
        if source_path.is_symlink() or not source_path.is_file():
            raise HostActionError("completion artifact source is missing or symlinked")
        return source_path.read_bytes()
    raise HostActionError("completion artifact requires content, bytes_base64, or source_path")


def _build_completion_plan(root: Path, event: dict[str, Any], state: dict[str, Any]) -> CompletionPlan:
    verified = _verify_host_event_without_commit(root, event, state)
    action = verified["action"]
    artifact_inputs = _completion_payload(event, "completion_artifacts")
    artifacts: list[dict[str, Any]] = []
    target_hashes: list[dict[str, str]] = []
    seen_targets: set[str] = set()
    for index, item in enumerate(artifact_inputs):
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not item["path"]:
            raise HostActionError("completion artifact requires a project-relative path")
        try:
            target = safe_project_output(root, Path(item["path"]))
        except (OSError, ValueError) as error:
            raise HostActionError(f"completion artifact path is not project-local: {error}") from error
        relative_target = Path(item["path"]).as_posix()
        if relative_target in {_ACTIONS_REL, _EVENTS_REL}:
            raise HostActionError("completion artifacts cannot directly target Host ledgers")
        if relative_target in seen_targets:
            raise HostActionError(f"completion artifact target is duplicated: {relative_target}")
        seen_targets.add(relative_target)
        if target.is_symlink():
            raise HostActionError(f"completion artifact target is symlinked: {item['path']}")
        value = _artifact_bytes(root, item)
        expected = item.get("sha256")
        actual = _sha_bytes(value)
        if expected is not None and expected != actual:
            raise HostActionError(f"completion artifact sha256 mismatch: {item['path']}")
        staged_name = f"artifact-{index:04d}.bin"
        artifacts.append({
            "path": relative_target,
            "sha256": actual,
            "staged_name": staged_name,
            "bytes": value,
        })
        target_hashes.append({"kind": "artifact", "path": relative_target, "sha256": actual})

    completion_actions: list[dict[str, Any]] = []
    for item in _completion_payload(event, "completion_actions"):
        valid_action = validate_host_action(item)
        if valid_action["project_id"] != root.name:
            raise HostActionError("completion action project_id does not match the project root")
        completion_actions.append(valid_action)
        target_hashes.append({
            "kind": "action",
            "idempotency_key": valid_action["idempotency_key"],
            "sha256": _sha_bytes(_canonical_bytes(valid_action)),
        })
    target_hashes.sort(key=_canonical_bytes)
    seed = {
        "action_id": action["action_id"],
        "idempotency_key": action["idempotency_key"],
        "event_output_sha256": event.get("output_sha256", ""),
        "target_hashes": target_hashes,
    }
    completion_id = f"COMP_{_sha_bytes(_canonical_bytes(seed))[:40]}"
    return CompletionPlan(
        completion_id=completion_id,
        action=action,
        event=dict(event),
        artifacts=tuple(artifacts),
        actions=tuple(completion_actions),
    )


def _journal_payload(plan: CompletionPlan) -> dict[str, Any]:
    artifacts = [
        {key: value for key, value in item.items() if key != "bytes"}
        for item in plan.artifacts
    ]
    return {
        "schema_version": "host-completion.v2",
        "completion_id": plan.completion_id,
        "transaction_id": plan.completion_id,
        "action_id": plan.action["action_id"],
        "idempotency_key": plan.action["idempotency_key"],
        "event": plan.event,
        "artifacts": artifacts,
        "actions": list(plan.actions),
        "state": "prepared",
    }


def _journal_path_for_plan(root: Path, plan: CompletionPlan) -> Path:
    return _completion_dir(root, plan.action["action_id"], plan.completion_id) / "JOURNAL.json"


def _load_journal(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise HostActionError(f"completion journal is missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HostActionError(f"completion journal is corrupt: {path}") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != "host-completion.v2":
        raise HostActionError(f"completion journal schema is invalid: {path}")
    return payload


def _find_completion_for_event(root: Path, event: dict[str, Any]) -> dict[str, Any] | None:
    completions = _completion_root(root)
    if not completions.is_dir():
        return None
    for journal_path in sorted(completions.glob("*/COMP_*/JOURNAL.json")):
        payload = _load_journal(journal_path)
        if _canonical_bytes(payload.get("event")) == _canonical_bytes(event):
            payload["journal_path"] = journal_path.relative_to(root).as_posix()
            return payload
    return None


def _publish_prepared_artifacts(root: Path, journal_dir: Path, journal: dict[str, Any], source_bytes: dict[str, bytes]) -> None:
    for item in journal.get("artifacts", []):
        target = safe_project_output(root, Path(str(item["path"])))
        expected = str(item["sha256"])
        if target.is_file() and not target.is_symlink():
            if _sha_bytes(target.read_bytes()) != expected:
                raise HostActionError(f"completion target conflicts with journal: {item['path']}")
            continue
        staged = journal_dir / "staged" / str(item["staged_name"])
        value = source_bytes.get(str(item["staged_name"]))
        if value is None:
            if staged.is_symlink() or not staged.is_file():
                raise HostActionError(f"completion staged bytes are missing: {staged}")
            value = staged.read_bytes()
        if _sha_bytes(value) != expected:
            raise HostActionError(f"completion staged bytes hash mismatch: {staged}")
        _atomic_bytes(target, value)


def _prepare_host_completion_locked(root: Path, event: dict[str, Any]) -> dict[str, Any]:
    valid = validate_host_event(event)
    state = _read_owned(root)
    key = valid["idempotency_key"]
    existing_event = next((item for item in state["events"] if item.get("idempotency_key") == key), None)
    if existing_event is not None:
        if _canonical_bytes(existing_event) != _canonical_bytes(valid):
            raise HostActionError("idempotency_key is already bound to different event bytes")
        journal = _find_completion_for_event(root, valid)
        return {
            "status": "unchanged",
            "completion_id": journal.get("completion_id") if journal else None,
            "journal_path": str(journal.get("journal_path", "")) if journal else "",
        }
    existing_journal = _find_completion_for_event(root, valid)
    if existing_journal is not None:
        journal_path = root / str(existing_journal["journal_path"])
        _publish_prepared_artifacts(root, journal_path.parent, existing_journal, {})
        return {
            "status": "prepared" if existing_journal.get("state") == "prepared" else "unchanged",
            "completion_id": str(existing_journal["completion_id"]),
            "journal_path": str(existing_journal["journal_path"]),
            "artifact_sha256s": [str(item["sha256"]) for item in existing_journal.get("artifacts", [])],
        }
    plan = _build_completion_plan(root, valid, state)
    action_dir = _completion_root(root) / _safe_component(plan.action["action_id"])
    if action_dir.is_dir():
        for candidate_path in sorted(action_dir.glob("COMP_*/JOURNAL.json")):
            candidate = _load_journal(candidate_path)
            if candidate.get("idempotency_key") == plan.action["idempotency_key"]:
                if _canonical_bytes(candidate.get("event")) != _canonical_bytes(valid):
                    raise HostActionError(
                        "one registered action cannot have two prepared Host event results"
                    )
    journal_path = _journal_path_for_plan(root, plan)
    journal_dir = journal_path.parent
    journal = _journal_payload(plan)
    journal_dir.mkdir(parents=True, exist_ok=True)
    staged_dir = journal_dir / "staged"
    staged_dir.mkdir(parents=True, exist_ok=True)
    source_bytes: dict[str, bytes] = {}
    for item in plan.artifacts:
        value = bytes(item["bytes"])
        staged = staged_dir / str(item["staged_name"])
        if staged.is_file() and staged.read_bytes() != value:
            raise HostActionError(f"completion staged bytes conflict: {staged}")
        staged.write_bytes(value)
        source_bytes[str(item["staged_name"])] = value
    if journal_path.is_file():
        existing = _load_journal(journal_path)
        if _canonical_bytes({**existing, "state": "prepared"}) != _canonical_bytes(journal):
            raise HostActionError("completion journal conflicts with deterministic retry")
        journal = existing
    else:
        _atomic_json(journal_path, journal)
    _publish_prepared_artifacts(root, journal_dir, journal, source_bytes)
    result = {
        "status": "prepared",
        "completion_id": str(journal["completion_id"]),
        "journal_path": journal_path.relative_to(root).as_posix(),
        "artifact_sha256s": [str(item["sha256"]) for item in journal.get("artifacts", [])],
    }
    return result


def prepare_host_completion(project: Path, event: dict[str, Any]) -> dict[str, Any]:
    """Prepare and publish staged completion bytes without committing ledgers."""
    with project_transaction_lock(project) as root:
        return _prepare_host_completion_locked(root, event)


def _reconcile_journal_locked(root: Path, journal_path: Path) -> tuple[bool, str]:
    journal = _load_journal(journal_path)
    if journal.get("state") not in {"prepared", "committed"}:
        raise HostActionError(f"completion journal state is invalid: {journal_path}")
    event = validate_host_event(journal.get("event"))
    state = _read_owned(root)
    action_id = str(journal.get("action_id", ""))
    key = str(journal.get("idempotency_key", ""))
    actions = [item for item in state["actions"] if item.get("idempotency_key") == key]
    if not actions:
        actions = [item for item in state["actions"] if item.get("action_id") == action_id]
    if len(actions) != 1:
        raise HostActionError(f"completion action is missing or ambiguous: {action_id}")
    if actions[0]["idempotency_key"] != key:
        raise HostActionError("completion journal action key does not match registered action")
    plan_state = {
        "actions": state["actions"],
        "events": [item for item in state["events"] if item.get("idempotency_key") != event["idempotency_key"]],
    }
    plan = _build_completion_plan(root, event, plan_state)
    journal_artifacts = [
        {key: value for key, value in item.items() if key != "bytes"}
        for item in plan.artifacts
    ]
    actual_artifacts = [
        {key: value for key, value in item.items() if key != "bytes"}
        for item in journal.get("artifacts", [])
    ]
    if (
        journal.get("completion_id") != plan.completion_id
        or actual_artifacts != journal_artifacts
        or journal.get("actions", []) != list(plan.actions)
    ):
        raise HostActionError("completion journal does not match deterministic completion plan")
    journal_dir = journal_path.parent
    _publish_prepared_artifacts(root, journal_dir, journal, {})
    actions_path, events_path = _action_paths(root)
    current_actions = _jsonl(actions_path)
    for candidate in journal.get("actions", []):
        valid_action = validate_host_action(candidate)
        matching = [item for item in current_actions if item.get("idempotency_key") == valid_action["idempotency_key"]]
        if matching and _canonical_bytes(matching[0]) != _canonical_bytes(valid_action):
            raise HostActionError("completion action conflicts with ACTIONS ledger")
        if not matching:
            current_actions.append(valid_action)
    if current_actions != state["actions"]:
        _atomic_jsonl(actions_path, current_actions)
    current_events = _jsonl(events_path)
    event_matches = [item for item in current_events if item.get("idempotency_key") == event["idempotency_key"]]
    if event_matches and _canonical_bytes(event_matches[0]) != _canonical_bytes(event):
        raise HostActionError("completion event conflicts with EVENTS ledger")
    if not event_matches:
        refreshed = _read_owned(root)
        _verify_host_event_without_commit(root, event, refreshed)
        _atomic_jsonl(events_path, [*refreshed["events"], event])
        committed_now = True
    else:
        committed_now = False
    if journal.get("state") != "committed":
        journal["state"] = "committed"
        _atomic_json(journal_path, journal)
        committed_now = True
    return committed_now, str(journal["completion_id"])


def reconcile_prepared_completions(project: Path) -> dict[str, Any]:
    """Recover all prepared completions while holding the project transaction lock."""
    with project_transaction_lock(project) as root:
        completions = _completion_root(root)
        if not completions.is_dir():
            return {"status": "unchanged", "committed_count": 0, "completion_ids": []}
        committed: list[str] = []
        for journal_path in sorted(completions.glob("*/COMP_*/JOURNAL.json")):
            changed, completion_id = _reconcile_journal_locked(root, journal_path)
            if changed:
                committed.append(completion_id)
        return {
            "status": "reconciled" if committed else "unchanged",
            "committed_count": len(committed),
            "completion_ids": committed,
        }


def complete_host_action(project: Path, event: dict[str, Any]) -> dict[str, Any]:
    """Prepare, publish, and finally commit one Host completion transaction."""
    with project_transaction_lock(project) as root:
        # A retry/restart must first settle every older prepared transaction so
        # no downstream read can observe a mixed authoritative state.
        reconcile_prepared_completions(root)
        prepared = _prepare_host_completion_locked(root, event)
        if prepared["status"] == "unchanged":
            return prepared
        journal_path = root / prepared["journal_path"]
        changed, completion_id = _reconcile_journal_locked(root, journal_path)
        return {
            "status": "committed" if changed else "unchanged",
            "completion_id": completion_id,
            "journal_path": prepared["journal_path"],
            "artifact_sha256s": prepared.get("artifact_sha256s", []),
        }


def assert_authoritative_artifact(project: Path, relative_path: str | Path) -> Path:
    """Reconcile prepared transactions before returning a V2 artifact path."""
    root = project.expanduser().resolve()
    reconcile_prepared_completions(root)
    target = safe_project_output(root, Path(relative_path))
    completions = _completion_root(root)
    for journal_path in sorted(completions.glob("*/COMP_*/JOURNAL.json")) if completions.is_dir() else []:
        journal = _load_journal(journal_path)
        if journal.get("state") != "committed":
            if any(item.get("path") == target.relative_to(root).as_posix() for item in journal.get("artifacts", [])):
                raise HostActionError(f"artifact is not authoritative before completion commit: {relative_path}")
    return target


def ensure_authoritative_artifact(project: Path, relative_path: str | Path) -> Path:
    return assert_authoritative_artifact(project, relative_path)


def _atomic_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    bytes_value = b"".join(
        json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        for item in records
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.parent / f".{path.name}.staging"
    staging.write_bytes(bytes_value)
    os.replace(staging, path)
