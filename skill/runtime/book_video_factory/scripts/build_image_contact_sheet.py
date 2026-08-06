#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a labeled cinematic asset contact sheet")
    parser.add_argument("--image-assets", type=Path, required=True)
    parser.add_argument("--image-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--columns", type=int, default=2)
    args = parser.parse_args()
    if args.columns < 1 or args.columns > 6:
        raise ValueError("columns must be between 1 and 6")
    manifest = json.loads(args.image_assets.read_text(encoding="utf-8"))
    assets = manifest.get("assets", [])
    if not isinstance(assets, list) or not assets:
        raise ValueError("image asset manifest requires nonempty assets")
    cell_width, image_height, label_height = 960, 540, 54
    rows = (len(assets) + args.columns - 1) // args.columns
    sheet = Image.new(
        "RGB",
        (cell_width * args.columns, (image_height + label_height) * rows),
        (16, 16, 18),
    )
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=28)
    for index, asset in enumerate(assets):
        if not isinstance(asset, dict):
            raise ValueError("image asset entry must be an object")
        source = (args.image_dir / str(asset["path"])).resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        image = Image.open(source).convert("RGB")
        fitted = ImageOps.fit(
            image,
            (cell_width, image_height),
            method=Image.Resampling.LANCZOS,
        )
        column = index % args.columns
        row = index // args.columns
        x = column * cell_width
        y = row * (image_height + label_height)
        sheet.paste(fitted, (x, y))
        label = (
            f"{asset.get('asset_id', '?')}  "
            f"QA: {asset.get('qa', 'pending')}  "
            f"SHA: {str(asset.get('sha256', ''))[:12]}"
        )
        draw.text((x + 20, y + image_height + 12), label, fill=(240, 240, 240), font=font)
    target = args.output.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target, quality=94)
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
