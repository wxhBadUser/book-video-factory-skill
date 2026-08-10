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
from book_video_factory.semantic_alignment.contract_bindings import (
    aggregate_contract_bindings_sha256,
)
from book_video_factory.semantic_alignment.models import VisualProposition
from book_video_factory.semantic_alignment.prompting import compute_prompt_binding

CAPTION_ID = "caption-0001"
CAPTION_TEXT = "福贵牵着老牛走过田埂。"
PROMPT_TEXT = "[1/9 CAPTION]\n福贵牵着老牛走过田埂。"


def _current_group(caption_ids: list[str] | None = None) -> dict:
    ids = list(caption_ids) if caption_ids is not None else [CAPTION_ID]
    group = {
        "group_id": "G001", "caption_ids": ids,
        "start": 0.0, "end": 2.0, "duration": 2.0,
        "narrative_function": "plot", "characters": [], "location": "",
        "time_of_day": "", "split_reasons": [],
        "scene_state_signature": "d" * 64,
        "contract_bindings": [
            {
                "caption_id": caption_id,
                "content_sha256": hashlib.sha256(
                    f"contract::{index}::{caption_id}".encode("utf-8")
                ).hexdigest(),
            }
            for index, caption_id in enumerate(ids)
        ],
        "split_from_previous": {"required": False, "reasons": []},
    }
    canonical = json.dumps(group, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    group["caption_group_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return group


def _proposition() -> VisualProposition:
    return VisualProposition(
        mode="Literal", subject="福贵", action="牵牛", environment="田埂",
        mood="平静", lighting="soft", palette="earth",
        rationale_text="字幕明确描述福贵牵牛走过田埂。",
    )


def _prompt_binding(
    *,
    group: dict | None = None,
    caption_texts: dict[str, str] | None = None,
    prompt_text: str = PROMPT_TEXT,
) -> dict:
    """Build the IMAGE_TASK prompt binding with the real production function.

    Using ``compute_prompt_binding`` rather than a hand-written dict is the
    point: it proves the reviewer's independently recomputed
    ``caption_text_set_sha256`` really does land on the same digest the Director
    writes into ``prompt_binding.caption_text_sha256``.
    """

    group = group if group is not None else _current_group()
    caption_ids = [str(value) for value in group["caption_ids"]]
    texts = caption_texts if caption_texts is not None else {CAPTION_ID: CAPTION_TEXT}
    # The Director joins one group's ordered caption prose with " / " before it
    # hashes it (see render_stage.compiler).
    caption_text = " / ".join(texts[caption_id] for caption_id in caption_ids)
    bindings = [dict(item) for item in group["contract_bindings"]]
    return compute_prompt_binding(
        caption_ids=caption_ids,
        caption_text=caption_text,
        proposition=_proposition(),
        prompt=prompt_text,
        scene_id="SCENE-1",
        beat_ids=["BEAT-1"],
        shot_id="SHOT_CG_0001",
        caption_visual_contract_sha256=aggregate_contract_bindings_sha256(
            bindings, expected_caption_ids=caption_ids
        ),
        group_id=str(group["group_id"]),
        caption_contract_bindings=bindings,
        caption_group_sha256=str(group["caption_group_sha256"]),
    )


GROUP_SHA = _current_group()["caption_group_sha256"]
PROMPT_SHA = hashlib.sha256(PROMPT_TEXT.encode("utf-8")).hexdigest()
PROPOSITION_SHA = _proposition().content_sha256()
TASK_ID = "TASK-CG-0001"


def _current_binding(image, anchors, **overrides) -> dict:
    """The full re-verification binding the render gate hands the verifier."""

    base = dict(
        image_path=image,
        task_id=TASK_ID,
        group_id="G001",
        caption_ids=(CAPTION_ID,),
        caption_texts={CAPTION_ID: CAPTION_TEXT},
        caption_group_sha256=GROUP_SHA,
        prompt_sha256=PROMPT_SHA,
        proposition_sha256=PROPOSITION_SHA,
        visible_persistent_character_ids=("C002",),
        identity_reference_paths=anchors,
        generation_provider="host-imagegen",
        require_pass=True,
    )
    base.update(overrides)
    return base


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
    def test_current_review_derives_semantic_hashes_from_the_exact_review_content(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            image = _write(root / "scene.png", b"scene-pixels")
            group = {
                "group_id": "G001",
                "caption_ids": ["caption-0001"],
                "start": 0.0,
                "end": 2.0,
                "duration": 2.0,
                "narrative_function": "plot",
                "characters": ["C002"],
                "location": "field",
                "time_of_day": "day",
                "split_reasons": [],
                "scene_state_signature": "d" * 64,
                "contract_bindings": [{"caption_id": "caption-0001", "content_sha256": "e" * 64}],
                "split_from_previous": {"required": False, "reasons": []},
            }
            canonical = json.dumps(group, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            group["caption_group_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            proposition = _proposition()
            prompt = PROMPT_TEXT
            binding = _prompt_binding(group=group)

            evidence = review_current_shot(
                shot_id="SHOT_CG_0001",
                task_id=TASK_ID,
                image_path=image,
                caption_group=group,
                caption_texts={CAPTION_ID: CAPTION_TEXT},
                proposition=proposition,
                prompt_text=prompt,
                prompt_binding=binding,
                visible_persistent_character_ids=(),
                identity_reference_paths=(),
                generation_provider="host-imagegen",
                provider=_CurrentStubProvider(identity="not_applicable"),
            )

            self.assertEqual(evidence.caption_group_sha256, group["caption_group_sha256"])
            self.assertEqual(evidence.prompt_sha256, hashlib.sha256(prompt.encode("utf-8")).hexdigest())
            self.assertEqual(evidence.proposition_sha256, proposition.content_sha256())
            # The independently recomputed reviewed-caption digest lands exactly
            # on the digest the Director bound to the prompt.
            self.assertEqual(
                evidence.caption_text_set_sha256, binding["caption_text_sha256"]
            )
            self.assertEqual(evidence.task_id, TASK_ID)
            self.assertEqual(evidence.group_id, "G001")
            self.assertEqual(evidence.caption_ids, (CAPTION_ID,))

            with self.assertRaises(VisionReviewError):
                review_current_shot(
                    shot_id="SHOT_CG_0001",
                    task_id=TASK_ID,
                    image_path=image,
                    caption_group={**group, "caption_group_sha256": "f" * 64},
                    caption_texts={CAPTION_ID: "UNRELATED TEXT SHOWN TO REVIEWER"},
                    proposition=proposition,
                    prompt_text=prompt,
                    prompt_binding=binding,
                    visible_persistent_character_ids=(),
                    identity_reference_paths=(),
                    generation_provider="host-imagegen",
                    provider=_CurrentStubProvider(identity="not_applicable"),
                )

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
            task_id=TASK_ID,
            image_path=image,
            caption_group=_current_group(),
            caption_texts={CAPTION_ID: CAPTION_TEXT},
            proposition=_proposition(),
            prompt_text=PROMPT_TEXT,
            prompt_binding=_prompt_binding(),
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
            self.assertEqual(evidence.caption_group_sha256, _current_group()["caption_group_sha256"])
            self.assertEqual(evidence.prompt_sha256, hashlib.sha256("[1/9 CAPTION]\n福贵牵着老牛走过田埂。".encode()).hexdigest())
            self.assertEqual(evidence.proposition_sha256, VisualProposition(
                mode="Literal", subject="福贵", action="牵牛", environment="田埂",
                mood="平静", lighting="soft", palette="earth",
                rationale_text="字幕明确描述福贵牵牛走过田埂。",
            ).content_sha256())
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
            bindings = {evidence.shot_id: _current_binding(image, anchors)}
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
                    current_bindings={evidence.shot_id: _current_binding(image, anchors)},
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
                verify_current_evidence(failed, **_current_binding(image, anchors))

    def test_current_verifier_invalidates_every_bound_hash_and_anchor_order(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            evidence, image, anchors = self._review(root, visible_ids=("C002", "C003"))
            base = _current_binding(
                image, anchors, visible_persistent_character_ids=("C002", "C003")
            )
            verify_current_evidence(evidence, **base)
            mutations = (
                {"caption_group_sha256": "d" * 64},
                {"prompt_sha256": "d" * 64},
                {"proposition_sha256": "d" * 64},
                {"visible_persistent_character_ids": ("C003", "C002")},
                {"identity_reference_paths": tuple(reversed(anchors))},
                {"generation_provider": "flow-web"},
                {"task_id": "TASK-CG-9999"},
                {"group_id": "G999"},
                {"caption_texts": {CAPTION_ID: CAPTION_TEXT + "。"}},
            )
            for mutation in mutations:
                with self.subTest(mutation=mutation):
                    with self.assertRaises(StaleVisionEvidenceError):
                        verify_current_evidence(evidence, **{**base, **mutation})

            anchors[0].write_bytes(b"changed-anchor-pixels")
            with self.assertRaises(StaleVisionEvidenceError):
                verify_current_evidence(evidence, **base)


class ReviewedCaptionBindingAttackTests(unittest.TestCase):
    """FINAL-1: the evidence must bind the captions that were really reviewed.

    Every test here drives the *production* entrypoint ``review_current_shot``
    (or the production re-verifier used by the render gate). None of them pokes
    at a hash helper in isolation, because a helper that is correct but never
    reached does not defend anything.
    """

    def _scene(self, root: Path) -> tuple[Path, tuple[Path, ...]]:
        image = _write(root / "scene.png", b"scene-pixels")
        anchors = (_write(root / "anchor-1.png", b"anchor-1"),)
        return image, anchors

    def test_attack_a_correct_group_and_image_but_wrong_caption_text_fails(self) -> None:
        """A: right Caption Group id, right image, wrong caption prose."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            image, anchors = self._scene(root)
            group = _current_group()
            # The binding was produced for the real caption; the reviewer is
            # handed different prose under the same caption id.
            binding = _prompt_binding(group=group)
            with self.assertRaises(VisionReviewError) as caught:
                review_current_shot(
                    shot_id="SHOT_CG_0001",
                    task_id=TASK_ID,
                    image_path=image,
                    caption_group=group,
                    caption_texts={CAPTION_ID: "家珍在城里的医院门口等了一夜。"},
                    proposition=_proposition(),
                    prompt_text=PROMPT_TEXT,
                    prompt_binding=binding,
                    visible_persistent_character_ids=("C002",),
                    identity_reference_paths=anchors,
                    generation_provider="host-imagegen",
                    provider=_CurrentStubProvider(),
                )
            self.assertIn("caption text actually under review", str(caught.exception))

    def test_attack_b_reordered_captions_fail(self) -> None:
        """B: same caption set, different order -> different bound prose."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            image, anchors = self._scene(root)
            texts = {"caption-0001": "福贵牵着老牛走过田埂。", "caption-0002": "家珍站在门口望着他。"}
            forward = _current_group(["caption-0001", "caption-0002"])
            reversed_group = _current_group(["caption-0002", "caption-0001"])
            binding = _prompt_binding(group=forward, caption_texts=texts)

            # Sanity: the honest order is accepted and binds the same digest.
            evidence = review_current_shot(
                shot_id="SHOT_CG_0001",
                task_id=TASK_ID,
                image_path=image,
                caption_group=forward,
                caption_texts=texts,
                proposition=_proposition(),
                prompt_text=PROMPT_TEXT,
                prompt_binding=binding,
                visible_persistent_character_ids=("C002",),
                identity_reference_paths=anchors,
                generation_provider="host-imagegen",
                provider=_CurrentStubProvider(),
            )
            self.assertEqual(
                evidence.caption_text_set_sha256, binding["caption_text_sha256"]
            )
            self.assertEqual(evidence.caption_ids, ("caption-0001", "caption-0002"))

            # The reordered group is a genuinely different Caption Group whose
            # joined prose hashes differently, so it cannot ride this binding.
            with self.assertRaises(VisionReviewError):
                review_current_shot(
                    shot_id="SHOT_CG_0001",
                    task_id=TASK_ID,
                    image_path=image,
                    caption_group=reversed_group,
                    caption_texts=texts,
                    proposition=_proposition(),
                    prompt_text=PROMPT_TEXT,
                    prompt_binding=binding,
                    visible_persistent_character_ids=("C002",),
                    identity_reference_paths=anchors,
                    generation_provider="host-imagegen",
                    provider=_CurrentStubProvider(),
                )

    def test_attack_c_one_character_caption_edit_makes_old_evidence_stale(self) -> None:
        """C: approved evidence, then one character of the caption changes."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            image, anchors = self._scene(root)
            evidence = review_current_shot(
                shot_id="SHOT_CG_0001",
                task_id=TASK_ID,
                image_path=image,
                caption_group=_current_group(),
                caption_texts={CAPTION_ID: CAPTION_TEXT},
                proposition=_proposition(),
                prompt_text=PROMPT_TEXT,
                prompt_binding=_prompt_binding(),
                visible_persistent_character_ids=("C002",),
                identity_reference_paths=anchors,
                generation_provider="host-imagegen",
                provider=_CurrentStubProvider(),
            )
            # Still current against the unedited caption.
            verify_current_evidence(evidence, **_current_binding(image, anchors))

            edited = CAPTION_TEXT.replace("老牛", "老狗")
            self.assertNotEqual(edited, CAPTION_TEXT)
            with self.assertRaises(StaleVisionEvidenceError) as caught:
                verify_current_evidence(
                    evidence,
                    **_current_binding(image, anchors, caption_texts={CAPTION_ID: edited}),
                )
            self.assertIn("Caption text changed since current review", str(caught.exception))

    def test_attack_d_hand_written_plausible_caption_sha_fails(self) -> None:
        """D: a forged but well-formed caption hash in the prompt binding."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            image, anchors = self._scene(root)
            forged = dict(_prompt_binding())
            forged["caption_text_sha256"] = hashlib.sha256(
                "一段看起来很像、但根本不是这条字幕的文字。".encode("utf-8")
            ).hexdigest()
            self.assertEqual(len(forged["caption_text_sha256"]), 64)
            with self.assertRaises(VisionReviewError):
                review_current_shot(
                    shot_id="SHOT_CG_0001",
                    task_id=TASK_ID,
                    image_path=image,
                    caption_group=_current_group(),
                    caption_texts={CAPTION_ID: CAPTION_TEXT},
                    proposition=_proposition(),
                    prompt_text=PROMPT_TEXT,
                    prompt_binding=forged,
                    visible_persistent_character_ids=("C002",),
                    identity_reference_paths=anchors,
                    generation_provider="host-imagegen",
                    provider=_CurrentStubProvider(),
                )

    def test_reviewer_never_accepts_a_caller_supplied_caption_digest(self) -> None:
        """The evidence digest is derived, so poisoning the binding cannot set it."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            image, anchors = self._scene(root)
            evidence = review_current_shot(
                shot_id="SHOT_CG_0001",
                task_id=TASK_ID,
                image_path=image,
                caption_group=_current_group(),
                caption_texts={CAPTION_ID: CAPTION_TEXT},
                proposition=_proposition(),
                prompt_text=PROMPT_TEXT,
                # A stray extra key that looks authoritative must be ignored.
                prompt_binding={**_prompt_binding(), "caption_text_set_sha256": "0" * 64},
                visible_persistent_character_ids=("C002",),
                identity_reference_paths=anchors,
                generation_provider="host-imagegen",
                provider=_CurrentStubProvider(),
            )
            self.assertNotEqual(evidence.caption_text_set_sha256, "0" * 64)
            self.assertEqual(
                evidence.caption_text_set_sha256,
                hashlib.sha256(CAPTION_TEXT.encode("utf-8")).hexdigest(),
            )

    def test_signature_covers_the_new_caption_binding_fields(self) -> None:
        """Editing any new field after minting breaks the provider signature."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            image, anchors = self._scene(root)
            evidence = review_current_shot(
                shot_id="SHOT_CG_0001",
                task_id=TASK_ID,
                image_path=image,
                caption_group=_current_group(),
                caption_texts={CAPTION_ID: CAPTION_TEXT},
                proposition=_proposition(),
                prompt_text=PROMPT_TEXT,
                prompt_binding=_prompt_binding(),
                visible_persistent_character_ids=("C002",),
                identity_reference_paths=anchors,
                generation_provider="host-imagegen",
                provider=_CurrentStubProvider(),
            )
            evidence.verify()
            for mutation in (
                {"task_id": "TASK-CG-9999"},
                {"group_id": "G999"},
                {"caption_ids": ("caption-9999",)},
                {"caption_text_set_sha256": hashlib.sha256(b"other").hexdigest()},
            ):
                with self.subTest(mutation=mutation):
                    with self.assertRaises(VisionReviewError):
                        replace(evidence, **mutation).verify()

    def test_evidence_document_round_trip_preserves_the_reviewed_caption_binding(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            image, anchors = self._scene(root)
            evidence = review_current_shot(
                shot_id="SHOT_CG_0001",
                task_id=TASK_ID,
                image_path=image,
                caption_group=_current_group(),
                caption_texts={CAPTION_ID: CAPTION_TEXT},
                proposition=_proposition(),
                prompt_text=PROMPT_TEXT,
                prompt_binding=_prompt_binding(),
                visible_persistent_character_ids=("C002",),
                identity_reference_paths=anchors,
                generation_provider="host-imagegen",
                provider=_CurrentStubProvider(),
            )
            payload = evidence.to_dict()
            for key in (
                "shot_id", "task_id", "group_id", "caption_ids",
                "caption_text_set_sha256", "caption_group_sha256",
                "proposition_sha256", "prompt_sha256", "image_sha256",
                "vision_provider", "vision_call_id",
                "semantic_review_status", "reality_review_status", "identity_review_status",
                "semantic_review_reasoning", "reality_review_reasoning", "identity_review_reasoning",
            ):
                self.assertIn(key, payload, key)
            self.assertEqual(CurrentVisionEvidence.from_mapping(payload), evidence)

            document = build_current_vision_evidence_document("huozhe-pilot-r1", [evidence])
            loaded = load_current_vision_evidence_document(
                document, current_bindings={evidence.shot_id: _current_binding(image, anchors)}
            )
            self.assertEqual(loaded[evidence.shot_id].caption_text_set_sha256,
                             evidence.caption_text_set_sha256)


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
