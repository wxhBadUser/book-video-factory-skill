"""P0-6: typed scene/object anchors must reach the provider reference pack.

Aligned to the REAL resolver API on 2026-08-16 (the plan draft called
`resolve_reference_pack(task=, foundation=)` with a plain-dict foundation; the
real signature is `resolve_reference_pack(root, task, foundation)` over a
verified project foundation with typed identity/location/style entries and a
hash-bound asset registry). The task carries `scene_anchor_task_ids` /
`object_anchor_task_ids`; the resolver must emit `location_anchor` +
`object_anchor` reference roles, and the scene reference evidence must record
them and fail closed when a declared role is missing.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import pytest

from book_video_factory.visual_foundation import (
    approve_visual_foundation,
    build_visual_foundation,
)
from book_video_factory.visual_foundation.evidence import (
    load_scene_reference_evidence,
    write_scene_reference_evidence,
)
from book_video_factory.visual_foundation.manifests import _visual_asset_manifest
from book_video_factory.visual_foundation.resolver import (
    resolve_reference_pack,
)
from book_video_factory.visual_foundation.runner import (
    ReferenceRunnerError,
    execute_reference_conditioned,
    verify_reference_inputs_match_pack,
)

from test_visual_foundation import _foundation_input, _png, _profile, _sha, _write_json


def _build_typed_anchor_project(base: Path) -> Path:
    """Real foundation project with a scene anchor (LOC_OPEN_SEA) and an object
    anchor (OBJ_HARPOON) registered, so the resolver can emit those roles."""
    project = base / "warehouse/projects/pilot"
    _write_json(project / "project.json", {"schema_version": "1.0", "workflow": {"visual_foundation_policy": "required"}})
    proj3 = project / "03_images_生成图片"
    profile = _profile()
    profile["scene_anchors"] = [{
        "anchor_id": "LOC_OPEN_SEA", "task_id": "LOC_OPEN_SEA",
        "name": "open sea", "prompt_subject": "vast open ocean, no land",
        "approved": True,
    }]
    profile["object_anchors"] = [{
        "anchor_id": "OBJ_HARPOON", "task_id": "OBJ_HARPOON",
        "name": "harpoon", "prompt_subject": "weathered harpoon, wooden shaft",
        "approved": True,
    }]
    _write_json(proj3 / "BOOK_VISUAL_PROFILE.json", profile)
    profile_sha = _sha(proj3 / "BOOK_VISUAL_PROFILE.json")
    assets = []
    for task_id in ("ANCHOR_C001_FRONT", "ANCHOR_C001_FULL_BODY", "ANCHOR_C002_FRONT"):
        p = _png(project / "assets/generated/anchors" / f"{task_id}.png")
        assets.append({"task_id": task_id, "task_kind": "character_anchor", "path": f"assets/generated/anchors/{task_id}.png", "sha256": _sha(p)})
    p = _png(project / "assets/generated/lookdev" / "LOOKDEV_LD01.png")
    assets.append({"task_id": "LOOKDEV_LD01", "task_kind": "lookdev", "path": "assets/generated/lookdev/LOOKDEV_LD01.png", "sha256": _sha(p)})
    for task_id in ("LOC_OPEN_SEA", "OBJ_HARPOON"):
        p = _png(project / "assets/generated/anchors" / f"{task_id}.png")
        kind = "scene_anchor" if task_id.startswith("LOC_") else "object_anchor"
        assets.append({"task_id": task_id, "task_kind": kind, "path": f"assets/generated/anchors/{task_id}.png", "sha256": _sha(p)})
    _write_json(proj3 / "VISUAL_ASSET_MANIFEST.json", {
        "schema_version": "visual-asset-manifest.v1",
        "release_id": "r1",
        "provider": "host-imagegen",
        "assets": assets,
        "registered_asset_count": len(assets),
        "last_registered_at": "2026-08-16T00:00:00+00:00",
    })
    fixture = _foundation_input(profile_sha)
    for master in fixture["style_masters"]:
        master["image_sha256"] = _sha(project / master["image_path"])
    fixture["location_anchors"] = [{
        "location_id": "LOC_OPEN_SEA",
        "name": "open sea",
        "anchor_task_id": "LOC_OPEN_SEA",
        "image_path": "assets/generated/anchors/LOC_OPEN_SEA.png",
        "image_sha256": _sha(project / "assets/generated/anchors/LOC_OPEN_SEA.png"),
        "approved": True,
        "aliases": [],
    }]
    build_visual_foundation(project, fixture)
    approve_visual_foundation(project, release_id="r1", reviewer="tester", note="typed-anchor fixture")
    return project


def _task(*, object_ids=("OBJ_HARPOON",), location_id="LOC_OPEN_SEA") -> dict:
    return {
        "task_id": "VB_001",
        "scene_id": "VB_001",
        "release_id": "r1",
        "scene_anchor_task_ids": [location_id] if location_id else [],
        "object_anchor_task_ids": list(object_ids),
        "identity_reference_task_ids": ["ANCHOR_C001_FRONT"],
        "style_reference_ids": ["STYLE_MASTER_RURAL_DAY"],
        "palette_id": "EARTH_DAY",
        "lighting_id": "SUN_WORN",
        "visual_event_state": {"action_predicate": "alive_active", "cause_type": "", "is_reference_death": False, "actors": [], "participant_roles": [], "objects": [], "subject_state": "", "required_observable_evidence": [], "forbidden_contradictory_state": []},
        "source_beat_ids": ["B001"],
        "chapter_ids": ["3"],
        "reference_contract": {
            "schema_version": "visual-reference-contract.v1",
            "style": {"required": True},
            "identity": {"required_when_characters_present": True, "characters": []},
            "location": {"location_id": location_id, "location_reference_type": "persistent_location_anchor", "required_persistent": True},
            "continuity": {"use_previous_scene": False, "required": False, "reason": ""},
        },
    }


def _foundation(project: Path) -> dict:
    from book_video_factory.visual_foundation.manifests import load_foundation
    return load_foundation(project, "r1")


class TestTypedAnchorTransport(unittest.TestCase):
    def test_reference_pack_includes_scene_and_object_anchors(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = _build_typed_anchor_project(Path(temp))
            pack = resolve_reference_pack(project, _task(), _foundation(project))
            roles = [r["role"] for r in pack["references"]]
            assert "location_anchor" in roles
            assert "object_anchor" in roles
            assert "style_master" in roles

    def test_reference_inputs_evidence_records_anchor_types(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = _build_typed_anchor_project(Path(temp))
            pack = resolve_reference_pack(project, _task(), _foundation(project))
            normalized = [{"role": r["role"], "reference_id": r["reference_id"],
                           "image_path": r["image_path"], "image_sha256": r["image_sha256"]} for r in pack["references"]]
            write_scene_reference_evidence(
                project, release_id="r1", task_id="VB_001", reference_pack=pack,
                reference_inputs=normalized, generation_attempt_id="attempt-1",
                provider="flow", provider_receipt="receipt-1", generation_mode="i2i",
                prompt_sha256="0" * 64, output_image_sha256="0" * 64,
            )
            record = load_scene_reference_evidence(project)["VB_001"]
            assert any(x["role"] == "object_anchor" for x in record["reference_inputs"])
            verify_reference_inputs_match_pack(record["reference_inputs"], pack)  # 证据 == pack，否则拒绝

    def test_unknown_object_anchor_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = _build_typed_anchor_project(Path(temp))
            task = _task(object_ids=("OBJ_NOT_REGISTERED",))
            with pytest.raises(Exception):
                resolve_reference_pack(project, task, _foundation(project))

    def test_evidence_missing_object_anchor_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = _build_typed_anchor_project(Path(temp))
            pack = resolve_reference_pack(project, _task(), _foundation(project))
            normalized = [{"role": r["role"], "reference_id": r["reference_id"],
                           "image_path": r["image_path"], "image_sha256": r["image_sha256"]} for r in pack["references"]]
            normalized = [x for x in normalized if x["role"] != "object_anchor"]  # 造缺失
            with pytest.raises(Exception):
                verify_reference_inputs_match_pack(normalized, pack)

    def test_runner_gate_accepts_style_master_and_rejects_unregistered(self) -> None:
        # P0-8: the approved-assets gate must accept references whose reference_id is
        # NOT an asset task id (style_master -> STYLE_MASTER_*, previous_scene -> scene
        # id) by matching the reference IMAGE (path+hash) to the manifest; and it must
        # reject a reference whose image is not a registered manifest asset.
        with tempfile.TemporaryDirectory() as temp:
            project = _build_typed_anchor_project(Path(temp))
            assets = _visual_asset_manifest(project).get("assets", [])
            pack = resolve_reference_pack(project, _task(), _foundation(project))
            assert any(r["role"] == "style_master" for r in pack["references"])
            execute_reference_conditioned(
                reference_pack=pack, prompt="p", output_path="o.png",
                provider="host-imagegen", approved_assets=assets,
            )  # must NOT false-reject the style_master
            forged = dict(pack)
            forged["references"] = [{"role": "style_master", "reference_id": "X",
                                     "image_path": "assets/generated/lookdev/NOT_REG.png",
                                     "image_sha256": "0" * 64}]
            with pytest.raises(ReferenceRunnerError):
                execute_reference_conditioned(
                    reference_pack=forged, prompt="p", output_path="o.png",
                    provider="host-imagegen", approved_assets=assets,
                )


if __name__ == "__main__":
    unittest.main()
