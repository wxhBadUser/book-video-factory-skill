from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from book_video_factory.locked_script import (
    LockedScriptError,
    load_locked_script,
    lock_path,
    verify_locked_script,
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_locked_project(base: Path, *, name: str = "pilot") -> Path:
    project = base / "warehouse" / "projects" / name
    _write_json(project / "01_research_资料搜集/SOURCE_MANIFEST.json", {
        "schema_version": "1.0",
        "rights_status": "cleared",
        "public_release_allowed": True,
        "source_dir": "source",
        "files": [],
    })
    _write_json(project / "01_research_资料搜集/SOURCE_SANITIZATION_REPORT.json", {
        "schema_version": "source-sanitization-report.v1",
        "rights_status": "cleared",
        "public_release_allowed": True,
        "detected_source_declarations": [],
    })
    script_text = "V2 locked narration."
    script = project / "02_story_script_故事脚本/SCRIPT_RELEASE.md"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(script_text, encoding="utf-8")
    _write_json(lock_path(project), {
        "schema_version": "locked-script.v2",
        "release_id": "release-1",
        "project_id": name,
        "title": "Pilot",
        "author": "Test",
        "language": "zh",
        "script_path": "02_story_script_故事脚本/SCRIPT_RELEASE.md",
        "script_sha256": _sha(script_text),
        "source_sha256": "a" * 64,
        "rights_state": "cleared",
        "lock_status": "locked",
        "locked_at": "2026-08-20T00:00:00Z",
    })
    return project


class TestLockedScript(unittest.TestCase):
    def test_valid_lock_verifies_as_script_locked(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_locked_project(Path(temp))
            status = verify_locked_script(project)
            self.assertEqual(status["status"], "script_locked")
            self.assertTrue(status["locked"])
            self.assertTrue(status["hash_match"])
            self.assertEqual(load_locked_script(project)["project_id"], "pilot")

    def test_missing_lock_is_not_a_block(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_locked_project(Path(temp))
            lock_path(project).unlink()
            status = verify_locked_script(project)
            self.assertEqual(status["status"], "missing_locked_script")
            self.assertFalse(status["locked"])
            self.assertIsNone(load_locked_script(project))

    def test_script_hash_mismatch_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_locked_project(Path(temp))
            script = project / "02_story_script_故事脚本/SCRIPT_RELEASE.md"
            script.write_text("tampered narration.", encoding="utf-8")
            status = verify_locked_script(project)
            self.assertEqual(status["status"], "blocked_by_script_integrity")
            self.assertFalse(status["hash_match"])
            with self.assertRaises(LockedScriptError):
                load_locked_script(project)

    def test_path_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_locked_project(Path(temp))
            data = json.loads(lock_path(project).read_text(encoding="utf-8"))
            data["script_path"] = "../outside/SCRIPT.md"
            lock_path(project).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            status = verify_locked_script(project)
            self.assertEqual(status["status"], "blocked_by_script_integrity")

    def test_uncleared_rights_block(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_locked_project(Path(temp))
            _write_json(project / "01_research_资料搜集/SOURCE_MANIFEST.json", {
                "schema_version": "1.0",
                "rights_status": "cleared",
                "public_release_allowed": True,
                "source_dir": "source",
                "files": [],
            })
            _write_json(project / "01_research_资料搜集/SOURCE_SANITIZATION_REPORT.json", {
                "schema_version": "source-sanitization-report.v1",
                "rights_status": "in_review",
                "public_release_allowed": False,
                "detected_source_declarations": [],
            })
            status = verify_locked_script(project)
            self.assertEqual(status["status"], "blocked_rights")


if __name__ == "__main__":
    unittest.main()
