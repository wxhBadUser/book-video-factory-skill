#!/usr/bin/env python3
"""Diagnose the active single-narrator HBG book-video pipeline."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Any


SCRIPT_PATH = Path(__file__).resolve()
FACTORY_ROOT = SCRIPT_PATH.parents[1]
SRC_ROOT = FACTORY_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from book_video_factory.contracts import ContractError, ReleaseProfile  # noqa: E402
from book_video_factory.gates import evaluate_workflow_state  # noqa: E402
from book_video_factory.style_profiles import (  # noqa: E402
    StyleProfileError,
    project_workflow,
)


PROFILE_ORDER = {
    "planning": 0,
    "local-render": 1,
    "production": 2,
    "public-release": 3,
}


def _required(profile: str, minimum: str) -> bool:
    return PROFILE_ORDER[profile] >= PROFILE_ORDER[minimum]


def _status(available: bool, required: bool) -> str:
    if available:
        return "ready"
    return "blocked" if required else "warn"


def executable_check(name: str, *, required: bool) -> dict[str, Any]:
    path = shutil.which(name)
    return {
        "name": name,
        "status": _status(path is not None, required),
        "path": path or "missing",
    }


def python_module_check(name: str, *, label: str, required: bool) -> dict[str, Any]:
    try:
        available = importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError):
        available = False
    return {
        "name": label,
        "status": _status(available, required),
        "path": "installed" if available else "missing",
    }


def edge_tts_check(*, required: bool) -> dict[str, Any]:
    command = shutil.which("edge-tts")
    try:
        module = importlib.util.find_spec("edge_tts") is not None
    except (ImportError, ModuleNotFoundError):
        module = False
    available = command is not None or module
    return {
        "name": "edge-tts",
        "status": _status(available, required),
        "path": command or ("python_module:edge_tts" if module else "missing"),
        "note": "Required when generating continuous narration and the master VTT; network access to the Edge speech service is also required.",
    }



def phase4_audio_cli_check() -> dict[str, Any]:
    required = (
        FACTORY_ROOT / "scripts/run_audio_stage.py",
        FACTORY_ROOT / "src/book_video_factory/audio_stage/compiler.py",
        FACTORY_ROOT / "src/book_video_factory/audio_stage/status.py",
        FACTORY_ROOT / "schemas/audio_stage_manifest.v1.schema.json",
    )
    missing = [str(path.relative_to(FACTORY_ROOT)) for path in required if not path.is_file()]
    return {
        "name": "phase4_audio_cli",
        "status": "blocked" if missing else "ready",
        "path": str(FACTORY_ROOT / "scripts/run_audio_stage.py"),
        "note": (
            "missing Phase 4 runtime files: " + ", ".join(missing)
            if missing
            else "Codex can run generate/finalize/status; real Edge TTS and network access remain external requirements."
        ),
    }



def phase5_7_runtime_check() -> dict[str, Any]:
    required = (
        FACTORY_ROOT / "scripts/build_director_stage.py",
        FACTORY_ROOT / "scripts/register_scene_asset.py",
        FACTORY_ROOT / "scripts/split_scene_sheet.py",
        FACTORY_ROOT / "scripts/review_scene_assets.py",
        FACTORY_ROOT / "scripts/approve_scene_assets.py",
        FACTORY_ROOT / "scripts/run_render_stage.py",
        FACTORY_ROOT / "scripts/approve_final_master.py",
        FACTORY_ROOT / "scripts/run_full_pipeline.py",
        FACTORY_ROOT / "scripts/codex_acceptance.py",
        FACTORY_ROOT / "src/book_video_factory/director_stage/compiler.py",
        FACTORY_ROOT / "src/book_video_factory/production_visuals/registry.py",
        FACTORY_ROOT / "src/book_video_factory/render_stage/compiler.py",
        FACTORY_ROOT / "src/book_video_factory/delivery_stage/approval.py",
    )
    missing = [str(path.relative_to(FACTORY_ROOT)) for path in required if not path.is_file()]
    return {
        "name": "phase5_7_codex_runtime",
        "status": "blocked" if missing else "ready",
        "path": str(FACTORY_ROOT / "scripts/run_full_pipeline.py"),
        "note": (
            "missing Phase 5-7 runtime files: " + ", ".join(missing)
            if missing
            else "Codex director, scene-asset, HBG render, encoded QA, and final-master approval commands are installed."
        ),
    }

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_hbg_vendor(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return explicit.expanduser().resolve()
    for parent in (SCRIPT_PATH.parent, *SCRIPT_PATH.parents):
        candidate = parent / "vendor/hbg-life-simulation"
        if candidate.is_dir():
            return candidate.resolve()
    return (SCRIPT_PATH.parents[2] / "vendor/hbg-life-simulation").resolve()


def verify_hbg_vendor(vendor: Path) -> dict[str, Any]:
    root = vendor.expanduser().resolve()
    lock_path = root / "UPSTREAM_LOCK.json"
    license_path = root / "LICENSE"
    if not root.is_dir():
        return {
            "name": "hbg_vendor_lock",
            "status": "blocked",
            "path": str(root),
            "note": "vendor directory is missing",
        }
    if not lock_path.is_file():
        return {
            "name": "hbg_vendor_lock",
            "status": "blocked",
            "path": str(lock_path),
            "note": "UPSTREAM_LOCK.json is missing",
        }
    if not license_path.is_file():
        return {
            "name": "hbg_vendor_lock",
            "status": "blocked",
            "path": str(license_path),
            "note": "MIT LICENSE is missing",
        }
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return {
            "name": "hbg_vendor_lock",
            "status": "blocked",
            "path": str(lock_path),
            "note": f"invalid lock file: {error}",
        }
    files = lock.get("files")
    if not isinstance(files, dict) or not files:
        return {
            "name": "hbg_vendor_lock",
            "status": "blocked",
            "path": str(lock_path),
            "note": "lock file has no file hashes",
        }
    mismatches: list[str] = []
    for relative, expected in sorted(files.items()):
        target = root / relative
        if not target.is_file():
            mismatches.append(f"missing: {relative}")
            continue
        observed = _sha256(target)
        if observed != expected:
            mismatches.append(f"hash mismatch: {relative}")
    if mismatches:
        return {
            "name": "hbg_vendor_lock",
            "status": "blocked",
            "path": str(lock_path),
            "note": "; ".join(mismatches[:12]),
        }
    return {
        "name": "hbg_vendor_lock",
        "status": "ready",
        "path": str(lock_path),
        "commit": lock.get("commit"),
        "verified_files": len(files),
    }


def verify_hbg_direct_reuse(vendor: Path) -> dict[str, Any]:
    root = vendor.expanduser().resolve()
    lock_path = root / "UPSTREAM_LOCK.json"
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return {
            "name": "hbg_direct_reuse",
            "status": "blocked",
            "path": str(lock_path),
            "note": f"cannot read direct-reuse list: {error}",
        }
    entries = lock.get("direct_reuse")
    if not isinstance(entries, list) or not entries:
        return {
            "name": "hbg_direct_reuse",
            "status": "blocked",
            "path": str(lock_path),
            "note": "direct_reuse allowlist is missing",
        }
    missing = [str(item) for item in entries if not (root / str(item)).is_file()]
    return {
        "name": "hbg_direct_reuse",
        "status": "blocked" if missing else "ready",
        "path": str(root),
        "verified_files": len(entries) - len(missing),
        "note": f"missing direct-reuse files: {', '.join(missing)}" if missing else "",
    }


def project_checks(project: Path, release_id: str | None, public_release: bool) -> list[dict[str, Any]]:
    root = project.expanduser().resolve()
    checks: list[dict[str, Any]] = []
    try:
        workflow = project_workflow(root)
    except StyleProfileError as error:
        return [
            {
                "name": "project_style_profile",
                "status": "blocked",
                "path": str(root),
                "note": str(error),
            }
        ]
    style = workflow["style_profile"]
    checks.append(
        {
            "name": "project_style_profile",
            "status": "ready",
            "path": str(style.path),
            "style_profile_id": style.style_id,
            "release_profile_id": workflow["release_profile_id"],
            "generation_lane": workflow["generation_lane"],
        }
    )
    if not public_release:
        return checks
    try:
        profile_path = (
            FACTORY_ROOT
            / "config/release_profiles"
            / f"{workflow['release_profile_id']}.json"
        )
        state = evaluate_workflow_state(
            root,
            ReleaseProfile.load(profile_path),
            release_id=release_id,
        )
    except (ContractError, OSError, ValueError, StyleProfileError) as error:
        checks.append(
            {
                "name": "project_public_release_gate",
                "status": "blocked",
                "path": str(root),
                "note": str(error),
            }
        )
    else:
        checks.append(
            {
                "name": "project_public_release_gate",
                "status": "ready" if state["ready_to_publish"] else "blocked",
                "path": str(root),
                "derived_state": state["derived_state"],
                "missing_publish_approvals": state["missing_publish_approvals"],
            }
        )
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--profile",
        choices=tuple(PROFILE_ORDER),
        default="planning",
    )
    parser.add_argument("--project", type=Path)
    parser.add_argument("--release-id")
    parser.add_argument("--vendor-root", type=Path)
    args = parser.parse_args()

    profile = args.profile
    checks: list[dict[str, Any]] = [
        {
            "name": "python_version",
            "status": "ready" if sys.version_info >= (3, 11) else "blocked",
            "path": sys.executable,
            "observed": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "required": ">=3.11",
        },
        executable_check("node", required=_required(profile, "local-render")),
        executable_check("npx", required=_required(profile, "local-render")),
        executable_check("ffmpeg", required=_required(profile, "local-render")),
        executable_check("ffprobe", required=_required(profile, "local-render")),
        python_module_check(
            "PIL",
            label="python_module:PIL",
            required=_required(profile, "local-render"),
        ),
        python_module_check(
            "jsonschema",
            label="python_module:jsonschema",
            required=_required(profile, "production"),
        ),
        edge_tts_check(required=_required(profile, "production")),
        phase4_audio_cli_check(),
        phase5_7_runtime_check(),
    ]

    vendor = find_hbg_vendor(args.vendor_root)
    checks.append(verify_hbg_vendor(vendor))
    checks.append(verify_hbg_direct_reuse(vendor))
    checks.append(
        {
            "name": "host_imagegen",
            "status": "ready",
            "path": "host ImageGen tool",
            "note": "Actual generation is proven by provider call IDs and output hashes, not by doctor.",
        }
    )

    free_gib = shutil.disk_usage(Path.cwd()).free / (1024**3)
    disk_required = _required(profile, "local-render")
    disk_ready = free_gib >= 12
    checks.append(
        {
            "name": "disk_free",
            "status": _status(disk_ready, disk_required),
            "free_gib": round(free_gib, 2),
            "required_gib": 12,
        }
    )

    if args.project is not None:
        checks.extend(
            project_checks(
                args.project,
                args.release_id,
                public_release=profile == "public-release",
            )
        )
    elif profile == "public-release":
        checks.append(
            {
                "name": "project_public_release_gate",
                "status": "blocked",
                "path": "--project is required",
            }
        )

    overall = "blocked" if any(check["status"] == "blocked" for check in checks) else "ready"
    report = {
        "profile": profile,
        "pipeline": "classic-narrator-hbg-v1",
        "overall": overall,
        "checks": checks,
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"book-video factory ({profile}): {overall}")
        for check in checks:
            detail = check.get("path") or check.get("free_gib") or ""
            print(f"[{str(check['status']).upper():7}] {check['name']}: {detail}")
    return 1 if overall == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
