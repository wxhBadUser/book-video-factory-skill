from __future__ import annotations

import math
import statistics
from pathlib import Path
from typing import Any

from PIL import Image, ImageStat


REFERENCE_ENVELOPE = {
    "median_luma": (49.0, 73.0),
    "dark_pixel_share_percent": (30.0, 53.0),
    "mean_saturation": (106.0, 132.0),
    "warm_pixel_share_percent": (43.0, 57.0),
}
REQUIRED_ENVELOPE_KEYS = tuple(REFERENCE_ENVELOPE)


def validate_reference_envelope(
    value: dict[str, Any] | None,
) -> dict[str, tuple[float, float]]:
    if value is None:
        return dict(REFERENCE_ENVELOPE)
    if not isinstance(value, dict) or set(value) != set(REQUIRED_ENVELOPE_KEYS):
        raise ValueError("reference envelope keys must match diagnostics")
    result: dict[str, tuple[float, float]] = {}
    for key in REQUIRED_ENVELOPE_KEYS:
        limits = value[key]
        if (
            not isinstance(limits, (list, tuple))
            or len(limits) != 2
            or not all(
                isinstance(item, (int, float)) and not isinstance(item, bool)
                for item in limits
            )
            or float(limits[0]) > float(limits[1])
        ):
            raise ValueError(f"invalid reference envelope: {key}")
        result[key] = (float(limits[0]), float(limits[1]))
    return result


def _median_from_histogram(histogram: list[int]) -> float:
    total = sum(histogram)
    midpoint = total / 2
    cumulative = 0
    for value, count in enumerate(histogram):
        cumulative += count
        if cumulative >= midpoint:
            return float(value)
    return 0.0


def analyze_image(path: Path) -> dict[str, Any]:
    source = path.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    with Image.open(source) as opened:
        rgb = opened.convert("RGB")
        width, height = rgb.size
        sample = rgb.copy()
        sample.thumbnail((640, 640), Image.Resampling.LANCZOS)
    luma = sample.convert("L")
    histogram = luma.histogram()
    pixels = sample.width * sample.height
    dark_share = sum(histogram[:40]) / pixels * 100
    saturation = ImageStat.Stat(sample.convert("HSV").getchannel("S")).mean[0]
    pixel_data = (
        sample.get_flattened_data()
        if hasattr(sample, "get_flattened_data")
        else sample.getdata()
    )
    warm = sum(
        1
        for red, green, blue in pixel_data
        if red > blue * 1.10 and red > green * 1.02
    )
    divisor = math.gcd(width, height)
    return {
        "path": str(source),
        "width": width,
        "height": height,
        "aspect_ratio": f"{width // divisor}:{height // divisor}",
        "is_landscape_16_9": abs(width / height - 16 / 9) <= 0.002,
        "median_luma": round(_median_from_histogram(histogram), 3),
        "dark_pixel_share_percent": round(dark_share, 3),
        "mean_saturation": round(saturation, 3),
        "warm_pixel_share_percent": round(warm / pixels * 100, 3),
    }


def inspect_image_batch(
    assets: list[dict[str, Any]],
    reference_envelope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not assets:
        raise ValueError("image batch must be nonempty")
    envelope = validate_reference_envelope(reference_envelope)
    measured: list[dict[str, Any]] = []
    for asset in assets:
        if not isinstance(asset, dict):
            raise ValueError("image asset must be an object")
        metrics = analyze_image(Path(asset["path"]))
        measured.append(
            {
                "asset_id": str(asset.get("asset_id", "")),
                **metrics,
                "human_qa": "pending",
            }
        )
    batch_metrics = {
        key: round(
            float(statistics.median(item[key] for item in measured)),
            3,
        )
        for key in REFERENCE_ENVELOPE
    }
    warnings = [
        {
            "metric": key,
            "observed": batch_metrics[key],
            "reference_min": limits[0],
            "reference_max": limits[1],
        }
        for key, limits in envelope.items()
        if not limits[0] <= batch_metrics[key] <= limits[1]
    ]
    wrong_ratio = [
        item["asset_id"] for item in measured if not item["is_landscape_16_9"]
    ]
    if wrong_ratio:
        warnings.append(
            {
                "metric": "aspect_ratio",
                "observed": wrong_ratio,
                "reference": "16:9",
            }
        )
    return {
        "schema_version": "1.0",
        "schema_id": "cinematic-image-machine-qc.v1",
        "machine_diagnostic_status": "warn" if warnings else "pass",
        "human_review_status": "pending",
        "note": (
            "Reference-envelope metrics are diagnostics, not aesthetic approval. "
            "Human review must inspect light motivation, shadow readability, anatomy, "
            "continuity, visible text/watermarks, and narrative fit."
        ),
        "reference_envelope": envelope,
        "batch_metrics": batch_metrics,
        "warnings": warnings,
        "assets": measured,
    }
