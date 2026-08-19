from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from phase2_fixture_factory import write_json
from phase4_fixture_factory import (
    audio_stage_input,
    build_approved_phase3_project,
    pronunciation_lexicon,
    symlink_or_skip,
    write_phase4_inputs,
)
from book_video_factory.manifests import sha256_file

try:
    from book_video_factory.audio_stage.contracts import (
        AudioStageContractError,
        validate_audio_stage_input,
        validate_pronunciation_lexicon,
        verify_phase4_prerequisites,
    )
except ModuleNotFoundError:
    AudioStageContractError = RuntimeError  # type: ignore


class Phase4ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temp = tempfile.TemporaryDirectory()
        cls.project = build_approved_phase3_project(Path(cls._temp.name))
        cls.input_path, cls.lexicon_path = write_phase4_inputs(cls.project)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temp.cleanup()

    def test_valid_input_lexicon_and_prior_gate_are_normalized(self) -> None:
        lexicon = validate_pronunciation_lexicon(self.project, json.loads(self.lexicon_path.read_text(encoding="utf-8")))
        payload = validate_audio_stage_input(self.project, json.loads(self.input_path.read_text(encoding="utf-8")))
        prior = verify_phase4_prerequisites(self.project, "r1")
        self.assertEqual(payload["provider"], "edge-tts")
        self.assertEqual(payload["body_mode"], "continuous")
        self.assertEqual(lexicon["release_id"], "r1")
        self.assertEqual(prior["next_stage_status"], "ready_for_edge_tts")
        self.assertEqual(prior["hbg_commit"], "63aa262d88f18c6058b205c2dd582cf909b219a4")

    def test_unknown_keys_invalid_provider_settings_and_noncontinuous_body_fail(self) -> None:
        base = json.loads(self.input_path.read_text(encoding="utf-8"))
        mutations = []
        item = copy.deepcopy(base); item["unknown"] = True; mutations.append(item)
        item = copy.deepcopy(base); item["provider"] = "voxcpm2"; mutations.append(item)
        item = copy.deepcopy(base); item["body_rate"] = "fast"; mutations.append(item)
        item = copy.deepcopy(base); item["pitch"] = "high"; mutations.append(item)
        item = copy.deepcopy(base); item["voice"] = ""; mutations.append(item)
        item = copy.deepcopy(base); item["body_mode"] = "per-chapter"; mutations.append(item)
        item = copy.deepcopy(base); item["lead_text"] = "TODO"; mutations.append(item)
        for payload in mutations:
            with self.subTest(payload=payload):
                with self.assertRaises(AudioStageContractError):
                    validate_audio_stage_input(self.project, payload)

    def test_stale_bindings_paths_and_secrets_fail(self) -> None:
        base = json.loads(self.input_path.read_text(encoding="utf-8"))
        for key in base["bindings"]:
            payload = copy.deepcopy(base)
            payload["bindings"][key] = "0" * 64
            with self.subTest(key=key):
                with self.assertRaises(AudioStageContractError):
                    validate_audio_stage_input(self.project, payload)
        for value in ("/Users/alice/private", "C:\\Users\\alice\\secret", "sk-secret-token"):
            payload = copy.deepcopy(base)
            payload["lead_text"] = value
            with self.assertRaises(AudioStageContractError):
                validate_audio_stage_input(self.project, payload)

    def test_binding_files_cannot_be_symlinks_even_when_hash_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = build_approved_phase3_project(Path(temp))
            input_path, _ = write_phase4_inputs(project)
            payload = json.loads(input_path.read_text(encoding="utf-8"))
            script = project / "SCRIPT.md"
            outside = Path(temp) / "outside-script.md"
            outside.write_bytes(script.read_bytes())
            script.unlink()
            symlink_or_skip(self,script,outside)
            payload["bindings"]["script_md_sha256"] = sha256_file(outside)
            with self.assertRaisesRegex(AudioStageContractError, "symlink|unsafe"):
                validate_audio_stage_input(project, payload)

    def test_lexicon_rejects_unknown_fields_ssml_paths_and_invalid_occurrence_policy(self) -> None:
        base = pronunciation_lexicon()
        bad = []
        item = copy.deepcopy(base); item["extra"] = 1; bad.append(item)
        item = copy.deepcopy(base); item["entries"][0]["spoken"] = "<prosody rate='fast'>海明威</prosody>"; bad.append(item)
        item = copy.deepcopy(base); item["entries"][0]["spoken"] = "/tmp/helper"; bad.append(item)
        item = copy.deepcopy(base); item["entries"][0]["occurrence_policy"] = {"mode": "exact", "count": 0}; bad.append(item)
        item = copy.deepcopy(base); item["entries"].append(copy.deepcopy(item["entries"][0])); bad.append(item)
        for payload in bad:
            with self.assertRaises(AudioStageContractError):
                validate_pronunciation_lexicon(self.project, payload)

    def test_stale_visual_approval_and_vendor_drift_fail(self) -> None:
        approval = self.project / "03_images_生成图片/ANCHOR_APPROVAL.json"
        original = approval.read_bytes()
        try:
            approval.write_bytes(original + b"tamper")
            with self.assertRaises(AudioStageContractError):
                verify_phase4_prerequisites(self.project, "r1")
        finally:
            approval.write_bytes(original)
        with mock.patch(
            "book_video_factory.audio_stage.contracts._verify_vendor",
            side_effect=RuntimeError("vendor drift"),
        ):
            with self.assertRaises(AudioStageContractError):
                verify_phase4_prerequisites(self.project, "r1")

    def test_required_visual_foundation_blocks_audio_prerequisites(self) -> None:
        """A required visual foundation may not be bypassed through the audio CLI."""

        project_contract = self.project / "project.json"
        original = project_contract.read_bytes()
        try:
            payload = json.loads(original.decode("utf-8"))
            payload["workflow"]["visual_foundation_policy"] = "required"
            project_contract.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(AudioStageContractError, "visual foundation"):
                verify_phase4_prerequisites(self.project, "r1")
        finally:
            project_contract.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
