from __future__ import annotations

import importlib.util
import json
import subprocess
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

FACTORY = Path(__file__).resolve().parents[1]


def _repository_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "scripts/verify_vfinal_architecture.py").is_file():
            return parent
    raise AssertionError("repository root was not found")


# ``FACTORY`` is the canonical runtime at
# ``skill/runtime/book_video_factory``; the immutable HBG vendor lives at the
# repository root, not beside the runtime mirror.
REPO = _repository_root()
DOCTOR_PATH = FACTORY / "scripts/doctor.py"


def load_doctor():
    spec = importlib.util.spec_from_file_location("phase0_doctor", DOCTOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load doctor")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PhaseZeroDoctorTests(unittest.TestCase):
    def test_planning_report_checks_edge_and_locked_hbg_without_removed_dependencies(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(DOCTOR_PATH), "--profile", "planning", "--json"],
            cwd=REPO,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        names = {check["name"] for check in payload["checks"]}
        self.assertIn("edge-tts", names)
        self.assertIn("hbg_vendor_lock", names)
        self.assertIn("hbg_direct_reuse", names)
        self.assertIn("node", names)
        self.assertIn("npx", names)
        self.assertIn("ffmpeg", names)
        self.assertIn("ffprobe", names)
        serialized = json.dumps(payload, ensure_ascii=False)
        for forbidden in (
            "voxcpm",
            "whisper",
            "gemini",
            "google-flow",
            "paper-collage",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, serialized.lower())

    def test_production_report_treats_edge_tts_as_optional_legacy_and_explains_provider_policy(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(DOCTOR_PATH), "--profile", "production", "--json"],
            cwd=REPO,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        payload = json.loads(completed.stdout)
        checks = {check["name"]: check for check in payload["checks"]}
        for name in (
            "node", "npx", "ffmpeg", "ffprobe", "edge-tts",
            "python_module:jsonschema", "hbg_vendor_lock", "phase4_audio_cli",
        ):
            self.assertIn(name, checks)
        self.assertEqual(checks["python_module:jsonschema"]["status"], "ready")
        edge = checks["edge-tts"]
        # Edge TTS is a legacy/optional provider; it never blocks production
        # readiness and never acts as a fallback from MiniMax.
        self.assertIn(edge["status"], {"ready", "warn"})
        self.assertIn("legacy", edge["note"].lower())
        self.assertIn("minimax", edge["note"].lower())
        self.assertEqual(payload["overall"], "ready")


    def test_vendor_verifier_detects_hash_tampering(self) -> None:
        doctor = load_doctor()
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "hbg-life-simulation"
            shutil.copytree(REPO / "vendor/hbg-life-simulation", destination)
            good = doctor.verify_hbg_vendor(destination)
            self.assertEqual(good["status"], "ready")

            target = destination / "scripts/build_narration.mjs"
            target.write_text(target.read_text(encoding="utf-8") + "\n// tampered\n", encoding="utf-8")
            bad = doctor.verify_hbg_vendor(destination)
            self.assertEqual(bad["status"], "blocked")
            self.assertIn("hash mismatch", str(bad["note"]).lower())


if __name__ == "__main__":
    unittest.main()
