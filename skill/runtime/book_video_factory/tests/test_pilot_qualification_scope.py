from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from book_video_factory.content_package import compile_content_package
from book_video_factory.originality_check import normalize
from book_video_factory.project import initialize_project


FIXTURE = Path(__file__).parent / "fixtures" / "phase1_valid_package"
INPUT_FILES = {
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


def _inputs_with_short_script() -> dict[str, dict]:
    values = {
        key: json.loads((FIXTURE / filename).read_text(encoding="utf-8"))
        for key, filename in INPUT_FILES.items()
    }
    script = copy.deepcopy(values["script"])
    texts = [
        "老人第八十五次出海，岸上的人仍认定他不会再有好运。",
        "他把绳索一圈圈放好，独自驶向更远更暗的海面。",
        "大鱼咬钩以后，小船被拖着走了一天又一个夜晚。",
        "绳索勒破手掌，他却始终不肯让自己的手先松开。",
        "鱼终于浮出水面，那一刻胜利看起来如此完整。",
        "可问题来了，带回一条鱼，真的就等于赢了吗？",
        "鲨鱼接连扑来，他用鱼叉、刀和木棍一次次抵抗。",
        "尊严不是保住战利品，而是在失去时仍选择行动。",
        "我们也会害怕，拼尽全力后却没有结果可以展示。",
        "天亮时只剩鱼骨，老人睡着，再次梦见海滩的狮子。",
    ]
    texts = [text + "久久无声" for text in texts]
    for version_name in ("performance_version", "release_version", "audit_version"):
        sections = script[version_name]["sections"]
        for section, text in zip(sections, texts, strict=True):
            section["text"] = text
        script[version_name]["text"] = "".join(texts)
    script["script_text"] = script["release_version"]["text"]
    script["duration_target_seconds"] = 75
    values["script"] = script

    normalized = normalize(script["script_text"])
    originality = copy.deepcopy(values["originality"])
    originality["candidate_sha256"] = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    originality["candidate_chars"] = len(normalized)
    originality["longest_overlap"] = 0
    originality["violations"] = []
    originality["passed"] = True
    values["originality"] = originality
    return values


class PilotQualificationScopeTests(unittest.TestCase):
    def test_short_script_is_accepted_for_production_with_chars_recorded_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = initialize_project(
                Path(temp) / "warehouse", "normal", "老人与海", "海明威"
            )
            result = compile_content_package(
                project,
                release_id="r1",
                source_root=FIXTURE,
                **_inputs_with_short_script(),
            )

            lock = json.loads(
                (project / "02_story_script_故事脚本" / "SCRIPT_LOCK.json").read_text(
                    encoding="utf-8"
                )
            )
            checks = lock["machine_gate"]["checks"]
            self.assertEqual(result.status, "created")
            self.assertTrue(lock["machine_locked"])
            # Character count is recorded for diagnostics only; no hard range
            # is enforced because the Creative Route decides target length.
            self.assertNotIn("chars_range", checks)
            self.assertEqual(checks["chars_recorded"]["passed"], True)
            self.assertIsNotNone(checks["chars_recorded"]["detail"]["value"])

    def test_explicit_pilot_scope_accepts_and_records_60_to_90_second_script(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = initialize_project(
                Path(temp) / "warehouse",
                "pilot",
                "老人与海",
                "海明威",
                qualification_scope="hbg-parity-pilot",
            )
            result = compile_content_package(
                project,
                release_id="r1",
                source_root=FIXTURE,
                **_inputs_with_short_script(),
            )

            lock = json.loads(
                (project / "02_story_script_故事脚本" / "SCRIPT_LOCK.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(result.status, "created")
            self.assertEqual(lock["qualification_scope"], "hbg-parity-pilot")
            self.assertEqual(
                lock["machine_gate"]["checks"]["duration_range"]["detail"]["range"],
                [1.0, 1.5],
            )
            self.assertTrue(lock["machine_locked"])


if __name__ == "__main__":
    unittest.main()
