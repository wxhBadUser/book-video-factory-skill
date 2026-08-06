from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from book_video_factory.manifests import safe_project_output, sha256_file
from book_video_factory.production_visuals.scheduler import _load_current, _refresh_statuses


class GenerationAttemptError(RuntimeError):
    """Generation attempt evidence is invalid or cannot be published atomically."""


@dataclass(frozen=True)
class GenerationAttemptResult:
    attempt: int
    ledger_path: Path
    prompts_path: Path
    run_manifest_path: Path


_OUTCOMES = {"success", "failed", "rejected", "regenerated"}
_ENDPOINT_CLASSES = {"host-imagegen", "system-cli", "openai-compatible"}
_SECRET = re.compile(
    r"(?:\bsk-[A-Za-z0-9_-]{8,}\b|\bbearer\s+[A-Za-z0-9._~-]{8,}|api[_ -]?key\s*[:=])",
    re.IGNORECASE,
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _clean(value: str | None, label: str, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise GenerationAttemptError(f"{label} is required")
        return None
    if not isinstance(value, str) or value != value.strip() or not value:
        raise GenerationAttemptError(f"{label} must be a nonempty trimmed string")
    if _SECRET.search(value):
        raise GenerationAttemptError(f"{label} contains secret-like material")
    return value


def _file_evidence(root: Path, path: Path | None, label: str) -> tuple[str | None, str | None]:
    if path is None:
        return None, None
    try:
        target = safe_project_output(root, path)
    except (OSError, ValueError) as error:
        raise GenerationAttemptError(f"{label} must be a project-local file: {error}") from error
    if target.is_symlink() or not target.is_file() or target.stat().st_size < 1:
        raise GenerationAttemptError(f"{label} is missing, empty, or symlinked")
    return target.relative_to(root).as_posix(), sha256_file(target)


def _scene_manifest(root: Path) -> dict[str, dict[str, Any]]:
    path = root / "06_visual_production/SCENE_ASSET_MANIFEST.json"
    if path.is_symlink() or not path.is_file():
        raise GenerationAttemptError("successful generation requires the scene asset manifest")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GenerationAttemptError(f"scene asset manifest is unreadable: {error}") from error
    assets = manifest.get("assets") if isinstance(manifest, dict) else None
    if not isinstance(assets, list):
        raise GenerationAttemptError("scene asset manifest assets are invalid")
    result: dict[str, dict[str, Any]] = {}
    for item in assets:
        task_id = item.get("task_id") if isinstance(item, dict) else None
        if not isinstance(task_id, str) or not task_id or task_id in result:
            raise GenerationAttemptError("scene asset manifest task IDs are invalid")
        registered_path = item.get("path")
        registered_hash = item.get("sha256")
        if not isinstance(registered_path, str) or not isinstance(registered_hash, str):
            raise GenerationAttemptError("scene asset manifest lacks path or hash evidence")
        try:
            asset_path = safe_project_output(root, Path(registered_path))
        except (OSError, ValueError) as error:
            raise GenerationAttemptError(f"registered scene asset path is invalid: {error}") from error
        if asset_path.is_symlink() or not asset_path.is_file() or sha256_file(asset_path) != registered_hash:
            raise GenerationAttemptError(f"registered scene asset hash is stale: {task_id}")
        result[task_id] = item
    return result


def _verify_success(
    root: Path,
    job: dict[str, Any],
    final_path: str | None,
    final_sha: str | None,
) -> None:
    if final_path is None or final_sha is None:
        raise GenerationAttemptError("success and regenerated outcomes require a final file")
    expected = job.get("output_target")
    if final_path != expected:
        raise GenerationAttemptError("final path does not match the generation job output target")
    registered = _scene_manifest(root)
    task_ids = job.get("task_ids", [])
    if any(task_id not in registered for task_id in task_ids):
        raise GenerationAttemptError("successful generation is not fully registered in the scene asset manifest")
    if job.get("generation_mode") == "single":
        record = registered[task_ids[0]]
        if record["path"] != final_path or record["sha256"] != final_sha:
            raise GenerationAttemptError("final hash does not match the registered scene asset")
        return
    split_targets = job.get("split_targets")
    if not isinstance(split_targets, list) or len(split_targets) != len(task_ids):
        raise GenerationAttemptError("sheet job split targets are invalid")
    for task_id, split_target in zip(task_ids, split_targets, strict=True):
        record = registered[task_id]
        if record["path"] != split_target:
            raise GenerationAttemptError("registered sheet child does not match its split target")


def _prompts_markdown(jobs: list[dict[str, Any]]) -> bytes:
    lines = ["# Production Image Generation Prompts", "", "Generated from `05_director/IMAGE_TASKS.jsonl` via the immutable generation jobs.", ""]
    for job in jobs:
        lines.extend((f"## {job['job_id']}", ""))
        for item in job.get("prompts", []):
            prompt = _clean(item.get("prompt") if isinstance(item, dict) else None, "generation prompt", required=True)
            lines.extend((f"### {item['task_id']}", "", prompt or "", ""))
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def record_generation_attempt(
    project: Path,
    *,
    job_id: str,
    outcome: str,
    provider: str,
    model: str,
    endpoint_class: str,
    concurrency: int,
    source: Path | None = None,
    final: Path | None = None,
    machine_rejection_reason: str | None = None,
    human_rejection_reason: str | None = None,
) -> GenerationAttemptResult:
    root = project.expanduser().resolve()
    job_id = _clean(job_id, "job_id", required=True) or ""
    provider = _clean(provider, "provider", required=True) or ""
    model = _clean(model, "model", required=True) or ""
    machine_reason = _clean(machine_rejection_reason, "machine rejection reason")
    human_reason = _clean(human_rejection_reason, "human rejection reason")
    if outcome not in _OUTCOMES:
        raise GenerationAttemptError("outcome must be success, failed, rejected, or regenerated")
    if endpoint_class not in _ENDPOINT_CLASSES:
        raise GenerationAttemptError("endpoint class is invalid")
    if isinstance(concurrency, bool) or not isinstance(concurrency, int) or not 1 <= concurrency <= 10:
        raise GenerationAttemptError("concurrency must be an integer from 1 through 10")
    if outcome in {"failed", "rejected"} and not (machine_reason or human_reason):
        raise GenerationAttemptError("failed or rejected attempts require a rejection reason")
    if outcome in {"success", "regenerated"} and (machine_reason or human_reason):
        raise GenerationAttemptError("successful attempts cannot contain a rejection reason")

    try:
        _plan, jobs, run, attempts = _load_current(root)
    except Exception as error:
        raise GenerationAttemptError(f"generation run evidence is not current: {error}") from error
    by_job = {item.get("job_id"): item for item in jobs}
    job = by_job.get(job_id)
    if job is None:
        raise GenerationAttemptError(f"unknown generation job: {job_id}")
    if concurrency != run.get("concurrency"):
        raise GenerationAttemptError("attempt concurrency does not match the generation plan")
    prior = [item for item in attempts if item.get("job_id") == job_id]
    if outcome == "regenerated" and not any(
        item.get("outcome") in {"failed", "rejected"} or item.get("status") == "failed" for item in prior
    ):
        raise GenerationAttemptError("regenerated outcome requires a prior failed or rejected attempt")

    source_path, source_sha = _file_evidence(root, source, "source")
    final_path, final_sha = _file_evidence(root, final, "final")
    if outcome in {"success", "regenerated"}:
        if source_path is None:
            raise GenerationAttemptError("success and regenerated outcomes require a source file")
        _verify_success(root, job, final_path, final_sha)
    elif final_path is not None:
        raise GenerationAttemptError("failed or rejected attempts cannot claim a final file")

    attempt = max((int(item.get("attempt", 0)) for item in prior), default=0) + 1
    record = {
        "schema_version": "generation-attempt.v2",
        "job_id": job_id,
        "attempt": attempt,
        "outcome": outcome,
        "machine_rejection_reason": machine_reason,
        "human_rejection_reason": human_reason,
        "provider": provider,
        "model": model,
        "endpoint_class": endpoint_class,
        "concurrency": concurrency,
        "source_path": source_path,
        "source_sha256": source_sha,
        "final_path": final_path,
        "final_sha256": final_sha,
        "recorded_at": _now(),
    }
    attempts.append(record)
    run_by_id = {item["job_id"]: item for item in run["jobs"]}
    state = run_by_id[job_id]
    state["attempt_count"] = attempt
    state["status"] = "completed" if outcome in {"success", "regenerated"} else "failed"
    state["last_error"] = machine_reason or human_reason
    state["evidence_sha256"] = final_sha
    _refresh_statuses(jobs, run["jobs"])
    run["attempt_count"] = len(attempts)

    ledger_path = root / "06_visual_production/GENERATION_ATTEMPTS.jsonl"
    run_path = root / "06_visual_production/GENERATION_RUN_MANIFEST.json"
    prompts_path = root / "06_visual_production/PROMPTS.md"
    prompts_bytes = _prompts_markdown(jobs)
    if prompts_path.exists() and (prompts_path.is_symlink() or prompts_path.read_bytes() != prompts_bytes):
        raise GenerationAttemptError("generation prompts evidence is stale or modified")
    old_ledger = ledger_path.read_bytes()
    old_run = run_path.read_bytes()
    old_prompts = prompts_path.read_bytes() if prompts_path.exists() else None
    ledger_bytes = b"".join(_canonical(item) + b"\n" for item in attempts)
    try:
        with tempfile.TemporaryDirectory(prefix=".generation-attempt-", dir=ledger_path.parent) as temp:
            staging = Path(temp)
            staged_ledger = staging / "ledger"
            staged_run = staging / "run"
            staged_prompts = staging / "prompts"
            staged_ledger.write_bytes(ledger_bytes)
            staged_run.write_bytes(_pretty(run))
            staged_prompts.write_bytes(prompts_bytes)
            os.replace(staged_ledger, ledger_path)
            os.replace(staged_prompts, prompts_path)
            os.replace(staged_run, run_path)
    except Exception as error:
        ledger_path.write_bytes(old_ledger)
        run_path.write_bytes(old_run)
        if old_prompts is None:
            prompts_path.unlink(missing_ok=True)
        else:
            prompts_path.write_bytes(old_prompts)
        raise GenerationAttemptError(f"generation attempt transaction failed: {error}") from error
    return GenerationAttemptResult(attempt, ledger_path, prompts_path, run_path)
