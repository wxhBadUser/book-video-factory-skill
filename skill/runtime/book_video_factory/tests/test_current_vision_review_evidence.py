from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from book_video_factory.production_visuals.registry import SceneAssetError, _manifest
from book_video_factory.semantic_alignment.vision_review import (
    CURRENT_SCHEMA_VERSION,
    BaseVisionProvider,
    CurrentReviewResult,
    CurrentVisionEvidence,
    StaleVisionEvidenceError,
    VisionReviewError,
    build_current_vision_evidence_document,
    load_current_vision_evidence_document,
    register_provider_key,
    review_current_shot,
    verify_current_evidence,
)


GROUP_SHA = "a" * 64
PROMPT_SHA = "b" * 64
PROPOSITION_SHA = "c" * 64


class _CurrentStubProvider(BaseVisionProvider):
    name = "current-vision-stub"
    signing_secret = "current-vision-stub-test-key"

    def __init__(
        self,
        *,
        semantic: str = "pass",
        reality: str = "pass",
        identity: str = "pass",
    ) -> None:
        self.semantic = semantic
        self.reality = reality
        self.identity = identity
        self.saw_identity_pixels: tuple[bytes, ...] = ()

    def _review(self, *, image_bytes: bytes, caption_text: str, prompt_text: str):
        raise AssertionError("the current-evidence path must not call the v1 reviewer")

    def _review_current(
        self,
        *,
        image_bytes: bytes,
        caption_group_text: str,
        proposition_text: str,
        prompt_text: str,
        identity_reference_bytes: tuple[bytes, ...],
    ) -> CurrentReviewResult:
        self.saw_identity_pixels = identity_reference_bytes
        return CurrentReviewResult(
            semantic_review_status=self.semantic,
            semantic_review_reasoning="画面逐条覆盖当前图片组中的全部字幕。",
            reality_review_status=self.reality,
            reality_review_reasoning="人物、手部、物件和接触关系符合现实。",
            identity_review_status=self.identity,
            identity_review_reasoning="人物脸型、年龄和服装与有序锚点一致。",
            vision_call_id="vision-call-current-001",
        )


register_provider_key(_CurrentStubProvider.name, _CurrentStubProvider.signing_secret)


