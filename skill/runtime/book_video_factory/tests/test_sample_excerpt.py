from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from book_video_factory.manifests import record_approval, sha256_file
from book_video_factory.project import initialize_project
from book_video_factory.sample_excerpt import (
    SampleExcerptError,
    build_sample_excerpt,
    build_sample_narration_units,
)


class SampleExcerptTests(unittest.TestCase):
    def _project(self, base: Path, *, approved: bool) -> tuple[Path, Path]:
        project = initialize_project(base, "sample", "样书", "作者")
        script_dir = project / "02_story_script_故事脚本"
        release_id = "r2"
        package = {
            "schema_version": "phase1-content-package.v1",
            "release_id": release_id,
            "script": {
                "performance_version": {
                    "sections": [
                        {
                            "section_id": "S01",
                            "narrative_function": "hook",
                            "text": "甲" * 70 + "。" + "乙" * 70 + "。" + "丙" * 70 + "。",
                        },
                        {
                            "section_id": "S02",
                            "narrative_function": "revelation",
                            "text": "丁" * 70 + "。" + "戊" * 70 + "。",
                        },
                    ]
                }
            },
        }
        files = {
            "SCRIPT_PACKAGE.json": package,
            "SCRIPT_RELEASE.md": "# 冻结稿\n\n原文。\n",
            "SCRIPT_AUDIT.md": "# 审计\n\n通过。\n",
            "SCRIPT_METRICS.json": {"release_id": release_id},
            "SCRIPT_LOCK.json": {"release_id": release_id},
            "CONTENT_PACKAGE_MANIFEST.json": {"release_id": release_id},
        }
        for name, value in files.items():
            path = script_dir / name
            if isinstance(value, dict):
                path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            else:
                path.write_text(value, encoding="utf-8")
        if approved:
            subjects = [script_dir / name for name in (
                "SCRIPT_RELEASE.md", "SCRIPT_AUDIT.md", "SCRIPT_METRICS.json",
                "SCRIPT_LOCK.json", "CONTENT_PACKAGE_MANIFEST.json",
            )]
            record_approval(
                project,
                release_id=release_id,
                gate="script",
                decision="approved",
                reviewer="human",
                subjects=subjects,
                note="reviewed",
            )
        input_path = script_dir / "SAMPLE_EXCERPT_INPUT.json"
        input_path.write_text(json.dumps({
            "schema_version": "sample-excerpt-input.v1",
            "release_id": release_id,
            "sample_id": "sample-r2-60s",
            "target_duration_seconds": 60,
            "min_duration_seconds": 45,
            "max_duration_seconds": 90,
            "characters_per_minute": 180,
            "source": {
                "script_package_sha256": sha256_file(script_dir / "SCRIPT_PACKAGE.json"),
                "script_release_sha256": sha256_file(script_dir / "SCRIPT_RELEASE.md"),
            },
            "selection": [{"section_id": "S01", "sentence_start": 1, "sentence_end": 3}],
        }, ensure_ascii=False), encoding="utf-8")
        return project, input_path

    def test_builds_current_approval_bound_exact_excerpt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, input_path = self._project(Path(temporary), approved=True)
            result = build_sample_excerpt(project, input_path)
            payload = json.loads(result.path.read_text(encoding="utf-8"))
            self.assertEqual(result.status, "created")
            self.assertGreaterEqual(result.duration_seconds, 45)
            self.assertLessEqual(result.duration_seconds, 90)
            self.assertTrue(payload["integrity"]["no_rewrite"])
            self.assertEqual(payload["spoken_text"], "甲" * 70 + "。" + "乙" * 70 + "。" + "丙" * 70 + "。")
            self.assertEqual(
                payload["integrity"]["selection_text_sha256"],
                hashlib.sha256(payload["spoken_text"].encode("utf-8")).hexdigest(),
            )
            self.assertEqual(build_sample_excerpt(project, input_path).status, "unchanged")

    def test_rejects_selection_before_human_script_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, input_path = self._project(Path(temporary), approved=False)
            with self.assertRaisesRegex(SampleExcerptError, "current human script approval"):
                build_sample_excerpt(project, input_path)

    def test_rejects_noncontiguous_source_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, input_path = self._project(Path(temporary), approved=True)
            payload = json.loads(input_path.read_text(encoding="utf-8"))
            payload["selection"] = [
                {"section_id": "S01", "sentence_start": 1, "sentence_end": 1},
                {"section_id": "S02", "sentence_start": 2, "sentence_end": 2},
            ]
            input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(SampleExcerptError, "contiguous"):
                build_sample_excerpt(project, input_path)

    def test_derives_emotional_minimax_units_only_from_current_excerpt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, input_path = self._project(Path(temporary), approved=True)
            excerpt = build_sample_excerpt(project, input_path).path
            result = build_sample_narration_units(project, excerpt)
            payload = json.loads(result.path.read_text(encoding="utf-8"))
            self.assertEqual(result.status, "created")
            self.assertEqual(len(payload["units"]), 3)
            self.assertEqual(
                "".join(unit["spoken_text"] for unit in payload["units"]),
                "甲" * 70 + "。" + "乙" * 70 + "。" + "丙" * 70 + "。",
            )
            self.assertTrue(payload["units"][-1]["is_reflection"])
            self.assertFalse(any(unit["is_dialogue_start"] for unit in payload["units"]))
            self.assertEqual(build_sample_narration_units(project, excerpt).status, "unchanged")

    def test_rejects_narration_units_when_the_excerpt_is_tampered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, input_path = self._project(Path(temporary), approved=True)
            excerpt = build_sample_excerpt(project, input_path).path
            payload = json.loads(excerpt.read_text(encoding="utf-8"))
            payload["spoken_text"] = "改写后的文本"
            excerpt.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(SampleExcerptError, "text integrity"):
                build_sample_narration_units(project, excerpt)


if __name__ == "__main__":
    unittest.main()
