"""Part 7: per-shot HTML / JSON review report generator tests."""

import json
import tempfile
import unittest
from pathlib import Path

from book_video_factory.semantic_alignment.review_report import (
    build_review_report,
    render_review_report_html,
    write_review_report,
)


def _evidence(shot_id: str = "B01", verdict: str = "match") -> dict:
    return {
        "shot_id": shot_id,
        "vision_provider": "claude-sonnet-4.5",
        "call_id": "call-abc123",
        "image_sha256": "a" * 64,
        "caption_sha256": "b" * 64,
        "prompt_sha256": "c" * 64,
        "parity_verdict": verdict,
        "parity_reasoning": "The image clearly shows the subject described in the caption.",
        "reviewed_pixels": True,
        "legacy_pass": False,
    }


def _decision_doc(*decisions: dict) -> dict:
    return {
        "schema_version": "scene-review-decision.v1",
        "release_id": "r1",
        "director_stage_manifest_sha256": "0" * 64,
        "scene_asset_manifest_sha256": "0" * 64,
        "reviewer": "Reviewer",
        "decisions": list(decisions),
    }


def _base_decision(task_id: str) -> dict:
    return {
        "task_id": task_id,
        "semantic_review_status": "pass",
        "reality_review_status": "pass",
        "identity_review_status": "pass",
        "note": "Reviewed.",
    }


class ReviewReportBuildTests(unittest.TestCase):
    def test_counts_split_bound_legacy_unbound(self) -> None:
        doc = _decision_doc(
            _base_decision("B01"),
            {**_base_decision("B02"), "legacy_pass": True},
            {**_base_decision("B03"), "vision_evidence": _evidence("B03")},
        )
        report = build_review_report(doc)
        self.assertEqual(report["shot_count"], 3)
        self.assertEqual(report["unbound_count"], 1)
        self.assertEqual(report["legacy_count"], 1)
        self.assertEqual(report["vision_bound_count"], 1)

    def test_unbound_shot_records_no_provider(self) -> None:
        report = build_review_report(_decision_doc(_base_decision("B01")))
        shot = report["shots"][0]
        self.assertFalse(shot["vision_bound"])
        self.assertIsNone(shot["vision_provider"])
        self.assertIsNone(shot["vision_verdict"])

    def test_bound_shot_records_verdict_and_provider(self) -> None:
        report = build_review_report(_decision_doc(
            {**_base_decision("B03"), "vision_evidence": _evidence("B03", "rough")},
        ))
        shot = report["shots"][0]
        self.assertTrue(shot["vision_bound"])
        self.assertEqual(shot["vision_provider"], "claude-sonnet-4.5")
        self.assertEqual(shot["vision_verdict"], "rough")

    def test_proposition_mode_passthrough(self) -> None:
        report = build_review_report(
            _decision_doc(_base_decision("B01")),
            proposition_modes={"B01": "Literal"},
        )
        self.assertEqual(report["shots"][0]["proposition_mode"], "Literal")

    def test_rejects_wrong_schema(self) -> None:
        with self.assertRaises(ValueError):
            build_review_report({"schema_version": "scene-review-decision.v0", "decisions": []})


class ReviewReportHtmlTests(unittest.TestCase):
    def test_html_contains_every_shot_and_summary(self) -> None:
        doc = _decision_doc(
            _base_decision("B01"),
            {**_base_decision("B02"), "legacy_pass": True},
            {**_base_decision("B03"), "vision_evidence": _evidence("B03")},
        )
        report = build_review_report(doc)
        page = render_review_report_html(report, title="逐镜审查")
        for token in ("B01", "B02", "B03", "逐镜审查", "未绑定视觉证据", "历史遗留", "镜头总数 3"):
            self.assertIn(token, page)

    def test_html_escapes_notes(self) -> None:
        doc = _decision_doc({**_base_decision("B01"), "note": "<script>alert(1)</script>"})
        page = render_review_report_html(build_review_report(doc))
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)


class ReviewReportWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="review-report-")
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_write_emits_json_and_html(self) -> None:
        decision_path = self.root / "SCENE_REVIEW_DECISION.json"
        decision_path.write_text(
            json.dumps(_decision_doc(_base_decision("B01")), ensure_ascii=False), encoding="utf-8"
        )
        json_path = self.root / "report.json"
        html_path = self.root / "report.html"
        report = write_review_report(decision_path, json_path=json_path, html_path=html_path)
        self.assertTrue(json_path.is_file())
        self.assertTrue(html_path.is_file())
        self.assertEqual(report["shot_count"], 1)
        reloaded = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(reloaded["schema_version"], "av-shot-review.v1")


if __name__ == "__main__":
    unittest.main()
