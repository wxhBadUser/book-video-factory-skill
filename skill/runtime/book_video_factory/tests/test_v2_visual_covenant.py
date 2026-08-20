from __future__ import annotations

import hashlib
import json
from pathlib import Path

from book_video_factory.visual_covenant import (
    VisualCovenantError,
    covenant_canonical_sha,
    covenant_production_assets,
    load_asset_catalog,
    load_visual_covenant,
    promote_covenant_assets,
    verify_asset_catalog,
    verify_visual_covenant,
)


def _sha(text: str | bytes) -> str:
    if isinstance(text, str):
        text = text.encode("utf-8")
    return hashlib.sha256(text).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _locked_project(tmp_path: Path, *, name: str = "pilot") -> Path:
    project = tmp_path / "warehouse" / "projects" / name
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
    script_text = "covenant narration"
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


def _asset_file(project: Path, relative: str, content: bytes = b"asset-png-bytes") -> None:
    target = project / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def _covenant_payload(*, assets: list[dict]) -> dict:
    payload = {
        "schema_version": "visual-covenant.v2",
        "release_id": "release-1",
        "project_id": "pilot",
        "locked_script_sha256": _sha("locked"),
        "assets": assets,
    }
    payload["visual_covenant_sha256"] = covenant_canonical_sha(payload)
    return payload


def _eligible_asset(asset_id: str, *, relative: str, family: str = "character:jane", func: str = "character_portrait") -> dict:
    return {
        "asset_id": asset_id,
        "asset_family": family,
        "visual_function": func,
        "source": "visual_covenant",
        "production_eligible": True,
        "technical_status": "verified",
        "path": relative,
        "file_sha256": _sha("asset-png-bytes"),
        "provenance": {"provider": "host-imagegen", "tool_call_id": f"call-{asset_id}"},
    }


def test_eligible_covenant_asset_is_directly_reusable(tmp_path: Path) -> None:
    project = _locked_project(tmp_path)
    asset = _eligible_asset("COV_ASSET_02", relative="03_images_生成图片/covenant/jane.png")
    _asset_file(project, asset["path"])
    _write_json(project / "04_visual_covenant_视觉契约/VISUAL_COVENANT.v2.json", _covenant_payload(assets=[asset]))
    assert verify_visual_covenant(project)["status"] == "visual_covenant_verified"
    promote_covenant_assets(project)
    production = covenant_production_assets(project)
    assert [item["asset_id"] for item in production] == ["COV_ASSET_02"]


def test_promotion_preserves_hash_and_provenance(tmp_path: Path) -> None:
    project = _locked_project(tmp_path)
    asset = _eligible_asset("COV_JANE", relative="03_images_生成图片/covenant/jane.png")
    _asset_file(project, asset["path"], content=b"exact-raw-bytes")
    asset["file_sha256"] = _sha("exact-raw-bytes")
    _write_json(project / "04_visual_covenant_视觉契约/VISUAL_COVENANT.v2.json", _covenant_payload(assets=[asset]))
    promote_covenant_assets(project)
    catalog = load_asset_catalog(project)
    entry = catalog["assets"][0]
    assert entry["file_sha256"] == _sha("exact-raw-bytes")
    assert entry["provenance"] == {"provider": "host-imagegen", "tool_call_id": "call-COV_JANE"}
    assert entry["covenant_approval_sha256"] == covenant_canonical_sha(
        _covenant_payload(assets=[asset])
    )


def test_non_eligible_reference_does_not_enter_production_catalog(tmp_path: Path) -> None:
    project = _locked_project(tmp_path)
    reference = _eligible_asset("COV_REF_01", relative="03_images_生成图片/reference/contact.png", func="style_reference")
    reference["production_eligible"] = False
    _asset_file(project, reference["path"])
    _write_json(project / "04_visual_covenant_视觉契约/VISUAL_COVENANT.v2.json", _covenant_payload(assets=[reference]))
    promote_covenant_assets(project)
    assert verify_asset_catalog(project)["catalog_asset_count"] == 0
    assert covenant_production_assets(project) == []


def test_invalid_covenant_approval_invalidates_all_promotions(tmp_path: Path) -> None:
    project = _locked_project(tmp_path)
    asset = _eligible_asset("COV_ASSET_02", relative="03_images_生成图片/covenant/jane.png")
    _asset_file(project, asset["path"])
    covenant = _covenant_payload(assets=[asset])
    _write_json(project / "04_visual_covenant_视觉契约/VISUAL_COVENANT.v2.json", covenant)
    promote_covenant_assets(project)
    # Tamper with the covenant payload (change an asset family), leaving the self sha stale.
    tampered = dict(covenant)
    tampered["assets"][0]["asset_family"] = "character:jane-rewritten"
    _write_json(project / "04_visual_covenant_视觉契约/VISUAL_COVENANT.v2.json", tampered)
    assert verify_visual_covenant(project)["status"] != "visual_covenant_verified"
    assert verify_asset_catalog(project)["status"] == "blocked_by_covenant_integrity"
    try:
        covenant_production_assets(project)
    except VisualCovenantError:
        pass
    else:
        raise AssertionError("stale covenant approval must block production asset loading")


def test_different_complexity_work_counts_differ_by_covenant(tmp_path: Path) -> None:
    project = _locked_project(tmp_path)
    assets = [
        _eligible_asset(f"COV_A{i}", relative=f"03_images_生成图片/covenant/a{i}.png", family="character:a", func="character_portrait")
        for i in range(3)
    ]
    for asset in assets:
        _asset_file(project, asset["path"], content=f"a-{asset['asset_id']}".encode())
        asset["file_sha256"] = _sha(f"a-{asset['asset_id']}")
    _write_json(project / "04_visual_covenant_视觉契约/VISUAL_COVENANT.v2.json", _covenant_payload(assets=assets))
    promote_covenant_assets(project)
    assert verify_asset_catalog(project)["catalog_asset_count"] == 3
    assert len(covenant_production_assets(project)) == 3
