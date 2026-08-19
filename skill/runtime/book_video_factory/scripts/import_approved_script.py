#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.approved_script_import import (
    ApprovedScriptImportError,
    import_approved_content_package,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Revalidate an approved script for a new unapproved project release")
    parser.add_argument("--source-project", required=True, type=Path)
    parser.add_argument("--target-project", required=True, type=Path)
    parser.add_argument("--source-release-id", required=True)
    parser.add_argument("--target-release-id", required=True)
    args = parser.parse_args()
    try:
        result = import_approved_content_package(
            args.source_project, args.target_project,
            source_release_id=args.source_release_id,
            target_release_id=args.target_release_id,
        )
    except ApprovedScriptImportError as error:
        print(json.dumps({"status": "failed", "message": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({
        "status": result.status,
        "content_manifest": str(result.content_result.manifest_path),
        "import_evidence": str(result.evidence_path),
        "next_stage_status": "awaiting_script_approval",
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
