"""Real production-boundary tests for the semantic bypass closures (Item 8)
and the Scene Asset Manifest provider==generation_lane invariant (Item 6).

These do NOT test helper stubs: they call the functions the production
Director and Scene Asset registry actually use --
``classify_proposition`` / ``_scene_proposition`` (Director) and ``_manifest``
(Scene Asset Manifest validator).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from book_video_factory.director_stage.compiler import (
    DirectorStageError,
    _scene_proposition,
)
from book_video_factory.production_visuals.registry import SceneAssetError, _manifest
from book_video_factory.semantic_alignment.classifier import (
    PropositionClassifierError,
    classify_proposition,
)
from book_video_factory.semantic_alignment.models import EntityVisibility, VisualProposition


class ClassifierAbstractFallbackTests(unittest.TestCase):
    def test_concrete_event_caption_cannot_degrade_to_abstract(self) -> None:
        # D-bridge: a plot (concrete-event) caption that the classifier cannot
        # ground as Literal/Symbolic must be re-proposed, not shipped as a blank
        # Abstract mood frame. The caption avoids every Symbolic-trope token
        # (月光/路/坟/雪/老牛/黄昏/灯/窗/风/河/水) and every Abstract marker so
        # it reaches the final fallback, where the concrete narrative_function
        # blocks the Abstract degradation.
        with self.assertRaises(PropositionClassifierError):
            classify_proposition(
                shot_id="s-concrete",
                caption_texts=["院里的公鸡扑棱着翅膀跳上墙头，惊醒了打盹的狗"],
                description="",
                seed_rationale="",
                source_entities=["福贵"],
                narrative_function="plot",
            )

    def test_meta_narration_may_fall_back_to_abstract(self) -> None:
        # A theory caption has no concrete referent, so Abstract is the correct
        # fallback (not a concrete event).
        prop = classify_proposition(
            shot_id="s-theory",
            caption_texts=["这本书真正可怕的地方，是命运对好人反复的碾压"],
            description="",
            seed_rationale="",
            source_entities=["福贵"],
            narrative_function="theory",
        )
        self.assertEqual(prop.mode, "Abstract")


class AgentAuthoredPropositionTests(unittest.TestCase):
    def test_invalid_agent_authored_proposition_is_rejected(self) -> None:
        # C-bridge: an Agent-authored proposition gets NO bypass. A Literal with
        # no subject/action and no visible entity must be rejected by the same
        # seven-tuple contract a derived proposition would face.
        bad = VisualProposition(
            mode="Literal",
            subject="",
            action="",
            environment="",
            mood="",
            lighting="",
            palette="",
            rationale_text="画面给出凤霞出嫁当天的场景",
            entity_visibility=(),
            surrogate_objects=(),
            source_terms=("凤霞",),
        )
        with self.assertRaises(DirectorStageError):
            _scene_proposition(
                {"visualProposition": bad.to_dict()},
                profile={},
                caption_texts=["凤霞出嫁那天唢呐声盖过了哭声"],
                required=["凤霞"],
                light={},
                narrative_function="plot",
                known_symbol_registry=(),
            )

    def test_valid_agent_authored_proposition_passes(self) -> None:
        good = VisualProposition(
            mode="Literal",
            subject="凤霞",
            action="出嫁",
            environment="乡村院落",
            mood="克制不夸张",
            lighting="DUSK_SOFT",
            palette="EARTH_DUSK",
            rationale_text="旁白点名凤霞出嫁，画面必须让凤霞可见",
            entity_visibility=(EntityVisibility("凤霞", True, "凤霞"),),
            surrogate_objects=(),
            source_terms=("凤霞",),
        )
        prop = _scene_proposition(
            {"visualProposition": good.to_dict()},
            profile={},
            caption_texts=["凤霞出嫁那天唢呐声盖过了哭声"],
            required=["凤霞"],
            light={},
            narrative_function="plot",
            known_symbol_registry=(),
        )
        self.assertEqual(prop.mode, "Literal")
        self.assertEqual(prop.subject, "凤霞")


def _write_asset(root: Path, rel: str, color: tuple[int, int, int]) -> tuple[int, str]:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1920, 1080), color).save(path)
    return path.stat().st_size, _sha256_file(path)


def _sha256_file(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


class SceneAssetManifestLaneInvariantTests(unittest.TestCase):
    def _manifest_with(self, root: Path, *, generation_lane: str) -> dict[str, object]:
        size, digest = _write_asset(root, "assets/generated/scenes/B01.png", (41, 92, 133))
        manifest = {
            "schema_version": "scene-asset-manifest.v1",
            "release_id": "r1",
            "director_stage_manifest_sha256": "0" * 64,
            "provider": "host-imagegen",
            "assets": [{
                "task_id": "B01",
                "scene_id": "S1",
                "path": "assets/generated/scenes/B01.png",
                "sha256": digest,
                "bytes": size,
                "width": 1920,
                "height": 1080,
                "mode": "RGB",
                "prompt_sha256": "0" * 64,
                "provider": "host-imagegen",
                "generation_lane": generation_lane,
                "tool_call_id": "imagegen_call_scene_000001",
                "style_reference_evidence": [],
                "identity_reference_evidence": [],
                "machine_diagnostic": {},
                "semantic_review_status": "pending",
                "reality_review_status": "pending",
                "identity_review_status": "pending",
                "human_review_status": "pending",
                "registered_at": "2026-01-01T00:00:00+00:00",
            }],
            "registered_asset_count": 1,
            "task_count": 1,
            "last_registered_at": "2026-01-01T00:00:00+00:00",
            "next_stage_status": "awaiting_scene_review",
        }
        (root / "06_visual_production/SCENE_ASSET_MANIFEST.json").parent.mkdir(
            parents=True, exist_ok=True
        )
        (root / "06_visual_production/SCENE_ASSET_MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return manifest

    def test_matching_lane_passes_invariants(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._manifest_with(root, generation_lane="host-imagegen")
            # Must not raise: provider == generation_lane.
            _manifest(root, "r1", "0" * 64, 1, "host-imagegen")

    def test_mismatched_lane_is_rejected_at_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._manifest_with(root, generation_lane="gemini-web")
            # A valid-but-mismatched provider (gemini-web under a host-imagegen
            # lane) passes the per-provider allowlist but violates the
            # provider==generation_lane invariant and must be rejected.
            with self.assertRaises(SceneAssetError):
                _manifest(root, "r1", "0" * 64, 1, "host-imagegen")


if __name__ == "__main__":
    unittest.main()
