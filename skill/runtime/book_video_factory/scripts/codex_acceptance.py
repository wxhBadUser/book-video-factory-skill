#!/usr/bin/env python3
"""Offline acceptance test for the packaged Codex production workflow."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def _run(command: list[str], *, cwd: Path) -> tuple[int, str, str]:
    completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    return completed.returncode, completed.stdout, completed.stderr


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    checks: list[dict[str, Any]] = []

    required = [
        "AGENTS.md", "CODEX_AGENT.md", "FULL_PIPELINE.md", "ACCEPTANCE.md",
        "INSTALL.md", "TROUBLESHOOTING.md",
        "skill/scripts/bootstrap_workspace.py",
        "book_video_factory/scripts/run_full_pipeline.py",
        "book_video_factory/scripts/approve_final_master.py",
        "vendor/hbg-life-simulation/UPSTREAM_LOCK.json",
    ]
    missing = [item for item in required if not (root / item).is_file()]
    checks.append({"id": "codex_entry_files", "result": "pass" if not missing else "fail", "missing": missing})

    code, stdout, stderr = _run(
        [sys.executable, str(root / "book_video_factory/scripts/run_full_pipeline.py"), "verify-repository", "--root", str(root)],
        cwd=root,
    )
    checks.append({"id": "phase0_7_repository_scanners", "result": "pass" if code == 0 else "fail", "stderr": stderr.strip()})

    with tempfile.TemporaryDirectory(prefix="book-video-codex-acceptance-") as temporary:
        workspace = Path(temporary) / "workspace"
        bootstrap = root / "skill/scripts/bootstrap_workspace.py"
        command = [
            sys.executable, str(bootstrap), "--workspace", str(workspace),
            "--slug", "codex-acceptance-book", "--book-title", "验收书目", "--author", "验收作者",
        ]
        code, out, err = _run(command, cwd=root)
        project = workspace / "book_video_warehouse/projects/codex-acceptance-book"
        checks.append({"id": "bootstrap_codex_workspace", "result": "pass" if code == 0 and project.is_dir() else "fail", "stderr": err.strip()})
        runtime_required = [
            "book_video_factory/scripts/build_content_package.py",
            "book_video_factory/scripts/build_hbg_bridge.py",
            "book_video_factory/scripts/build_visual_stage.py",
            "book_video_factory/scripts/run_audio_stage.py",
            "book_video_factory/scripts/build_director_stage.py",
            "book_video_factory/scripts/register_scene_asset.py",
            "book_video_factory/scripts/run_render_stage.py",
            "book_video_factory/scripts/approve_final_master.py",
            "vendor/hbg-life-simulation/scripts/build_narration.mjs",
            "vendor/hbg-life-simulation/scripts/render_streaming_ffmpeg.mjs",
        ]
        runtime_missing = [item for item in runtime_required if not (workspace / item).is_file()]
        checks.append({"id": "installed_phase0_7_runtime", "result": "pass" if not runtime_missing else "fail", "missing": runtime_missing})
        status_cmd = [sys.executable, str(workspace / "book_video_factory/scripts/run_full_pipeline.py"), "status", "--project", str(project)]
        code, out, err = _run(status_cmd, cwd=workspace)
        try:
            status = json.loads(out)
        except json.JSONDecodeError:
            status = {}
        checks.append({
            "id": "new_project_fail_closed_status",
            "result": "pass" if code == 0 and status.get("status") == "awaiting_content_package" else "fail",
            "observed": status,
            "stderr": err.strip(),
        })
        protected = project / "SCRIPT_SOURCE.md"
        protected.write_text("USER LOCKED CONTENT\n", encoding="utf-8")
        code, _, err = _run(command, cwd=root)
        checks.append({
            "id": "bootstrap_preserves_user_project",
            "result": "pass" if code == 0 and protected.read_text(encoding="utf-8") == "USER LOCKED CONTENT\n" else "fail",
            "stderr": err.strip(),
        })

    failed = [item for item in checks if item["result"] != "pass"]
    report = {
        "schema_version": "codex-production-acceptance.v1",
        "scope": "offline-package-and-fail-closed-workflow",
        "status": "pass" if not failed else "fail",
        "checks": checks,
        "external_execution_not_claimed": ["edge-tts-network", "host-imagegen", "hyperframes", "full-length-ffmpeg-master"],
    }
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    print(text, end="")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(text, encoding="utf-8")
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
