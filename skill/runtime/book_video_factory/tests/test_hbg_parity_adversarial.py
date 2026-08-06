from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from book_video_factory.hbg_bridge.provenance import (
    HbgBridgeProvenanceError,
    _verify_vendor,
)
from book_video_factory.visual_stage.asset_registry import (
    VisualAssetRegistrationError,
    _validate_source,
)


REPO = Path(__file__).resolve().parents[2]


def _load_vfinal_scanner():
    path = REPO / "scripts/verify_vfinal_architecture.py"
    spec = importlib.util.spec_from_file_location("hbg_parity_vfinal_scanner", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_vendor_lock_rejects_a_modified_pristine_hbg_file(tmp_path: Path) -> None:
    source = REPO / "vendor/hbg-life-simulation"
    target = tmp_path / "vendor/hbg-life-simulation"
    shutil.copytree(source, target)
    lock = json.loads((target / "UPSTREAM_LOCK.json").read_text(encoding="utf-8"))
    victim = target / next(iter(lock["files"]))
    victim.write_bytes(victim.read_bytes() + b"\nmodified")
    with pytest.raises(HbgBridgeProvenanceError, match="hash mismatch"):
        _verify_vendor(tmp_path)


def test_portrait_task_rejects_a_landscape_provider_asset(tmp_path: Path) -> None:
    image = tmp_path / "provider.png"
    Image.new("RGB", (1920, 1080), (30, 40, 50)).save(image)
    with pytest.raises(VisualAssetRegistrationError, match="1080x1920"):
        _validate_source(
            image,
            set(),
            {"width": 1080, "height": 1920, "orientation": "portrait"},
        )


def test_scanner_blocks_private_tts_image_and_ffmpeg_engines(tmp_path: Path) -> None:
    code = tmp_path / "book_video_factory/src/book_video_factory/director_stage/attack.py"
    code.parent.mkdir(parents=True)
    code.write_text(
        "import edge_tts\n"
        "import requests, subprocess\n"
        "requests.post('https://private.invalid/images.generate')\n"
        "subprocess.run(['ffmpeg', '-version'])\n",
        encoding="utf-8",
    )
    report = _load_vfinal_scanner().scan_repository(tmp_path)
    identifiers = {item["check_id"] for item in report["findings"]}
    assert {"duplicate_tts_engine", "private_image_client", "duplicate_media_engine"} <= identifiers
