from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from book_video_factory.manifests import sha256_file


class GenerationScheduleError(RuntimeError):
    """The production generation DAG or its mutable run projection is invalid."""


@dataclass(frozen=True)
class GenerationPlanResult:
    status: str
    concurrency: int
    plan_path: Path
    jobs_path: Path
    run_manifest_path: Path
    attempts_path: Path


@dataclass(frozen=True)
class GenerationWaveResult:
    jobs: list[dict[str, Any]]
    concurrency: int
    remaining_count: int


@dataclass(frozen=True)
class GenerationFinalizeResult:
    settled_job_ids: list[str]
    completed_count: int
    failed_count: int
    next_ready_count: int


_OUTPUT_DIR = "06_visual_production"
_PLAN = f"{_OUTPUT_DIR}/GENERATION_PLAN.json"
_JOBS = f"{_OUTPUT_DIR}/GENERATION_JOBS.jsonl"
_RUN = f"{_OUTPUT_DIR}/GENERATION_RUN_MANIFEST.json"
_ATTEMPTS = f"{_OUTPUT_DIR}/GENERATION_ATTEMPTS.jsonl"
_FILES = (_PLAN, _JOBS, _RUN, _ATTEMPTS)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise GenerationScheduleError(f"{label} is missing or symlinked")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GenerationScheduleError(f"{label} is unreadable: {error}") from error
    if not isinstance(value, dict):
        raise GenerationScheduleError(f"{label} must be an object")
    return value


def _load_tasks(root: Path) -> list[dict[str, Any]]:
    path = root / "05_director/IMAGE_TASKS.jsonl"
    if path.is_symlink() or not path.is_file():
        raise GenerationScheduleError("production image tasks are missing or symlinked")
    tasks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            task = json.loads(line)
        except json.JSONDecodeError as error:
            raise GenerationScheduleError(f"production image task line {number} is invalid") from error
        task_id = task.get("task_id") if isinstance(task, dict) else None
        if (
            not isinstance(task, dict)
            or task.get("schema_version") != "production-image-task.v1"
            or not isinstance(task_id, str)
            or not task_id
            or task_id in seen
            or task.get("generation_mode") not in {"single", "2x2"}
        ):
            raise GenerationScheduleError(f"production image task line {number} has an invalid contract")
        identity = task.get("identity_reference_task_ids", [])
        if not isinstance(identity, list) or any(not isinstance(item, str) or not item for item in identity):
            raise GenerationScheduleError(f"production image task {task_id} has invalid identity references")
        seen.add(task_id)
        tasks.append(task)
    if not tasks:
        raise GenerationScheduleError("production image task queue is empty")
    return tasks


def _registered_task_ids(root: Path) -> set[str]:
    path = root / "06_visual_production/SCENE_ASSET_MANIFEST.json"
    if not path.exists():
        return set()
    value = _load_json(path, "scene asset manifest")
    assets = value.get("assets")
    if not isinstance(assets, list):
        raise GenerationScheduleError("scene asset manifest assets are invalid")
    result: set[str] = set()
    for item in assets:
        task_id = item.get("task_id") if isinstance(item, dict) else None
        if not isinstance(task_id, str) or not task_id or task_id in result:
            raise GenerationScheduleError("scene asset manifest task IDs are invalid")
        result.add(task_id)
    return result


