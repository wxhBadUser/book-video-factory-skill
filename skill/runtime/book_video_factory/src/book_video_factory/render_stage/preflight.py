from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from book_video_factory.hbg_bridge.provenance import _verify_vendor
from book_video_factory.hbg_bridge.runner import repository_root
from book_video_factory.hbg_bridge.shell import bash_executable, path_for_bash
from book_video_factory.manifests import safe_project_output, sha256_file
from book_video_factory.semantic_alignment.vision_review import (
    MissingVisionEvidenceError,
    VisionReviewError,
    validate_review_decision,
)
from book_video_factory.semantic_alignment.caption_contract import (
    load_caption_visual_contract_document,
)
from book_video_factory.semantic_alignment.vision_review.contracts import VisionEvidence
from book_video_factory.semantic_alignment.vision_review.evidence import (
    StaleVisionEvidenceError,
    verify_evidence_current,
)


class RenderPreflightError(RuntimeError):
    """The current HBG render workspace is not proven safe to start."""


@dataclass(frozen=True)
class RenderPreflightResult:
    status: str
    report_path: Path
    render_job_id: str
    next_stage_status: str


CommandRunner = Callable[[list[str], Path, dict[str, str] | None], subprocess.CompletedProcess[str]]
ProcessLister = Callable[[], list[dict[str, Any]]]

_REPORT_FIELDS = {
    "schema_version", "release_id", "render_manifest_path", "render_manifest_sha256",
    "opening_mix_approval_sha256", "hbg_vendor_lock_sha256", "workspace", "chosen_renderer",
    "render_job_id", "expected_work_dir", "free_disk_bytes", "free_disk_gib", "required_disk_bytes",
    "required_disk_gib", "style_validation", "hbg_disk_preflight", "existing_work_dirs",
    "recovery_commands",     "vision_review_decision_present", "vision_review_blockers",
    "caption_visual_contract_in_force", "caption_visual_contract_blockers",
    "recorded_at", "status", "next_stage_status",
}

# §10.1 / Part 6: the render preflight re-validates the committed scene review
# decision as a final vision-evidence gate. A shot may advance only if it carries
# authoritative vision evidence (a trusted multimodal provider that read the
# pixels, with a passing verdict) bound to its frame. The decision artifact MUST
# exist and be a regular file: a missing, symlinked, or tampered decision fails
# closed and blocks the render -- "no verified review means no render".
_SCENE_REVIEW_DECISION_RELATIVE = "06_visual_production/SCENE_REVIEW_DECISION.json"
_CONTRACT_RELATIVE = "04_audio/CAPTION_VISUAL_CONTRACT.json"
_REQUIRED_DECISION_FIELDS = {
    "task_id", "semantic_review_status", "reality_review_status",
    "identity_review_status", "note",
}
_OPTIONAL_DECISION_FIELDS = {"vision_evidence", "legacy_pass"}


