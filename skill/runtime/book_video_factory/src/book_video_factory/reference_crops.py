from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

from .manifests import sha256_file


def _validated_crop_box(value: Any, source: Path) -> tuple[int, int, int, int]:
    if (
        not isinstance(value, list)
        or len(value) != 4
        or not all(isinstance(item, int) and not isinstance(item, bool) for item in value)
    ):
        raise ValueError(f"invalid crop_box for {source}")
    left, top, right, bottom = value
    with Image.open(source) as opened:
        width, height = opened.size
    if (
        left < 0
        or top < 0
        or right > width
        or bottom > height
        or left >= right
        or top >= bottom
    ):
        raise ValueError(f"crop_box is outside source bounds for {source}")
    return left, top, right, bottom


def prepare_reference_crops(
    plan: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    references = plan.get("references")
    if not isinstance(references, list) or not references:
        raise ValueError("crop plan requires nonempty references")

    destination = output_dir.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    output_names: set[str] = set()
    assets: list[dict[str, Any]] = []

    for item in references:
        if not isinstance(item, dict):
            raise ValueError("crop plan references must be objects")
        source = Path(str(item.get("source", ""))).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        crop_box = _validated_crop_box(item.get("crop_box"), source)

        output_name = str(item.get("output_name", "")).strip()
        if (
            not output_name
            or Path(output_name).name != output_name
            or output_name in output_names
        ):
            raise ValueError(f"invalid or duplicate output_name: {output_name}")
        output_names.add(output_name)
        target = destination / output_name

        with Image.open(source) as opened:
            crop = opened.convert("RGB").crop(crop_box)
            crop.save(target, format="JPEG", quality=96, subsampling=0)

        assets.append(
            {
                "reference_id": str(item.get("reference_id", "")).strip(),
                "path": str(target),
                "source": str(source),
                "crop_box": list(crop_box),
                "role": str(item.get("role", "")).strip(),
                "width": crop.width,
                "height": crop.height,
                "sha256": sha256_file(target),
            }
        )

    return {
        "schema_version": "1.0",
        "style_id": str(plan.get("style_id", "")).strip(),
        "assets": assets,
    }
