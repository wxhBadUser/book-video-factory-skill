from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, UnidentifiedImageError

from book_video_factory.hbg_bridge.runner import repository_root
from book_video_factory.hbg_bridge.shell import bash_executable as _bash_executable
from book_video_factory.hbg_bridge.shell import path_for_bash as _path_for_bash
from book_video_factory.manifests import artifact, sha256_file, write_stage_manifest
from book_video_factory.style_profiles import project_workflow

from .asset_registry import (
    VisualAssetRegistrationError,
    VerifiedVisualAssets,
    verify_visual_asset_manifest,
)


class VisualReviewError(RuntimeError):
    pass


@dataclass(frozen=True)
class VisualReviewResult:
    status: str
    review_digest: str
    contact_sheet_path: Path
    report_path: Path
    stage_manifest_path: Path


_CONTACT_RELATIVE = "03_images_生成图片/LOOKDEV_CONTACT_SHEET.jpg"
_ANCHOR_CONTACT_RELATIVE = "03_images_生成图片/ANCHOR_CONTACT_SHEET.jpg"
_REPORT_RELATIVE = "03_images_生成图片/VISUAL_REVIEW_REPORT.json"
_SEMANTIC_CHECKS = [
    "each image represents its assigned task rather than an adjacent event",
    "required entities are visible and forbidden entities are absent",
    "character identity, period, geography and recurring production design are consistent",
    "the twelve LookDev categories are visibly distinct and collectively cover the book world",
]
_REALITY_CHECKS = [
    "hands, wrists, limbs and body ownership are anatomically coherent",
    "props have correct front/back orientation, functional direction, grip and contact",
    "gravity, support, water, reflections, animals and physical interactions are believable",
    "clothing, architecture and objects are historically and geographically plausible",
]
_AESTHETIC_CHECKS = [
    "the result matches the approved per-book visual profile rather than a generic AI oil-painting filter",
    "composition, tonal hierarchy, material handling and subtitle-safe framing meet the literary-cinematic kernel",
    "style references influence broad language only and are not copied in identity, layout or exact composition",
]


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VisualReviewError(f"{label} is unreadable: {error}") from error
    if not isinstance(value, dict):
        raise VisualReviewError(f"{label} must be a JSON object")
    return value


def _verify_hbg_contact_sheet_script() -> tuple[Path, str, str]:
    repo = repository_root()
    vendor = repo / "vendor/hbg-life-simulation"
    lock = _load_json(vendor / "UPSTREAM_LOCK.json", "HBG upstream lock")
    commit = lock.get("commit")
    expected = lock.get("files", {}).get("scripts/make_contact_sheet.sh")
    script = vendor / "scripts/make_contact_sheet.sh"
    if not isinstance(commit, str) or len(commit) != 40 or not isinstance(expected, str):
        raise VisualReviewError("HBG upstream lock does not bind the contact-sheet script")
    if not script.is_file() or sha256_file(script) != expected:
        raise VisualReviewError("HBG contact-sheet script hash mismatch")
    return script, commit, expected


def _stage_recorded_at(root: Path, visual_manifest: Mapping[str, Any]) -> str:
    relative = visual_manifest.get("stage_manifest_path")
    if not isinstance(relative, str):
        raise VisualReviewError("visual stage manifest has no stage manifest path")
    stage = _load_json(root / relative, "visual stage stage manifest")
    recorded_at = stage.get("recorded_at")
    if not isinstance(recorded_at, str) or not recorded_at:
        raise VisualReviewError("visual stage has no deterministic recorded_at")
    return recorded_at


def _run_hbg_contact_sheet(
    script: Path, output: Path, image_paths: list[Path], root: Path, columns: int = 4
) -> None:
    bash = _bash_executable()
    arguments = [
        _path_for_bash(script),
        _path_for_bash(output),
        str(columns),
        *[_path_for_bash(path) for path in image_paths],
    ]
    command = [bash, *arguments]
    if os.name == "nt" and Path(bash).name.lower() == "bash.exe":
        command = [
            bash,
            "-c",
            'pwd() { builtin pwd -W; }; export -f pwd; exec "$@"',
            "hbg-contact-sheet",
            *arguments,
        ]
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise VisualReviewError(f"HBG contact-sheet execution failed: {error}") from error
    if completed.returncode != 0:
        raise VisualReviewError(
            f"HBG contact-sheet execution failed with {completed.returncode}: "
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )


