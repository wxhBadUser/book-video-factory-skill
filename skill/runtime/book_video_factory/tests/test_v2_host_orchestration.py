from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from book_video_factory.host_orchestration import (
    HostActionError,
    MAX_ATTEMPTS,
    derive_next_action,
    register_action,
    register_host_event,
    validate_host_action,
    verify_host_event,
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _locked_project(base: Path, *, name: str = "pilot") -> Path:
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
    script_text = "locked narration for host orchestration"
    script = project / "02_story_script_故事脚本/SCRIPT_RELEASE.md"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(script_text, encoding="utf-8")
    _write_json(project / "02_story_script_故事脚本/LOCKED_SCRIPT.v2.json", {
        "schema_version": "locked-script.v2",
        "release_id": "release-1",
        "project_id": name,
        "language": "zh",
        "script_path": "02_story_script_故事脚本/SCRIPT_RELEASE.md",
        "script_sha256": _sha(script_text),
        "rights_state": "cleared",
        "lock_status": "locked",
    })
    return project


def _generate_action(*, key: str = "release-1:TASK_001:attempt-1", attempt: int = 1, action_id: str = "ACT_0001") -> dict:
    return {
        "schema_version": "host-agent-action.v2",
        "action_id": action_id,
        "release_id": "release-1",
        "project_id": "pilot",
        "action_type": "generate_image",
        "idempotency_key": key,
        "attempt": attempt,
        "inputs": [{"kind": "visual_paragraph", "path": "05_director/VISUAL_PARAGRAPH.md", "sha256": _sha("p")}],
        "expected_output": {"asset_id": "SCENE_S01", "asset_sha256": _sha("img")},
        "max_runtime_seconds": 900,
    }


def _judge_action(*, key: str = "release-1:TASK_002:attempt-1", attempt: int = 1, action_id: str = "ACT_0002") -> dict:
    return {
        "schema_version": "host-agent-action.v2",
        "action_id": action_id,
        "release_id": "release-1",
        "project_id": "pilot",
        "action_type": "judge_visual_asset",
        "idempotency_key": key,
        "attempt": attempt,
        "inputs": [{"kind": "asset", "path": "assets/scenes/S01.png", "sha256": _sha("img")}],
        "expected_output": {
            "covenant_approval_sha256": _sha("covenant"),
            "visual_paragraph_sha256": _sha("paragraph"),
            "judge_policy_sha256": _sha("policy"),
            "asset_id": "SCENE_S01",
            "asset_sha256": _sha("img"),
        },
        "max_runtime_seconds": 900,
    }


def _succeeded_event(key: str, action_id: str, *, output_path: str) -> dict:
    return {
        "schema_version": "host-agent-event.v2",
        "action_id": action_id,
        "event_type": "generate_image",
        "status": "succeeded",
        "idempotency_key": key,
        "provider": "host-imagegen",
        "tool_call_id": "call_abc",
        "output_path": output_path,
        "output_sha256": _sha("rendered"),
        "model": "nano2",
    }


class TestHostActionValidation(unittest.TestCase):
    def test_judge_action_requires_hash_bindings(self) -> None:
        action = _judge_action()
        action["expected_output"].pop("judge_policy_sha256")
        with self.assertRaises(HostActionError):
            validate_host_action(action)

    def test_unknown_action_type_rejected(self) -> None:
        action = _generate_action()
        action["action_type"] = "render_mp4"
        with self.assertRaises(HostActionError):
            validate_host_action(action)

    def test_invalid_attempt_rejected(self) -> None:
        action = _generate_action()
        action["attempt"] = 0
        with self.assertRaises(HostActionError):
            validate_host_action(action)


class TestHostLedgers(unittest.TestCase):
    def test_same_idempotency_key_registered_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = _locked_project(Path(temp))
            first = register_action(project, _generate_action())
            second = register_action(project, _generate_action())
            self.assertTrue(first["registered"])
            self.assertEqual(second, {"registered": False, "status": "unchanged"})

    def test_derive_next_action_emits_unique_manifest_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = _locked_project(Path(temp))
            register_action(project, _generate_action())
            action = derive_next_action(project)
            self.assertEqual(action["action_id"], "ACT_0001")

    def test_same_action_not_executed_twice_after_terminal_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = _locked_project(Path(temp))
            action = _generate_action()
            register_action(project, action)
            out = project / "assets/scenes/S01.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"rendered")
            event = _succeeded_event(action["idempotency_key"], action["action_id"], output_path="assets/scenes/S01.png")
            event["output_sha256"] = hashlib.sha256(b"rendered").hexdigest()
            register_host_event(project, event)
            self.assertIsNone(derive_next_action(project))

    def test_retryable_failure_synthesizes_retry_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = _locked_project(Path(temp))
            action = _generate_action()
            register_action(project, action)
            event = {
                "schema_version": "host-agent-event.v2",
                "action_id": action["action_id"],
                "event_type": "generate_image",
                "status": "failed",
                "idempotency_key": action["idempotency_key"],
                "provider": "host-imagegen",
                "tool_call_id": "call_fail",
                "failure_class": "provider_http_502",
                "retryable": True,
                "has_registered_output": False,
            }
            register_host_event(project, event)
            retry = derive_next_action(project)
            self.assertEqual(retry["attempt"], 2)
            self.assertEqual(retry["idempotency_key"], "release-1:TASK_001:attempt-2")
            self.assertEqual(retry["retry_of"], action["action_id"])
            self.assertEqual(retry["action_type"], "generate_image")

    def test_non_retryable_failure_does_not_synthesize(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = _locked_project(Path(temp))
            action = _generate_action()
            register_action(project, action)
            event = {
                "schema_version": "host-agent-event.v2",
                "action_id": action["action_id"],
                "event_type": "generate_image",
                "status": "failed",
                "idempotency_key": action["idempotency_key"],
                "provider": "host-imagegen",
                "tool_call_id": "call_fail",
                "failure_class": "policy_rejection",
                "retryable": False,
                "has_registered_output": False,
            }
            register_host_event(project, event)
            self.assertIsNone(derive_next_action(project))


class TestHostEventVerification(unittest.TestCase):
    def test_succeeded_event_missing_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = _locked_project(Path(temp))
            action = _generate_action()
            register_action(project, action)
            event = _succeeded_event(action["idempotency_key"], action["action_id"], output_path="assets/missing.png")
            event["output_sha256"] = _sha("rendered")
            with self.assertRaises(HostActionError):
                register_host_event(project, event)

    def test_succeeded_event_hash_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = _locked_project(Path(temp))
            action = _generate_action()
            register_action(project, action)
            out = project / "assets/scenes/S01.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"rendered")
            event = _succeeded_event(action["idempotency_key"], action["action_id"], output_path="assets/scenes/S01.png")
            event["output_sha256"] = "f" * 64
            with self.assertRaises(HostActionError):
                register_host_event(project, event)

    def test_succeeded_event_path_escape_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = _locked_project(Path(temp))
            action = _generate_action()
            register_action(project, action)
            outside = Path(temp) / "outside.png"
            outside.write_bytes(b"rendered")
            event = _succeeded_event(action["idempotency_key"], action["action_id"], output_path="../outside.png")
            event["output_sha256"] = hashlib.sha256(b"rendered").hexdigest()
            with self.assertRaises(HostActionError):
                register_host_event(project, event)

    def test_unknown_action_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = _locked_project(Path(temp))
            out = project / "assets/scenes/S01.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"rendered")
            event = _succeeded_event("release-1:UNKNOWN:attempt-1", "ACT_9999", output_path="assets/scenes/S01.png")
            event["output_sha256"] = hashlib.sha256(b"rendered").hexdigest()
            with self.assertRaises(HostActionError):
                register_host_event(project, event)


if __name__ == "__main__":
    unittest.main()