def _build_jobs(tasks: list[dict[str, Any]], sheet_map: dict[str, Any]) -> list[dict[str, Any]]:
    by_id = {task["task_id"]: task for task in tasks}
    groups = sheet_map.get("sheet_groups")
    singles = sheet_map.get("single_tasks")
    if sheet_map.get("schema_version") != "sheet-map.v1" or not isinstance(groups, list) or not isinstance(singles, list):
        raise GenerationScheduleError("sheet map contract is invalid")
    jobs: list[dict[str, Any]] = []
    assigned: set[str] = set()

    def prompt_records(task_ids: Iterable[str]) -> list[dict[str, str]]:
        return [
            {"task_id": task_id, "prompt": str(by_id[task_id]["prompt"]), "prompt_sha256": str(by_id[task_id]["prompt_sha256"])}
            for task_id in task_ids
        ]

    for group in groups:
        task_ids = group.get("task_ids") if isinstance(group, dict) else None
        if (
            not isinstance(group, dict)
            or not isinstance(group.get("sheet_id"), str)
            or not isinstance(task_ids, list)
            or len(task_ids) != 4
            or any(task_id not in by_id or task_id in assigned for task_id in task_ids)
        ):
            raise GenerationScheduleError("sheet map group must contain four unique known tasks")
        if any(by_id[task_id]["generation_mode"] != "2x2" for task_id in task_ids):
            raise GenerationScheduleError("sheet group contains a task that is not 2x2-safe")
        assigned.update(task_ids)
        identities = sorted({item for task_id in task_ids for item in by_id[task_id].get("identity_reference_task_ids", [])})
        jobs.append({
            "schema_version": "generation-job.v1",
            "job_id": group["sheet_id"],
            "task_ids": task_ids,
            "generation_mode": "2x2",
            "dependencies": [],
            "identity_keys": identities,
            "output_target": group.get("output_target"),
            "split_targets": group.get("split_targets"),
            "prompts": prompt_records(task_ids),
        })
    for item in singles:
        task_id = item.get("task_id") if isinstance(item, dict) else None
        if not isinstance(task_id, str) or task_id not in by_id or task_id in assigned:
            raise GenerationScheduleError("single task map contains an unknown or duplicate task")
        assigned.add(task_id)
        task = by_id[task_id]
        jobs.append({
            "schema_version": "generation-job.v1",
            "job_id": task_id,
            "task_ids": [task_id],
            "generation_mode": "single",
            "dependencies": [],
            "identity_keys": sorted(set(task.get("identity_reference_task_ids", []))),
            "output_target": task.get("output_target"),
            "split_targets": [],
            "prompts": prompt_records([task_id]),
        })
    if assigned != set(by_id):
        raise GenerationScheduleError("sheet map does not cover every production image task")
    last_for_identity: dict[str, str] = {}
    for job in jobs:
        dependencies: list[str] = []
        for identity in job["identity_keys"]:
            previous = last_for_identity.get(identity)
            if previous and previous not in dependencies:
                dependencies.append(previous)
            last_for_identity[identity] = job["job_id"]
        job["dependencies"] = dependencies
    return jobs


def _source_evidence(root: Path, concurrency: int) -> dict[str, Any]:
    paths = {
        "image_tasks": root / "05_director/IMAGE_TASKS.jsonl",
        "sheet_map": root / "05_director/SHEET_MAP.json",
        "director_stage_manifest": root / "05_director/DIRECTOR_STAGE_MANIFEST.json",
    }
    for label, path in paths.items():
        if path.is_symlink() or not path.is_file():
            raise GenerationScheduleError(f"{label} is missing or symlinked")
    return {
        **{f"{label}_sha256": sha256_file(path) for label, path in paths.items()},
        "concurrency": concurrency,
    }


def _initial_run(jobs: list[dict[str, Any]], registered: set[str]) -> list[dict[str, Any]]:
    for job in jobs:
        registered_children = set(job["task_ids"]) & registered
        if job["generation_mode"] == "2x2" and registered_children and registered_children != set(job["task_ids"]):
            raise GenerationScheduleError(
                f"sheet job {job['job_id']} is only partially registered and cannot be rerun"
            )
    completed: set[str] = {
        job["job_id"] for job in jobs if set(job["task_ids"]).issubset(registered)
    }
    run_jobs: list[dict[str, Any]] = []
    for job in jobs:
        if job["job_id"] in completed:
            status = "completed"
        elif all(dependency in completed for dependency in job["dependencies"]):
            status = "ready"
        else:
            status = "blocked"
        run_jobs.append({
            "job_id": job["job_id"],
            "status": status,
            "attempt_count": 0,
            "last_error": None,
            "evidence_sha256": None,
        })
    return run_jobs


def _paths(root: Path) -> tuple[Path, Path, Path, Path]:
    return tuple(root / relative for relative in _FILES)  # type: ignore[return-value]


