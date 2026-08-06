#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.content_package import (
    ContentPackageConflict,
    ContentPackageError,
    compile_content_package,
)
from book_video_factory.project import initialize_project

FILES = {
    "source_manifest": "SOURCE_MANIFEST.json",
    "research": "BOOK_RESEARCH.json",
    "creative_decision": "CREATIVE_DECISION.json",
    "fate_anchors": "FATE_ANCHORS.json",
    "event_cards": "EVENT_CARDS.json",
    "script": "SCRIPT_PACKAGE.json",
    "quality": "CONTENT_QUALITY_REPORT.json",
    "originality": "ORIGINALITY_REPORT.json",
    "blind_review": "BLIND_REVIEW.json",
}


def _read_inputs(directory: Path) -> dict[str, dict]:
    values: dict[str, dict] = {}
    for key, filename in FILES.items():
        path = directory / filename
        if not path.is_file():
            raise ContentPackageError(f"missing input artifact: {path}")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ContentPackageError(f"cannot read {path}: {error}") from error
        if not isinstance(value, dict):
            raise ContentPackageError(f"input artifact must be a JSON object: {path}")
        values[key] = value
    return values


def _validate_in_temporary_project(project: Path, inputs: dict, release_id: str) -> str:
    contract = json.loads((project / "project.json").read_text(encoding="utf-8"))
    book = contract.get("book") or {}
    with tempfile.TemporaryDirectory(prefix="book-video-phase1-validate-") as temp:
        temp_project = initialize_project(
            Path(temp) / "warehouse",
            str(contract.get("project_id") or project.name),
            str(book.get("title") or "validation-book"),
            str(book.get("author") or "validation-author"),
            qualification_scope=str(
                (contract.get("workflow") or {}).get("qualification_scope", "production")
            ),
        )
        result = compile_content_package(
            temp_project, release_id=release_id, source_root=project, **inputs
        )
        return result.package_digest


def _emit_error(error_type: str, message: str) -> None:
    print(
        json.dumps({"status": "failed", "error_type": error_type, "message": message}, ensure_ascii=False),
        file=sys.stderr,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate or compile one formal single-narrator Phase 1 content package"
    )
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    project = args.project.expanduser().resolve()
    input_dir = args.input_dir.expanduser().resolve()
    try:
        inputs = _read_inputs(input_dir)
        if args.validate_only:
            digest = _validate_in_temporary_project(project, inputs, args.release_id)
            print(json.dumps({"status": "valid", "package_digest": digest}, ensure_ascii=False))
            return 0
        result = compile_content_package(project, release_id=args.release_id, **inputs)
        print(json.dumps({
            "status": result.status,
            "package_digest": result.package_digest,
            "manifest_path": str(result.manifest_path),
            "stage_manifest_path": str(result.stage_manifest_path),
        }, ensure_ascii=False))
        return 0
    except ContentPackageConflict as error:
        _emit_error("conflict", str(error))
        return 3
    except ContentPackageError as error:
        _emit_error("validation", str(error))
        return 2
    except Exception as error:  # pragma: no cover - defensive CLI boundary
        _emit_error("execution", str(error))
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
