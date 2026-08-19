# -*- coding: utf-8 -*-
"""Raw scene asset cleanliness detection (single-frame review round).

A_CLEAN means: no burned caption, no review subtitle, no black letterbox, no UI,
no watermark. Black-row counting alone misses burned subtitles on non-black
backgrounds, so the detector also scans for text-like horizontal bands.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image


def classify_raw_cleanliness(image_path: str | Path) -> dict[str, object]:
    path = Path(image_path)
    with Image.open(path) as im:
        width, height = im.size
        px = im.convert("RGB")

        def row_stats(y: int) -> tuple[float, float]:
            vals = [px.getpixel((x, y)) for x in range(0, width, 4)]
            mean = sum(sum(v) for v in vals) / (len(vals) * 3)
            variance = sum((sum(v) / 3 - mean) ** 2 for v in vals) / len(vals)
            return mean, variance

        all_rows = [row_stats(y) for y in range(height)]
        bottom_rows = all_rows[int(height * 0.6):]
        bottom_black = sum(1 for mean, _var in bottom_rows if mean < 8)
        bottom_black_ratio = bottom_black / len(bottom_rows) if bottom_rows else 0.0

        # Text-like bands: contiguous runs of high-variance rows.
        def bands(rows: list[tuple[float, float]], min_mean: float, max_mean: float,
                  min_var: float, min_run: int) -> list[list[int]]:
            runs: list[list[int]] = []
            current: list[int] = []
            for index, (mean, variance) in enumerate(rows):
                if min_mean <= mean <= max_mean and variance > min_var:
                    current.append(index)
                else:
                    if len(current) >= min_run:
                        runs.append(current)
                    current = []
            if len(current) >= min_run:
                runs.append(current)
            return runs

        lower_text_bands = bands(
            all_rows[int(height * 0.7):],  # subtitle zone
            min_mean=8, max_mean=235, min_var=60, min_run=6,
        )
        whole_text_bands = bands(
            all_rows,
            min_mean=8, max_mean=235, min_var=250, min_run=10,
        )

        reasons: list[str] = []
        verdict = "A_CLEAN"
        if bottom_black_ratio > 0.20:
            reasons.append(f"black letterbox (bottom black {bottom_black_ratio:.0%})")
            verdict = "B_CONTAMINATED"
        if lower_text_bands:
            reasons.append(
                f"text-like band in subtitle zone at relative rows "
                f"{[int((int(height*0.7) + run[0]) / height * 100) for run in lower_text_bands[:3]]}%"
            )
            if verdict == "A_CLEAN":
                verdict = "VERIFY"
        if whole_text_bands:
            reasons.append(
                f"readable text band in frame at relative rows "
                f"{[int(run[0] / height * 100) for run in whole_text_bands[:3]]}%"
            )
            if verdict == "A_CLEAN":
                verdict = "VERIFY"
        return {
            "size": f"{width}x{height}",
            "bottom_black_ratio": round(bottom_black_ratio, 3),
            "subtitle_zone_text_bands": len(lower_text_bands),
            "frame_text_bands": len(whole_text_bands),
            "verdict": verdict,
            "reasons": reasons,
        }
