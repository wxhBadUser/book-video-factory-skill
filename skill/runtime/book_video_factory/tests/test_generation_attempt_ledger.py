from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from book_video_factory.manifests import sha256_file
from book_video_factory.production_visuals.attempt_ledger import (
    GenerationAttemptError,
    record_generation_attempt,
)
from book_video_factory.production_visuals.scheduler import plan_generation_run
from test_generation_scheduler import _write_json, build_project


def _registered_single(project: Path, task_id: str = "SCENE_S07") -> tuple[Path, Path]:
    source = project / "06_visual_production/staging/provider/S07.png"
    final = project / "assets/generated/scenes/S07.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    final.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"real-provider-image")
    final.write_bytes(source.read_bytes())
    _write_json(project / "06_visual_production/SCENE_ASSET_MANIFEST.json", {
        "schema_version": "scene-asset-manifest.v1",
        "assets": [{"task_id": task_id, "path": "assets/generated/scenes/S07.png", "sha256": sha256_file(final)}],
    })
    return source, final


class GenerationAttemptLedgerTests(unittest.TestCase):
    def test_records_success_with_real_source_and_registered_final_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_project(Path(temp))
            plan_generation_run(project, concurrency=4)
            source, final = _registered_single(project)
            result = record_generation_attempt(
                project,
                job_id="SCENE_S07",
                outcome="success",
                provider="openai",
                model="gpt-image-1",
                endpoint_class="system-cli",
                concurrency=4,
                source=source,
                final=final,
            )
            self.assertEqual(result.attempt, 1)
            record = json.loads(result.ledger_path.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(record["source_sha256"], sha256_file(source))
            self.assertEqual(record["final_sha256"], sha256_file(final))
            self.assertEqual(record["outcome"], "success")
            self.assertEqual(json.loads(result.run_manifest_path.read_text(encoding="utf-8"))["attempt_count"], 1)
            prompts = result.prompts_path.read_text(encoding="utf-8")
            self.assertIn("SCENE_S07", prompts)
            self.assertIn("production prompt 7", prompts)

    def test_success_cannot_self_certify_an_unregistered_or_wrong_final(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_project(Path(temp))
            plan_generation_run(project)
            source = project / "06_visual_production/staging/S07.png"
            final = project / "assets/generated/scenes/S07.png"
            source.parent.mkdir(parents=True)
            final.parent.mkdir(parents=True)
            source.write_bytes(b"source")
            final.write_bytes(b"different")
            with self.assertRaisesRegex(GenerationAttemptError, "registered|manifest|hash"):
                record_generation_attempt(
                    project, job_id="SCENE_S07", outcome="success", provider="openai",
                    model="gpt-image-1", endpoint_class="system-cli", concurrency=5,
                    source=source, final=final,
                )

    def test_failed_and_rejected_attempts_require_precise_reasons(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_project(Path(temp))
            plan_generation_run(project)
            with self.assertRaisesRegex(GenerationAttemptError, "reason"):
                record_generation_attempt(
                    project, job_id="SCENE_S07", outcome="failed", provider="openai",
                    model="gpt-image-1", endpoint_class="system-cli", concurrency=5,
                )
            failed = record_generation_attempt(
                project, job_id="SCENE_S07", outcome="failed", provider="openai",
                model="gpt-image-1", endpoint_class="system-cli", concurrency=5,
                machine_rejection_reason="provider returned HTTP 503",
            )
            rejected = record_generation_attempt(
                project, job_id="SCENE_S07", outcome="rejected", provider="openai",
                model="gpt-image-1", endpoint_class="system-cli", concurrency=5,
                human_rejection_reason="main character identity drift",
            )
            self.assertEqual((failed.attempt, rejected.attempt), (1, 2))

    def test_secret_like_values_are_rejected_before_any_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_project(Path(temp))
            plan_generation_run(project)
            ledger = project / "06_visual_production/GENERATION_ATTEMPTS.jsonl"
            with self.assertRaisesRegex(GenerationAttemptError, "secret"):
                record_generation_attempt(
                    project, job_id="SCENE_S07", outcome="failed", provider="openai",
                    model="sk-test-secret-value", endpoint_class="system-cli", concurrency=5,
                    machine_rejection_reason="provider rejected request",
                )
            self.assertEqual(ledger.read_bytes(), b"")

    def test_cli_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_project(Path(temp))
            plan_generation_run(project)
            scripts = Path(__file__).resolve().parents[1] / "scripts"
            completed = subprocess.run([
                sys.executable, str(scripts / "record_generation_attempt.py"),
                "--project", str(project), "--job-id", "SCENE_S07", "--outcome", "failed",
                "--provider", "openai", "--model", "gpt-image-1", "--endpoint-class", "system-cli",
                "--concurrency", "5", "--machine-rejection-reason", "provider timeout",
            ], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertEqual(json.loads(completed.stdout)["attempt"], 1)


if __name__ == "__main__":
    unittest.main()
