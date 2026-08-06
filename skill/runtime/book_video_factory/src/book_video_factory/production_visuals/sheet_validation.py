from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from book_video_factory.manifests import sha256_file


class SheetValidationError(RuntimeError):
    """A generated 2x2 sheet is not structurally safe for the HBG splitter."""


@dataclass(frozen=True)
class SheetValidationResult:
    source: Path
    sha256: str
    width: int
    height: int
    orientation: str
    panel_order: list[str]
    panel_boxes: list[tuple[int, int, int, int]]
    outer_margin: int
    gutter: int


def _order(value: list[str], label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) != 4
        or any(not isinstance(item, str) or not item or item != item.strip() for item in value)
        or len(set(value)) != 4
    ):
        raise SheetValidationError(f"{label} must bind four unique task IDs in panel order")
    return list(value)


def _black_region(image: Image.Image, box: tuple[int, int, int, int], label: str) -> None:
    extrema = image.crop(box).getextrema()
    if not isinstance(extrema, tuple) or len(extrema) < 3:
        raise SheetValidationError(f"{label} cannot be inspected")
    channel_ranges = [item for item in extrema[:3] if isinstance(item, tuple)]
    if len(channel_ranges) != 3 or any(high > 16 or high - low > 8 for low, high in channel_ranges):
        raise SheetValidationError(f"{label} is not a continuous uniform black gutter or contains pollution")


def validate_scene_sheet(
    source: Path,
    *,
    orientation: str,
    panel_order: list[str],
    expected_panel_order: list[str] | None = None,
) -> SheetValidationResult:
    path = source.expanduser().resolve()
    if path.is_symlink() or not path.is_file():
        raise SheetValidationError("scene sheet is missing or symlinked")
    if orientation not in {"landscape", "portrait"}:
        raise SheetValidationError("sheet orientation must be landscape or portrait")
    order = _order(panel_order, "panel_order")
    if expected_panel_order is not None and order != _order(expected_panel_order, "expected_panel_order"):
        raise SheetValidationError("declared panel order does not match SHEET_MAP")
    panel_width, panel_height = ((1920, 1080) if orientation == "landscape" else (1080, 1920))
    outer = gutter = 8
    expected_width = panel_width * 2 + outer * 2 + gutter
    expected_height = panel_height * 2 + outer * 2 + gutter
    try:
        with Image.open(path) as probe:
            probe.verify()
        with Image.open(path) as opened:
            image = opened.convert("RGB")
    except (OSError, UnidentifiedImageError) as error:
        raise SheetValidationError(f"scene sheet is not a decodable image: {error}") from error
    if image.size != (expected_width, expected_height):
        raise SheetValidationError(
            f"scene sheet canvas is not exact 2x2-compatible {expected_width}x{expected_height}: {image.width}x{image.height}"
        )
    x2 = outer + panel_width + gutter
    y2 = outer + panel_height + gutter
    panel_boxes = [
        (outer, outer, outer + panel_width, outer + panel_height),
        (x2, outer, x2 + panel_width, outer + panel_height),
        (outer, y2, outer + panel_width, y2 + panel_height),
        (x2, y2, x2 + panel_width, y2 + panel_height),
    ]
    gutter_regions = [
        ((0, 0, expected_width, outer), "top outer margin"),
        ((0, expected_height - outer, expected_width, expected_height), "bottom outer margin"),
        ((0, 0, outer, expected_height), "left outer margin"),
        ((expected_width - outer, 0, expected_width, expected_height), "right outer margin"),
        ((outer + panel_width, 0, x2, expected_height), "vertical gutter"),
        ((0, outer + panel_height, expected_width, y2), "horizontal gutter"),
    ]
    for box, label in gutter_regions:
        _black_region(image, box, label)
    for index, box in enumerate(panel_boxes, start=1):
        extrema = image.crop(box).getextrema()
        if all(high <= 16 for _low, high in extrema[:3]):
            raise SheetValidationError(f"panel {index} is empty black instead of a native {orientation} panel")
        width = box[2] - box[0]
        height = box[3] - box[1]
        if (orientation == "landscape" and width <= height) or (orientation == "portrait" and height <= width):
            raise SheetValidationError(f"panel {index} has the wrong native orientation")
    return SheetValidationResult(
        path,
        sha256_file(path),
        image.width,
        image.height,
        orientation,
        order,
        panel_boxes,
        outer,
        gutter,
    )