def _label_contact_sheet(path: Path, task_ids: list[str], columns: int = 5) -> None:
    """Add deterministic task labels on top of the HBG-rendered anchor sheet."""
    try:
        with Image.open(path) as source:
            image = source.convert("RGB")
        width, height = image.size
        rows = (len(task_ids) + columns - 1) // columns
        cell_width = max(1, (width - 16 - (columns - 1) * 4) // columns)
        cell_height = max(1, (height - 16 - (rows - 1) * 4) // rows)
        from PIL import ImageDraw, ImageFont

        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default(size=24)
        for index, task_id in enumerate(task_ids):
            column = index % columns
            row = index // columns
            x = 8 + column * (cell_width + 4) + 10
            y = 8 + row * (cell_height + 4) + 10
            box = draw.textbbox((x, y), task_id, font=font)
            draw.rectangle((box[0] - 6, box[1] - 4, box[2] + 6, box[3] + 4), fill=(12, 12, 14))
            draw.text((x, y), task_id, fill=(245, 245, 245), font=font)
        image.save(path, format="JPEG", quality=94)
    except (OSError, UnidentifiedImageError) as error:
        raise VisualReviewError(f"could not label HBG contact sheet: {error}") from error


def _run_labeled_hbg_contact_sheet(
    script: Path,
    output: Path,
    image_paths: list[Path],
    task_ids: list[str],
    root: Path,
    columns: int = 5,
) -> None:
    _run_hbg_contact_sheet(script, output, image_paths, root, columns=columns)
    _label_contact_sheet(output, task_ids, columns=columns)


def _validate_contact_sheet(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
            fmt = image.format
    except (OSError, UnidentifiedImageError) as error:
        raise VisualReviewError("HBG contact sheet is not a decodable image") from error
    if fmt != "JPEG" or width <= 0 or height <= 0:
        raise VisualReviewError("HBG contact sheet must be a non-empty JPEG")
    return width, height


def _asset_evidence(verified: VerifiedVisualAssets, task: dict[str, Any]) -> dict[str, Any]:
    asset = verified.assets_by_task[task["task_id"]]
    return {
        "task_id": task["task_id"],
        "category": task["category"],
        "path": asset["path"],
        "sha256": asset["sha256"],
        "prompt_sha256": asset["prompt_sha256"],
        "palette_id": asset["palette_id"],
        "lighting_id": asset["lighting_id"],
        "machine_diagnostic": asset["machine_diagnostic"],
    }


def _diagnostic_summary(assets: list[dict[str, Any]]) -> dict[str, Any]:
    by_palette: dict[str, dict[str, int]] = {}
    passed = 0
    warned = 0
    for item in assets:
        status = item["machine_diagnostic"]["status"]
        passed += status == "pass"
        warned += status == "warn"
        palette = by_palette.setdefault(item["palette_id"], {"pass": 0, "warn": 0})
        palette[status] += 1
    return {
        "pass_count": passed,
        "warn_count": warned,
        "by_palette": by_palette,
        "interpretation": "Warnings are diagnostic evidence only and do not equal aesthetic failure or human approval.",
    }


def _snapshot(root: Path, relatives: list[str]) -> dict[str, bytes | None]:
    return {relative: (root / relative).read_bytes() if (root / relative).is_file() else None for relative in relatives}


def _restore(root: Path, originals: Mapping[str, bytes | None]) -> None:
    for relative, content in originals.items():
        target = root / relative
        if content is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)


def _contact_record(path: Path, relative: str, columns: int, rows: int) -> dict[str, Any]:
    width, height = _validate_contact_sheet(path)
    return {
        "path": relative,
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "width": width,
        "height": height,
        "columns": columns,
        "rows": rows,
    }


def _expected_report(
    *,
    verified: VerifiedVisualAssets,
    asset_manifest_path: Path,
    asset_manifest_hash: str,
    hbg_commit: str,
    script_hash: str,
    anchor_evidence: list[dict[str, Any]],
    lookdev_evidence: list[dict[str, Any]],
    all_evidence: list[dict[str, Any]],
    review_digest: str,
    contact_record: dict[str, Any],
    anchor_contact_record: dict[str, Any],
    stage_relative: str,
) -> dict[str, Any]:
    return {
        "schema_version": "visual-review-report.v1",
        "release_id": verified.visual_stage_manifest["release_id"],
        "visual_stage_digest": verified.visual_stage_manifest["visual_stage_digest"],
        "review_digest": review_digest,
        "visual_asset_manifest": {
            "path": "03_images_生成图片/VISUAL_ASSET_MANIFEST.json",
            "sha256": asset_manifest_hash,
        },
        "generator": {
            "repository": "Mr-funny/hbg-life-simulation",
            "commit": hbg_commit,
            "script": "vendor/hbg-life-simulation/scripts/make_contact_sheet.sh",
            "script_sha256": script_hash,
            "command": [
                "make_contact_sheet.sh",
                _CONTACT_RELATIVE,
                "4",
                *[item["path"] for item in lookdev_evidence],
            ],
        },
        "anchor_generator": {
            "repository": "Mr-funny/hbg-life-simulation",
            "commit": hbg_commit,
            "script": "vendor/hbg-life-simulation/scripts/make_contact_sheet.sh",
            "script_sha256": script_hash,
            "command": [
                "make_contact_sheet.sh",
                _ANCHOR_CONTACT_RELATIVE,
                "5",
                *[item["path"] for item in anchor_evidence],
            ],
            "groups": ["people", "animals", "crowd", "scenes", "objects"],
        },
        "anchor_assets": anchor_evidence,
        "lookdev_assets": lookdev_evidence,
        "diagnostic_summary": _diagnostic_summary(all_evidence),
        "contact_sheet": contact_record,
        "anchor_contact_sheet": anchor_contact_record,
        "semantic_review": {"status": "pending", "required_checks": _SEMANTIC_CHECKS},
        "reality_review": {"status": "pending", "required_checks": _REALITY_CHECKS},
        "aesthetic_review": {"status": "pending", "required_checks": _AESTHETIC_CHECKS},
        "human_review_status": "pending",
        "stage_manifest_path": stage_relative,
        "next_stage_status": "blocked_by_visual_review",
    }


_STAGE_CHECKS = [
    {"id": "all_visual_tasks_registered", "result": "pass", "severity": "error"},
    {"id": "hbg_contact_sheet_generated", "result": "pass", "severity": "error"},
    {"id": "hbg_anchor_contact_sheet_generated", "result": "pass", "severity": "error"},
    {"id": "machine_diagnostics_recorded", "result": "pass", "severity": "error"},
    {"id": "human_review_pending", "result": "pass", "severity": "info"},
]


def _expected_stage_manifest(
    root: Path,
    *,
    release_id: str,
    recorded_at: str,
    manifest_id: str,
    contact_path: Path,
    anchor_contact_path: Path,
    report_path: Path,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "manifest_id": manifest_id,
        "project_id": root.name,
        "stage": "visual_review",
        "release_id": release_id,
        "release_profile_id": str(project_workflow(root)["release_profile_id"]),
        "producer": {"tool": "book-video-factory-visual-review"},
        "recorded_at": recorded_at,
        "status": "success",
        "inputs": [
            artifact(root, "visual_stage_manifest", root / "03_images_生成图片/VISUAL_STAGE_MANIFEST.json"),
            artifact(root, "visual_asset_manifest", root / "03_images_生成图片/VISUAL_ASSET_MANIFEST.json"),
        ],
        "outputs": [
            artifact(root, "lookdev_contact_sheet", contact_path),
            artifact(root, "anchor_contact_sheet", anchor_contact_path),
            artifact(root, "visual_review_report", report_path),
        ],
        "checks": _STAGE_CHECKS,
        "approval_event_ids": [],
        "cost_event_ids": [],
    }


def _verify_rendered_contact(
    *,
    root: Path,
    script: Path,
    image_paths: list[Path],
    expected_record: dict[str, Any],
) -> None:
    temp_root = Path(tempfile.mkdtemp(prefix=".visual-review-verify-", dir=root))
    try:
        rendered = temp_root / "LOOKDEV_CONTACT_SHEET.jpg"
        _run_hbg_contact_sheet(script, rendered, image_paths, root, columns=4)
        actual = _contact_record(rendered, _CONTACT_RELATIVE, 4, 3)
        # The temporary path is intentionally normalized to the canonical output
        # before comparison. All other image bytes and layout metadata must match.
        if actual != expected_record:
            raise VisualReviewError("HBG contact-sheet render does not match the recorded deterministic review")
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def _verify_rendered_labeled_contact(
    *,
    root: Path,
    script: Path,
    image_paths: list[Path],
    task_ids: list[str],
    expected_record: dict[str, Any],
) -> None:
    temp_root = Path(tempfile.mkdtemp(prefix=".visual-review-anchor-verify-", dir=root))
    try:
        rendered = temp_root / "ANCHOR_CONTACT_SHEET.jpg"
        _run_labeled_hbg_contact_sheet(script, rendered, image_paths, task_ids, root)
        actual = _contact_record(rendered, _ANCHOR_CONTACT_RELATIVE, 5, 6)
        if actual != expected_record:
            raise VisualReviewError("HBG labeled anchor contact-sheet render does not match the recorded deterministic review")
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def _verify_existing(
    root: Path,
    report: dict[str, Any],
    *,
    expected_report: dict[str, Any],
    expected_digest: str,
    recorded_at: str,
    manifest_id: str,
    stage_relative: str,
    script: Path,
    image_paths: list[Path],
    anchor_image_paths: list[Path],
    anchor_task_ids: list[str],
    verify_contact_render: bool,
) -> VisualReviewResult:
    if report.get("review_digest") != expected_digest:
        raise VisualReviewError("a different visual review already exists")
    contact_record = report.get("contact_sheet")
    anchor_contact_record = report.get("anchor_contact_sheet")
    if not isinstance(contact_record, dict) or contact_record.get("path") != _CONTACT_RELATIVE:
        raise VisualReviewError("visual review contact sheet record is invalid")
    if not isinstance(anchor_contact_record, dict) or anchor_contact_record.get("path") != _ANCHOR_CONTACT_RELATIVE:
        raise VisualReviewError("visual review anchor contact sheet record is invalid")
    contact_path = root / _CONTACT_RELATIVE
    anchor_contact_path = root / _ANCHOR_CONTACT_RELATIVE
    if not contact_path.is_file():
        raise VisualReviewError("visual review contact sheet is missing")
    if not anchor_contact_path.is_file():
        raise VisualReviewError("visual review anchor contact sheet is missing")
    if _contact_record(contact_path, _CONTACT_RELATIVE, 4, 3) != contact_record:
        raise VisualReviewError("visual review contact sheet hash or metadata mismatch")
    if _contact_record(anchor_contact_path, _ANCHOR_CONTACT_RELATIVE, 5, 6) != anchor_contact_record:
        raise VisualReviewError("visual review anchor contact sheet hash or metadata mismatch")
    if report != expected_report:
        raise VisualReviewError("visual review report does not match deterministic current evidence")

    report_path = root / _REPORT_RELATIVE
    stage_path = root / stage_relative
    if report.get("stage_manifest_path") != stage_relative or not stage_path.is_file():
        raise VisualReviewError("visual review stage manifest path is invalid")
    stage = _load_json(stage_path, "visual review stage manifest")
    expected_stage = _expected_stage_manifest(
        root,
        release_id=str(report["release_id"]),
        recorded_at=recorded_at,
        manifest_id=manifest_id,
        contact_path=contact_path,
        anchor_contact_path=anchor_contact_path,
        report_path=report_path,
    )
    if stage != expected_stage:
        raise VisualReviewError("visual review stage manifest does not match deterministic evidence")
    if verify_contact_render:
        _verify_rendered_contact(
            root=root,
            script=script,
            image_paths=image_paths,
            expected_record=contact_record,
        )
        _verify_rendered_labeled_contact(
            root=root,
            script=script,
            image_paths=anchor_image_paths,
            task_ids=anchor_task_ids,
            expected_record=anchor_contact_record,
        )
    return VisualReviewResult("unchanged", expected_digest, contact_path, report_path, stage_path)


def build_visual_review(project: Path, *, verify_contact_render: bool = False) -> VisualReviewResult:
    root = project.expanduser().resolve()
    try:
        verified = verify_visual_asset_manifest(root, require_complete=True)
    except VisualAssetRegistrationError as error:
        raise VisualReviewError(str(error)) from error
    tasks = list(verified.tasks)
    lookdev_tasks = [task for task in tasks if task.get("task_kind") == "lookdev"]
    anchor_tasks = [task for task in tasks if task.get("task_kind") != "lookdev"]
    if len(lookdev_tasks) != 12:
        raise VisualReviewError("visual review requires exactly twelve LookDev assets")
    if not anchor_tasks:
        raise VisualReviewError("visual review requires mandatory anchor assets")

    script, hbg_commit, script_hash = _verify_hbg_contact_sheet_script()
    asset_manifest_path = root / "03_images_生成图片/VISUAL_ASSET_MANIFEST.json"
    asset_manifest_hash = sha256_file(asset_manifest_path)
    all_evidence = [_asset_evidence(verified, task) for task in tasks]
    anchor_evidence = [_asset_evidence(verified, task) for task in anchor_tasks]
    lookdev_evidence = [_asset_evidence(verified, task) for task in lookdev_tasks]
    digest_payload = {
        "release_id": verified.visual_stage_manifest["release_id"],
        "visual_stage_digest": verified.visual_stage_manifest["visual_stage_digest"],
        "asset_manifest_sha256": asset_manifest_hash,
        "assets": all_evidence,
        "hbg_commit": hbg_commit,
        "hbg_contact_sheet_script_sha256": script_hash,
        "columns": 4,
    }
    review_digest = hashlib.sha256(_canonical_bytes(digest_payload)).hexdigest()
    recorded_at = _stage_recorded_at(root, verified.visual_stage_manifest)
    manifest_id = f"visual-review-{review_digest[:16]}"
    stage_relative = (
        "manifests/stages/visual_review/"
        f"{recorded_at.replace(':', '-').replace('+', '_')}-{manifest_id}.json"
    )
    report_path = root / _REPORT_RELATIVE
    contact_path = root / _CONTACT_RELATIVE
    anchor_contact_path = root / _ANCHOR_CONTACT_RELATIVE
    image_paths = [root / verified.assets_by_task[task["task_id"]]["path"] for task in lookdev_tasks]
    anchor_image_paths = [root / verified.assets_by_task[task["task_id"]]["path"] for task in anchor_tasks]
    anchor_task_ids = [task["task_id"] for task in anchor_tasks]

    if report_path.is_file():
        report = _load_json(report_path, "visual review report")
        contact_record = report.get("contact_sheet")
        if not isinstance(contact_record, dict):
            raise VisualReviewError("visual review contact sheet record is invalid")
        expected_report = _expected_report(
            verified=verified,
            asset_manifest_path=asset_manifest_path,
            asset_manifest_hash=asset_manifest_hash,
            hbg_commit=hbg_commit,
            script_hash=script_hash,
            anchor_evidence=anchor_evidence,
            lookdev_evidence=lookdev_evidence,
            all_evidence=all_evidence,
            review_digest=review_digest,
            contact_record=contact_record,
            anchor_contact_record=report.get("anchor_contact_sheet", {}),
            stage_relative=stage_relative,
        )
        return _verify_existing(
            root,
            report,
            expected_report=expected_report,
            expected_digest=review_digest,
            recorded_at=recorded_at,
            manifest_id=manifest_id,
            stage_relative=stage_relative,
            script=script,
            image_paths=image_paths,
            anchor_image_paths=anchor_image_paths,
            anchor_task_ids=anchor_task_ids,
            verify_contact_render=verify_contact_render,
        )
    if contact_path.exists() or anchor_contact_path.exists():
        raise VisualReviewError("refusing to overwrite an unbound visual review contact sheet")

    transaction_relatives = [_CONTACT_RELATIVE, _ANCHOR_CONTACT_RELATIVE, _REPORT_RELATIVE, stage_relative]
    originals = _snapshot(root, transaction_relatives)
    temp_root: Path | None = None
    try:
        temp_root = Path(tempfile.mkdtemp(prefix=".visual-review-", dir=root))
        temp_contact = temp_root / "LOOKDEV_CONTACT_SHEET.jpg"
        temp_anchor_contact = temp_root / "ANCHOR_CONTACT_SHEET.jpg"
        _run_hbg_contact_sheet(script, temp_contact, image_paths, root, columns=4)
        _run_labeled_hbg_contact_sheet(script, temp_anchor_contact, anchor_image_paths, anchor_task_ids, root)
        contact_record = _contact_record(temp_contact, _CONTACT_RELATIVE, 4, 3)
        anchor_contact_record = _contact_record(temp_anchor_contact, _ANCHOR_CONTACT_RELATIVE, 5, 6)
        report = _expected_report(
            verified=verified,
            asset_manifest_path=asset_manifest_path,
            asset_manifest_hash=asset_manifest_hash,
            hbg_commit=hbg_commit,
            script_hash=script_hash,
            anchor_evidence=anchor_evidence,
            lookdev_evidence=lookdev_evidence,
            all_evidence=all_evidence,
            review_digest=review_digest,
            contact_record=contact_record,
            anchor_contact_record=anchor_contact_record,
            stage_relative=stage_relative,
        )
        report_bytes = _pretty_bytes(report)
        contact_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        temp_report = temp_root / "VISUAL_REVIEW_REPORT.json"
        temp_report.write_bytes(report_bytes)
        os.replace(temp_contact, contact_path)
        os.replace(temp_anchor_contact, anchor_contact_path)
        os.replace(temp_report, report_path)
        stage_path = write_stage_manifest(
            root,
            stage="visual_review",
            release_id=str(verified.visual_stage_manifest["release_id"]),
            release_profile_id=str(project_workflow(root)["release_profile_id"]),
            inputs=[
                ("visual_stage_manifest", root / "03_images_生成图片/VISUAL_STAGE_MANIFEST.json"),
                ("visual_asset_manifest", asset_manifest_path),
            ],
            outputs=[
                ("lookdev_contact_sheet", contact_path),
                ("anchor_contact_sheet", anchor_contact_path),
                ("visual_review_report", report_path),
            ],
            checks=_STAGE_CHECKS,
            producer="book-video-factory-visual-review",
            manifest_id=manifest_id,
            recorded_at=recorded_at,
        )
        if stage_path.relative_to(root).as_posix() != stage_relative:
            raise VisualReviewError("visual review stage manifest path is not deterministic")
        expected_stage = _expected_stage_manifest(
            root,
            release_id=str(verified.visual_stage_manifest["release_id"]),
            recorded_at=recorded_at,
            manifest_id=manifest_id,
            contact_path=contact_path,
            anchor_contact_path=anchor_contact_path,
            report_path=report_path,
        )
        if _load_json(stage_path, "visual review stage manifest") != expected_stage:
            raise VisualReviewError("visual review stage manifest does not match deterministic evidence")
        if verify_contact_render:
            _verify_rendered_contact(
                root=root,
                script=script,
                image_paths=image_paths,
                expected_record=contact_record,
            )
            _verify_rendered_labeled_contact(
                root=root,
                script=script,
                image_paths=anchor_image_paths,
                task_ids=anchor_task_ids,
                expected_record=anchor_contact_record,
            )
        return VisualReviewResult("created", review_digest, contact_path, report_path, stage_path)
    except Exception:
        _restore(root, originals)
        raise
    finally:
        if temp_root is not None:
            shutil.rmtree(temp_root, ignore_errors=True)