def _write(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


class CurrentVisionEvidenceTests(unittest.TestCase):
    def _review(
        self,
        root: Path,
        *,
        visible_ids: tuple[str, ...] = ("C002",),
        identity_paths: tuple[Path, ...] | None = None,
        identity_status: str = "pass",
        generation_provider: str = "host-imagegen",
        reviewed_pixels: bool = True,
    ) -> tuple[CurrentVisionEvidence, Path, tuple[Path, ...]]:
        image = _write(root / "scene.png", b"scene-pixels")
        paths = identity_paths
        if paths is None:
            paths = tuple(
                _write(root / f"anchor-{index}.png", f"anchor-{index}".encode())
                for index, _ in enumerate(visible_ids, start=1)
            )
        provider = _CurrentStubProvider(identity=identity_status)
        evidence = review_current_shot(
            shot_id="SHOT_CG_0001",
            image_path=image,
            caption_group_text="福贵牵着老牛走过田埂。",
            caption_group_sha256=GROUP_SHA,
            proposition_text="老年福贵牵老牛走在田埂上。",
            proposition_sha256=PROPOSITION_SHA,
            prompt_text="[1/9 CAPTION]\n福贵牵着老牛走过田埂。",
            prompt_sha256=PROMPT_SHA,
            visible_persistent_character_ids=visible_ids,
            identity_reference_paths=paths,
            generation_provider=generation_provider,
            provider=provider,
        )
        self.assertEqual(provider.saw_identity_pixels, tuple(path.read_bytes() for path in paths))
        if not reviewed_pixels:
            evidence = replace(evidence, reviewed_pixels=False)
        return evidence, image, paths

    def test_current_review_binds_full_group_proposition_prompt_image_and_ordered_anchors(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            evidence, image, anchors = self._review(
                root,
                visible_ids=("C002", "C003"),
            )

            self.assertEqual(evidence.image_sha256, hashlib.sha256(image.read_bytes()).hexdigest())
            self.assertEqual(evidence.caption_group_sha256, GROUP_SHA)
            self.assertEqual(evidence.prompt_sha256, PROMPT_SHA)
            self.assertEqual(evidence.proposition_sha256, PROPOSITION_SHA)
            self.assertEqual(
                evidence.identity_anchor_sha256,
                tuple(hashlib.sha256(path.read_bytes()).hexdigest() for path in anchors),
            )
            self.assertEqual(evidence.vision_provider, "current-vision-stub")
            self.assertEqual(evidence.vision_call_id, "vision-call-current-001")
            self.assertTrue(evidence.reviewed_pixels)
            self.assertFalse(evidence.legacy_pass)
            evidence.verify()

    def test_current_document_is_versioned_and_rejects_v1(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            evidence, image, anchors = self._review(Path(raw))
            document = build_current_vision_evidence_document("huozhe-pilot-r1", [evidence])
            self.assertEqual(document["schema_version"], "vision-evidence.v2")
            self.assertEqual(CURRENT_SCHEMA_VERSION, "vision-evidence.v2")
            bindings = {
                evidence.shot_id: {
                    "image_path": image,
                    "caption_group_sha256": GROUP_SHA,
                    "prompt_sha256": PROMPT_SHA,
                    "proposition_sha256": PROPOSITION_SHA,
                    "visible_persistent_character_ids": ("C002",),
                    "identity_reference_paths": anchors,
                    "generation_provider": "host-imagegen",
                    "require_pass": True,
                }
            }
            self.assertEqual(
                load_current_vision_evidence_document(document, current_bindings=bindings)[evidence.shot_id],
                evidence,
            )
            with self.assertRaises(VisionReviewError):
                load_current_vision_evidence_document(
                    {**document, "schema_version": "vision-evidence.v1"},
                    current_bindings=bindings,
                )

    def test_current_document_load_requires_bindings_and_rehashes_anchor_pixels(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            evidence, image, anchors = self._review(Path(raw))
            document = build_current_vision_evidence_document("huozhe-pilot-r1", [evidence])
            with self.assertRaises(VisionReviewError):
                load_current_vision_evidence_document(document, current_bindings={})
            anchors[0].write_bytes(b"anchor-changed-after-review")
            with self.assertRaises(StaleVisionEvidenceError):
                load_current_vision_evidence_document(
                    document,
                    current_bindings={
                        evidence.shot_id: {
                            "image_path": image,
                            "caption_group_sha256": GROUP_SHA,
                            "prompt_sha256": PROMPT_SHA,
                            "proposition_sha256": PROPOSITION_SHA,
                            "visible_persistent_character_ids": ("C002",),
                            "identity_reference_paths": anchors,
                            "generation_provider": "host-imagegen",
                            "require_pass": True,
                        }
                    },
                )

    def test_no_visible_persistent_character_requires_identity_not_applicable(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            evidence, _, _ = self._review(
                Path(raw),
                visible_ids=(),
                identity_paths=(),
                identity_status="not_applicable",
            )
            evidence.verify()
            with self.assertRaises(VisionReviewError):
                replace(evidence, identity_review_status="pass").validate()

    def test_visible_persistent_character_cannot_use_identity_not_applicable(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(VisionReviewError):
                self._review(Path(raw), identity_status="not_applicable")

    def test_every_visible_persistent_character_requires_one_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            one_anchor = (_write(root / "only.png", b"one-anchor"),)
            with self.assertRaises(VisionReviewError):
                self._review(
                    root,
                    visible_ids=("C002", "C003"),
                    identity_paths=one_anchor,
                )

    def test_generation_provider_cannot_review_its_own_image(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(VisionReviewError):
                self._review(Path(raw), generation_provider="current-vision-stub")

    def test_current_evidence_rejects_legacy_pass_and_no_pixels(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            evidence, _, _ = self._review(Path(raw))
            with self.assertRaises(VisionReviewError):
                replace(evidence, legacy_pass=True).validate()
            with self.assertRaises(VisionReviewError):
                replace(evidence, reviewed_pixels=False).validate()

    def test_three_review_statuses_are_independent_and_all_required_for_pass(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            evidence, image, anchors = self._review(Path(raw))
            self.assertTrue(evidence.is_pass())
            failed = replace(evidence, reality_review_status="fail")
            self.assertFalse(failed.is_pass())
            with self.assertRaises(VisionReviewError):
                verify_current_evidence(
                    failed,
                    image_path=image,
                    caption_group_sha256=GROUP_SHA,
                    prompt_sha256=PROMPT_SHA,
                    proposition_sha256=PROPOSITION_SHA,
                    visible_persistent_character_ids=("C002",),
                    identity_reference_paths=anchors,
                    generation_provider="host-imagegen",
                    require_pass=True,
                )

    def test_current_verifier_invalidates_every_bound_hash_and_anchor_order(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            evidence, image, anchors = self._review(root, visible_ids=("C002", "C003"))
            base = dict(
                image_path=image,
                caption_group_sha256=GROUP_SHA,
                prompt_sha256=PROMPT_SHA,
                proposition_sha256=PROPOSITION_SHA,
                visible_persistent_character_ids=("C002", "C003"),
                identity_reference_paths=anchors,
                generation_provider="host-imagegen",
                require_pass=True,
            )
            verify_current_evidence(evidence, **base)
            mutations = (
                {"caption_group_sha256": "d" * 64},
                {"prompt_sha256": "d" * 64},
                {"proposition_sha256": "d" * 64},
                {"visible_persistent_character_ids": ("C003", "C002")},
                {"identity_reference_paths": tuple(reversed(anchors))},
                {"generation_provider": "flow-web"},
            )
            for mutation in mutations:
                with self.subTest(mutation=mutation):
                    with self.assertRaises(StaleVisionEvidenceError):
                        verify_current_evidence(evidence, **{**base, **mutation})

            anchors[0].write_bytes(b"changed-anchor-pixels")
            with self.assertRaises(StaleVisionEvidenceError):
                verify_current_evidence(evidence, **base)


class RegistryIdentityEvidenceTests(unittest.TestCase):
    def _manifest_document(self, root: Path, anchor: Path, scene: Path) -> dict:
        return {
            "schema_version": "scene-asset-manifest.v1",
            "release_id": "pilot-r1",
            "director_stage_manifest_sha256": "f" * 64,
            "provider": "host-imagegen",
            "assets": [
                {
                    "task_id": "TASK-1",
                    "scene_id": "SCENE-1",
                    "path": scene.relative_to(root).as_posix(),
                    "sha256": hashlib.sha256(scene.read_bytes()).hexdigest(),
                    "bytes": scene.stat().st_size,
                    "width": 1920,
                    "height": 1080,
                    "mode": "RGB",
                    "prompt_sha256": "1" * 64,
                    "provider": "host-imagegen",
                    "generation_lane": "host-imagegen",
                    "tool_call_id": "call-real-001",
                    "style_reference_evidence": [],
                    "identity_reference_evidence": [
                        {
                            "task_id": "ANCHOR-C002",
                            "path": anchor.relative_to(root).as_posix(),
                            "sha256": hashlib.sha256(anchor.read_bytes()).hexdigest(),
                            "role": "identity_reference",
                        }
                    ],
                    "machine_diagnostic": {},
                    "semantic_review_status": "pending",
                    "reality_review_status": "pending",
                    "identity_review_status": "pending",
                    "human_review_status": "pending",
                    "registered_at": "2026-08-09T00:00:00+00:00",
                }
            ],
            "registered_asset_count": 1,
            "task_count": 1,
            "last_registered_at": "2026-08-09T00:00:00+00:00",
            "next_stage_status": "awaiting_scene_review",
        }

    def test_registry_manifest_load_rehashes_identity_reference_pixels(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            anchor = _write(root / "03_images_生成图片/anchors/C002.png", b"anchor-current")
            scene = _write(root / "06_visual_production/scenes/scene.png", b"scene-current")
            manifest = self._manifest_document(root, anchor, scene)
            manifest_path = root / "06_visual_production/SCENE_ASSET_MANIFEST.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            self.assertEqual(_manifest(root, "pilot-r1", "f" * 64, 1)["assets"][0]["task_id"], "TASK-1")
            anchor.write_bytes(b"anchor-stale")
            with self.assertRaises(SceneAssetError):
                _manifest(root, "pilot-r1", "f" * 64, 1)


if __name__ == "__main__":
    unittest.main()