def _scene_review_vision_blockers(
    decision_path: Path,
    assets_by_task: Mapping[str, Any] | None = None,
    root: Path | None = None,
) -> list[str]:
    """Return the task ids whose scene review decision lacks a vision binding.

    The decision artifact is optional at the preflight layer: when it is absent
    the scene-approval gate (a separate control) governs render eligibility. When
    it is present, every decision must carry vision evidence or a legacy mark;
    otherwise the offending task ids are returned so the preflight can block.

    ``assets_by_task`` optionally maps ``task_id`` to the current on-disk image
    path. When supplied, a shot whose stored evidence no longer matches the
    *current* pixels, caption prose, or image prompt is also blocked
    (BLOCKER-2 / BLOCKER-4 change-invalidation at the render gate). The
    authoritative caption/prompt are sourced from the director task queue
    (IMAGE_TASKS.jsonl) -- the same source the evidence was minted over. When
    that source cannot be loaded for a shot, the gate fails closed and blocks.
    """

    if decision_path.is_symlink() or not decision_path.is_file():
        raise RenderPreflightError(
            f"scene review decision is missing or symlinked: {decision_path}"
        )
    try:
        document = json.loads(decision_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RenderPreflightError(f"scene review decision is unreadable: {error}") from error
    if not isinstance(document, dict) or document.get("schema_version") != "scene-review-decision.v1":
        raise RenderPreflightError("scene review decision schema is unexpected")
    decisions = document.get("decisions")
    if not isinstance(decisions, list):
        raise RenderPreflightError("scene review decision has no decisions")
    blockers: list[str] = []
    caption_prompt_by_task: Mapping[str, tuple[str, str]] | None = None
    if assets_by_task is not None:
        search_root = root if root is not None else decision_path.parent.parent
        try:
            from book_video_factory.production_visuals.registry import _tasks as _load_tasks

            task_map = _load_tasks(search_root)
            caption_prompt_by_task = {
                str(tid): (str(t.get("caption_text", "")), str(t.get("prompt", "")))
                for tid, t in task_map.items()
                if isinstance(t, Mapping)
            }
        except Exception:
            # Cannot source the authoritative caption/prompt -> fail closed below.
            caption_prompt_by_task = None
    for item in decisions:
        if not isinstance(item, dict):
            raise RenderPreflightError("scene review decision item is invalid")
        keys = set(item)
        if not _REQUIRED_DECISION_FIELDS <= keys or not keys <= (_REQUIRED_DECISION_FIELDS | _OPTIONAL_DECISION_FIELDS):
            raise RenderPreflightError("scene review decision item fields are invalid")
        task_id = item.get("task_id")
        try:
            validate_review_decision({**item, "shot_id": task_id}, require_pass=True)
        except (MissingVisionEvidenceError, VisionReviewError):
            blockers.append(task_id)
            continue
        if assets_by_task is not None:
            evidence_payload = item.get("vision_evidence")
            asset_path = assets_by_task.get(task_id)
            if isinstance(evidence_payload, Mapping) and asset_path is not None:
                pair = caption_prompt_by_task.get(task_id) if caption_prompt_by_task is not None else None
                if pair is None:
                    # No authoritative caption/prompt available -> cannot prove
                    # the approval is current, so the render is blocked.
                    blockers.append(task_id)
                    continue
                try:
                    verify_evidence_current(
                        VisionEvidence.from_mapping({**evidence_payload, "shot_id": task_id}),
                        image_path=Path(asset_path),
                        caption_text=pair[0],
                        prompt_text=pair[1],
                    )
                except StaleVisionEvidenceError:
                    blockers.append(task_id)
    return blockers


def _contract_currency_blockers(contract_path: Path, root: Path, release_id: str | None = None) -> list[str]:
    """When the Caption Visual Contract is in force, every production image task
    must bind the contract's current hash, or the render is blocked.

    This is the final fail-closed link in the chain
    ``Caption -> Caption Visual Contract -> Shot Grouping -> Visual Proposition
    -> Image Prompt -> Actual Image -> Visual Alignment Review -> Render Gate``:
    an edited caption, recomputed proposition, or regenerated prompt changes the
    contract's ``content_sha256``, which invalidates the task's bound
    ``caption_visual_contract_sha256``, which blocks the render. The contract is
    the single source of truth; nothing downstream may outlive a contract change.

    Fail-closed: a missing / symlinked / invalid / tampered / release-mismatched
    contract blocks the entire render rather than proceeding with a stale image
    set.
    """

    if contract_path.is_symlink() or not contract_path.is_file():
        raise RenderPreflightError(f"caption visual contract is missing or symlinked: {contract_path}")
    try:
        raw = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RenderPreflightError(f"caption visual contract is unreadable: {error}") from error
    if isinstance(release_id, str) and raw.get("release_id") is not None and str(raw.get("release_id")) != release_id:
        raise RenderPreflightError(
            f"caption visual contract release {raw.get('release_id')!r} does not match "
            f"render release {release_id!r}"
        )
    # load_caption_visual_contract_document fail-closed-validates every contract
    # (caption_text sha256 + narrative_function), so a tampered contract blocks.
    try:
        contracts = load_caption_visual_contract_document(contract_path)
    except Exception as error:
        raise RenderPreflightError(f"caption visual contract is invalid: {error}") from error
    if not contracts:
        raise RenderPreflightError("caption visual contract contains no contracts")

    content_sha = {cid: c.content_sha256() for cid, c in contracts.items()}
    try:
        from book_video_factory.production_visuals.registry import _tasks as _load_tasks

        task_map = _load_tasks(root)
    except Exception as error:
        raise RenderPreflightError(
            f"cannot load production image tasks for contract currency: {error}"
        ) from error

    blockers: list[str] = []
    for tid, task in task_map.items():
        if not isinstance(task, Mapping):
            continue
        caption_ids = task.get("caption_ids") or []
        if not caption_ids:
            # identity / style-reference tasks are not bound to the contract.
            continue
        bound = task.get("caption_visual_contract_sha256")
        expected_inputs = sorted(content_sha[cid] for cid in caption_ids if cid in content_sha)
        if not expected_inputs:
            # The contract covers none of this task's captions -> the contract is
            # incomplete relative to the production tasks, so the bind is unsafe.
            blockers.append(str(tid))
            continue
        expected = hashlib.sha256("|".join(expected_inputs).encode("utf-8")).hexdigest()
        if bound is None or bound != expected:
            blockers.append(str(tid))
    return blockers


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _pretty(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _load(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RenderPreflightError(f"{label} is missing or symlinked")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RenderPreflightError(f"{label} is unreadable: {error}") from error
    if not isinstance(value, dict):
        raise RenderPreflightError(f"{label} must be an object")
    return value


def _default_command(command: list[str], cwd: Path, env: dict[str, str] | None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8")


def _default_processes() -> list[dict[str, Any]]:
    if os.name == "nt":
        completed = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_Process | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if completed.returncode != 0:
            raise RenderPreflightError("unable to enumerate active Windows render processes")
        try:
            raw = json.loads(completed.stdout or "[]")
        except json.JSONDecodeError as error:
            raise RenderPreflightError("Windows process inventory is unreadable") from error
        rows = raw if isinstance(raw, list) else [raw]
        return [
            {"pid": item.get("ProcessId"), "command_line": item.get("CommandLine") or ""}
            for item in rows if isinstance(item, dict)
        ]
    completed = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True, encoding="utf-8")
    if completed.returncode != 0:
        raise RenderPreflightError("unable to enumerate active render processes")
    result: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pid, _, command = stripped.partition(" ")
        result.append({"pid": int(pid), "command_line": command})
    return result


def _directory_bytes(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        if item.is_symlink():
            raise RenderPreflightError(f"render work directory contains a symlink: {item}")
        if item.is_file():
            total += item.stat().st_size
    return total


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _work_dirs(workspace: Path, processes: list[dict[str, Any]], expected_name: str | None) -> list[dict[str, Any]]:
    renders = workspace / "renders"
    if not renders.exists():
        return []
    if renders.is_symlink() or not renders.is_dir():
        raise RenderPreflightError("workspace renders path is invalid")
    result: list[dict[str, Any]] = []
    for path in sorted(renders.iterdir(), key=lambda item: item.name.casefold()):
        if not (path.name.startswith("work-") or path.name.startswith("ffmpeg-work-")):
            continue
        resolved = path.resolve()
        try:
            resolved.relative_to(renders.resolve())
        except ValueError as error:
            raise RenderPreflightError("render work directory resolves outside the workspace") from error
        if path.is_symlink() or not path.is_dir():
            result.append({
                "path": path.relative_to(workspace).as_posix(), "bytes": 0, "activity": "unsafe",
                "matching_pids": [], "belongs_to_current_job": path.name == expected_name,
                "verify_command": None, "remove_command": None,
            })
            continue
        matches = sorted({
            int(item["pid"])
            for item in processes
            if isinstance(item, dict)
            and isinstance(item.get("pid"), int)
            and isinstance(item.get("command_line"), str)
            and (str(resolved).casefold() in item["command_line"].casefold() or path.name.casefold() in item["command_line"].casefold())
        })
        absolute = str(resolved)
        verify = (
            "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "
            + _ps_quote(f"*{path.name}*")
            + " } | Select-Object ProcessId,CommandLine"
            if os.name == "nt"
            else f"ps -eo pid=,args= | grep -F -- {_ps_quote(path.name)}"
        )
        remove = (
            f"Remove-Item -LiteralPath {_ps_quote(absolute)} -Recurse"
            if os.name == "nt"
            else f"rm -rf -- {_ps_quote(absolute)}"
        )
        result.append({
            "path": path.relative_to(workspace).as_posix(),
            "bytes": _directory_bytes(path),
            "activity": "active" if matches else "inactive",
            "matching_pids": matches,
            "belongs_to_current_job": path.name == expected_name,
            "verify_command": verify,
            "remove_command": None if matches else remove,
        })
    return result


def _command_evidence(completed: subprocess.CompletedProcess[str], command: list[str]) -> dict[str, Any]:
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    return {
        "command": command,
        "exit_code": completed.returncode,
        "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
    }


def _reported_disk(stdout: str) -> tuple[int | None, int | None]:
    values: dict[str, str] = {}
    for line in stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip()] = value.strip()
    try:
        return int(values["free_gib"]), int(values["required_gib"])
    except (KeyError, ValueError):
        return None, None


def preflight_render(
    project: Path,
    input_path: Path,
    *,
    command_runner: CommandRunner | None = None,
    process_lister: ProcessLister | None = None,
) -> RenderPreflightResult:
    root = project.expanduser().resolve()
    from book_video_factory.render_stage.compiler import RenderStageError, prepare_render_stage

    try:
        prepared = prepare_render_stage(root, input_path)
    except RenderStageError as error:
        raise RenderPreflightError(f"render workspace preparation failed: {error}") from error
    manifest = _load(prepared.render_manifest_path, "render manifest")
    workspace = prepared.workspace
    try:
        _verify_vendor(repository_root())
    except RuntimeError as error:
        raise RenderPreflightError(f"HBG vendor integrity failed: {error}") from error
    approval_path = root / "07_render/OPENING_MIX_APPROVAL.json"
    if approval_path.is_symlink() or not approval_path.is_file():
        raise RenderPreflightError("opening mix approval is missing")
    runner = command_runner or _default_command
    scripts = repository_root() / "vendor/hbg-life-simulation/scripts"
    style_command = ["node", str(scripts / "validate_style_system.mjs"), str(workspace)]
    try:
        style_result = runner(style_command, workspace, None)
    except OSError as error:
        raise RenderPreflightError(f"HBG style validator could not start: {error}") from error
    disk_command = [
        bash_executable(),
        path_for_bash(scripts / "preflight_long_render.sh"),
        path_for_bash(workspace),
        str(manifest["minimum_free_gib"]),
    ]
    try:
        disk_result = runner(disk_command, workspace, None)
    except OSError as error:
        raise RenderPreflightError(f"HBG disk preflight could not start: {error}") from error
    style_evidence = _command_evidence(style_result, style_command)
    try:
        style_payload = json.loads(style_result.stdout or "{}")
    except json.JSONDecodeError:
        style_payload = {}
    style_evidence["validated"] = style_result.returncode == 0 and style_payload.get("status") == "validated"
    disk_evidence = _command_evidence(disk_result, disk_command)
    reported_free, reported_required = _reported_disk(disk_result.stdout or "")
    disk_evidence["reported_free_gib"] = reported_free
    disk_evidence["reported_required_gib"] = reported_required
    disk_evidence["passed"] = disk_result.returncode == 0 and "preflight=pass" in (disk_result.stdout or "")

    usage = shutil.disk_usage(workspace)
    required_gib = int(manifest["minimum_free_gib"])
    required_bytes = required_gib * 1024 ** 3
    output_stem = Path(str(manifest["output_name"])).stem
    expected_name = f"ffmpeg-work-{output_stem}" if manifest["renderer"] == "streaming_ffmpeg" else None
    try:
        processes = (process_lister or _default_processes)()
    except Exception as error:
        raise RenderPreflightError(f"active render process detection failed: {error}") from error
    work_dirs = _work_dirs(workspace, processes, expected_name)
    blockers = [
        item for item in work_dirs
        if item["activity"] in {"active", "unsafe"} or item["belongs_to_current_job"]
    ]
    decision_path = root / _SCENE_REVIEW_DECISION_RELATIVE
    # L4 (BLOCKER-2 change-invalidation): map each task to its current on-disk
    # scene image so the render gate can detect a swapped frame. Only populated
    # when the scene-asset manifest exists; otherwise image staleness is not
    # checked here (the scene-approval gate still enforces it).
    assets_by_task: dict[str, Any] = {}
    scene_asset_manifest_path = root / "06_visual_production/SCENE_ASSET_MANIFEST.json"
    if scene_asset_manifest_path.is_file():
        scene_asset_manifest = _load(scene_asset_manifest_path, "scene asset manifest")
        for asset in scene_asset_manifest.get("assets", []):
            task_id = asset.get("task_id")
            asset_rel = asset.get("path")
            if isinstance(task_id, str) and isinstance(asset_rel, str):
                assets_by_task[task_id] = root / asset_rel
    vision_blockers = _scene_review_vision_blockers(decision_path, assets_by_task)
    # L5: when the Caption Visual Contract is in force, the prompt binding of every
    # production image task must carry its current hash; a stale or missing bind
    # blocks the render (fail-closed). Skipped when the contract is absent so
    # pre-contract projects/tests are unaffected.
    contract_path = root / _CONTRACT_RELATIVE
    contract_blockers: list[str] = []
    if contract_path.is_file():
        contract_blockers = _contract_currency_blockers(contract_path, root, manifest.get("release_id"))
    passed = (
        bool(style_evidence["validated"])
        and bool(disk_evidence["passed"])
        and usage.free >= required_bytes
        and not blockers
        and not vision_blockers
        and not contract_blockers
    )
    render_manifest_sha = sha256_file(prepared.render_manifest_path)
    job_id = hashlib.sha256(f"{render_manifest_sha}:{manifest['output_name']}".encode("utf-8")).hexdigest()[:20]
    report = {
        "schema_version": "render-preflight.v1",
        "release_id": manifest["release_id"],
        "render_manifest_path": prepared.render_manifest_path.relative_to(root).as_posix(),
        "render_manifest_sha256": render_manifest_sha,
        "opening_mix_approval_sha256": sha256_file(approval_path),
        "hbg_vendor_lock_sha256": sha256_file(repository_root() / "vendor/hbg-life-simulation/UPSTREAM_LOCK.json"),
        "workspace": workspace.relative_to(root).as_posix(),
        "chosen_renderer": manifest["renderer"],
        "render_job_id": job_id,
        "expected_work_dir": f"renders/{expected_name}" if expected_name else None,
        "free_disk_bytes": usage.free,
        "free_disk_gib": round(usage.free / 1024 ** 3, 3),
        "required_disk_bytes": required_bytes,
        "required_disk_gib": required_gib,
        "style_validation": style_evidence,
        "hbg_disk_preflight": disk_evidence,
        "existing_work_dirs": work_dirs,
        "recovery_commands": [item["remove_command"] for item in work_dirs if item["remove_command"]],
        "vision_review_decision_present": decision_path.is_file(),
        "vision_review_blockers": vision_blockers,
        "caption_visual_contract_in_force": contract_path.is_file(),
        "caption_visual_contract_blockers": contract_blockers,
        "recorded_at": _now(),
        "status": "pass" if passed else "blocked",
        "next_stage_status": "ready_for_hbg_render" if passed else "blocked_by_render_preflight",
    }
    report_path = safe_project_output(root, Path("07_render/RENDER_PREFLIGHT.json"))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=".render-preflight-", suffix=".json", dir=report_path.parent, delete=False) as output:
        temp_path = Path(output.name)
        output.write(_pretty(report))
    try:
        os.replace(temp_path, report_path)
    except OSError as error:
        temp_path.unlink(missing_ok=True)
        raise RenderPreflightError(f"render preflight report could not be published: {error}") from error
    return RenderPreflightResult("created", report_path, job_id, report["next_stage_status"])


def verify_render_preflight(project: Path) -> dict[str, Any]:
    root = project.expanduser().resolve()
    report_path = root / "07_render/RENDER_PREFLIGHT.json"
    report = _load(report_path, "render preflight report")
    if set(report) != _REPORT_FIELDS or report.get("schema_version") != "render-preflight.v1":
        raise RenderPreflightError("render preflight report fields are invalid")
    manifest_path = safe_project_output(root, Path(str(report.get("render_manifest_path", ""))))
    manifest = _load(manifest_path, "render manifest")
    approval_path = root / "07_render/OPENING_MIX_APPROVAL.json"
    expected = {
        "release_id": manifest.get("release_id"),
        "render_manifest_sha256": sha256_file(manifest_path),
        "opening_mix_approval_sha256": sha256_file(approval_path),
        "hbg_vendor_lock_sha256": sha256_file(repository_root() / "vendor/hbg-life-simulation/UPSTREAM_LOCK.json"),
        "workspace": manifest.get("workspace"),
        "chosen_renderer": manifest.get("renderer"),
        "required_disk_gib": manifest.get("minimum_free_gib"),
        "required_disk_bytes": int(manifest.get("minimum_free_gib", 0)) * 1024 ** 3,
        "status": "pass",
        "next_stage_status": "ready_for_hbg_render",
    }
    for key, value in expected.items():
        if report.get(key) != value:
            raise RenderPreflightError(f"render preflight is stale or blocked: {key}")
    workspace = safe_project_output(root, Path(str(report["workspace"])))
    if not workspace.is_dir() or workspace.is_symlink():
        raise RenderPreflightError("render preflight workspace is missing")
    current_free = shutil.disk_usage(workspace).free
    if current_free < int(report["required_disk_bytes"]):
        raise RenderPreflightError("render preflight disk headroom is no longer sufficient")
    output_stem = Path(str(manifest.get("output_name", ""))).stem
    expected_name = f"ffmpeg-work-{output_stem}" if manifest.get("renderer") == "streaming_ffmpeg" else None
    expected_work_dir = f"renders/{expected_name}" if expected_name else None
    expected_job_id = hashlib.sha256(
        f"{sha256_file(manifest_path)}:{manifest.get('output_name')}".encode("utf-8")
    ).hexdigest()[:20]
    if report.get("expected_work_dir") != expected_work_dir or report.get("render_job_id") != expected_job_id:
        raise RenderPreflightError("render preflight job identity is stale")
    if report.get("free_disk_gib") != round(int(report.get("free_disk_bytes", -1)) / 1024 ** 3, 3):
        raise RenderPreflightError("render preflight disk calculation is invalid")
    current_work = _work_dirs(workspace, [], expected_name)
    if current_work != report.get("existing_work_dirs") or any(
        item.get("activity") != "inactive" or item.get("belongs_to_current_job") for item in current_work
    ):
        raise RenderPreflightError("render work directory state changed or remains blocked")
    if report.get("recovery_commands") != [item["remove_command"] for item in current_work if item["remove_command"]]:
        raise RenderPreflightError("render preflight recovery commands are stale")
    if not report.get("style_validation", {}).get("validated") or not report.get("hbg_disk_preflight", {}).get("passed"):
        raise RenderPreflightError("HBG style or disk preflight did not pass")
    return report