def _load_current(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    plan_path, jobs_path, run_path, attempts_path = _paths(root)
    plan = _load_json(plan_path, "generation plan")
    run = _load_json(run_path, "generation run manifest")
    try:
        jobs = [json.loads(line) for line in jobs_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        attempts = [json.loads(line) for line in attempts_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError) as error:
        raise GenerationScheduleError(f"generation JSONL evidence is unreadable: {error}") from error
    if (
        plan.get("schema_version") != "generation-plan.v1"
        or run.get("schema_version") != "generation-run-manifest.v1"
        or run.get("generation_plan_sha256") != sha256_file(plan_path)
        or run.get("generation_jobs_sha256") != sha256_file(jobs_path)
        or run.get("attempt_count") != len(attempts)
        or len(run.get("jobs", [])) != len(jobs)
    ):
        raise GenerationScheduleError("generation run evidence is stale or inconsistent")
    return plan, jobs, run, attempts


def plan_generation_run(project: Path, *, concurrency: int = 5) -> GenerationPlanResult:
    if isinstance(concurrency, bool) or not isinstance(concurrency, int) or not 1 <= concurrency <= 10:
        raise GenerationScheduleError("concurrency must be an integer from 1 through 10")
    root = project.expanduser().resolve()
    tasks = _load_tasks(root)
    sheet_map = _load_json(root / "05_director/SHEET_MAP.json", "sheet map")
    director = _load_json(root / "05_director/DIRECTOR_STAGE_MANIFEST.json", "director stage manifest")
    release_id = director.get("release_id")
    if not isinstance(release_id, str) or not release_id:
        raise GenerationScheduleError("director stage release is invalid")
    jobs = _build_jobs(tasks, sheet_map)
    evidence = _source_evidence(root, concurrency)
    input_digest = _digest_bytes(_canonical(evidence))
    plan_path, jobs_path, run_path, attempts_path = _paths(root)
    existing = [path.exists() for path in (plan_path, jobs_path, run_path, attempts_path)]
    if any(existing):
        if not all(existing):
            raise GenerationScheduleError("generation run is a partial transaction")
        plan, existing_jobs, run, _attempts = _load_current(root)
        if plan.get("input_digest") != input_digest or plan.get("concurrency") != concurrency or existing_jobs != jobs:
            raise GenerationScheduleError("existing generation run belongs to different or stale inputs")
        return GenerationPlanResult("unchanged", concurrency, plan_path, jobs_path, run_path, attempts_path)
    jobs_bytes = b"".join(_canonical(job) + b"\n" for job in jobs)
    plan = {
        "schema_version": "generation-plan.v1",
        "release_id": release_id,
        "input_digest": input_digest,
        "source_evidence": evidence,
        "concurrency": concurrency,
        "max_concurrency": 10,
        "job_count": len(jobs),
        "sheet_job_count": sum(job["generation_mode"] == "2x2" for job in jobs),
        "single_job_count": sum(job["generation_mode"] == "single" for job in jobs),
    }
    plan_bytes = _pretty(plan)
    attempts_bytes = b""
    run = {
        "schema_version": "generation-run-manifest.v1",
        "release_id": release_id,
        "input_digest": input_digest,
        "generation_plan_sha256": _digest_bytes(plan_bytes),
        "generation_jobs_sha256": _digest_bytes(jobs_bytes),
        "concurrency": concurrency,
        "max_concurrency": 10,
        "attempt_count": 0,
        "jobs": _initial_run(jobs, _registered_task_ids(root)),
    }
    output_dir = plan_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    published: list[Path] = []
    try:
        with tempfile.TemporaryDirectory(prefix=".generation-plan-", dir=output_dir) as temp:
            staging = Path(temp)
            payloads = (("plan", plan_path, plan_bytes), ("jobs", jobs_path, jobs_bytes), ("attempts", attempts_path, attempts_bytes), ("run", run_path, _pretty(run)))
            staged: list[tuple[Path, Path]] = []
            for name, destination, payload in payloads:
                source = staging / name
                source.write_bytes(payload)
                staged.append((source, destination))
            for source, destination in staged:
                os.replace(source, destination)
                published.append(destination)
    except Exception as error:
        for path in published:
            path.unlink(missing_ok=True)
        if isinstance(error, GenerationScheduleError):
            raise
        raise GenerationScheduleError(f"generation plan transaction failed: {error}") from error
    return GenerationPlanResult("created", concurrency, plan_path, jobs_path, run_path, attempts_path)


def next_generation_wave(project: Path, *, limit: int | None = None) -> GenerationWaveResult:
    root = project.expanduser().resolve()
    plan, jobs, run, _attempts = _load_current(root)
    concurrency = int(plan["concurrency"])
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
        raise GenerationScheduleError("wave limit must be a positive integer")
    bound = min(concurrency, limit if limit is not None else concurrency, 5)
    by_status = {item["job_id"]: item for item in run["jobs"]}
    eligible = [job for job in jobs if by_status[job["job_id"]]["status"] in {"ready", "failed"}]
    return GenerationWaveResult(eligible[:bound], concurrency, len(eligible))


def _refresh_statuses(jobs: list[dict[str, Any]], run_jobs: list[dict[str, Any]]) -> None:
    by_id = {item["job_id"]: item for item in run_jobs}
    completed = {job_id for job_id, item in by_id.items() if item["status"] == "completed"}
    for job in jobs:
        state = by_id[job["job_id"]]
        if state["status"] in {"completed", "failed"}:
            continue
        state["status"] = "ready" if all(item in completed for item in job["dependencies"]) else "blocked"


def finalize_generation_wave(project: Path, results: list[dict[str, Any]]) -> GenerationFinalizeResult:
    root = project.expanduser().resolve()
    _plan, jobs, run, attempts = _load_current(root)
    current = next_generation_wave(root).jobs
    expected_ids = [job["job_id"] for job in current]
    if not isinstance(results, list) or len(results) != len(expected_ids):
        raise GenerationScheduleError("wave results must settle every current job exactly once")
    supplied: dict[str, dict[str, Any]] = {}
    for item in results:
        if not isinstance(item, dict) or item.get("job_id") not in expected_ids or item["job_id"] in supplied:
            raise GenerationScheduleError("wave results contain an unknown or duplicate job")
        status = item.get("status")
        allowed = {"job_id", "status", "evidence_sha256"} if status == "completed" else {"job_id", "status", "error"}
        if set(item) != allowed or status not in {"completed", "failed"}:
            raise GenerationScheduleError("wave result fields or status are invalid")
        if status == "completed":
            digest = item.get("evidence_sha256")
            if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise GenerationScheduleError("completed wave result requires a lowercase SHA-256")
        else:
            error = item.get("error")
            if not isinstance(error, str) or not error.strip() or error != error.strip():
                raise GenerationScheduleError("failed wave result requires a precise error")
        supplied[item["job_id"]] = item
    if set(supplied) != set(expected_ids):
        raise GenerationScheduleError("wave results do not settle the current wave")
    run_by_id = {item["job_id"]: item for item in run["jobs"]}
    for job_id in expected_ids:
        result = supplied[job_id]
        state = run_by_id[job_id]
        attempt = int(state["attempt_count"]) + 1
        state["status"] = result["status"]
        state["attempt_count"] = attempt
        state["last_error"] = result.get("error")
        state["evidence_sha256"] = result.get("evidence_sha256")
        attempts.append({
            "schema_version": "generation-attempt.v1",
            "job_id": job_id,
            "attempt": attempt,
            "status": result["status"],
            "error": result.get("error"),
            "evidence_sha256": result.get("evidence_sha256"),
            "recorded_at": _now(),
        })
    _refresh_statuses(jobs, run["jobs"])
    run["attempt_count"] = len(attempts)
    run_path = root / _RUN
    attempts_path = root / _ATTEMPTS
    old_run = run_path.read_bytes()
    old_attempts = attempts_path.read_bytes()
    attempt_bytes = b"".join(_canonical(item) + b"\n" for item in attempts)
    try:
        with tempfile.TemporaryDirectory(prefix=".generation-wave-", dir=run_path.parent) as temp:
            temp_root = Path(temp)
            staged_attempts = temp_root / "attempts"
            staged_run = temp_root / "run"
            staged_attempts.write_bytes(attempt_bytes)
            staged_run.write_bytes(_pretty(run))
            os.replace(staged_attempts, attempts_path)
            os.replace(staged_run, run_path)
    except Exception as error:
        attempts_path.write_bytes(old_attempts)
        run_path.write_bytes(old_run)
        raise GenerationScheduleError(f"generation wave transaction failed: {error}") from error
    statuses = [item["status"] for item in run["jobs"]]
    return GenerationFinalizeResult(
        expected_ids,
        statuses.count("completed"),
        statuses.count("failed"),
        statuses.count("ready") + statuses.count("failed"),
    )
