from __future__ import annotations

from typing import Any, Mapping


class OrientationError(ValueError):
    """A project mixes incompatible HBG output orientations."""


_CANVASES = {
    "landscape": (1920, 1080),
    "portrait": (1080, 1920),
}


def canvas_for_orientation(orientation: str) -> dict[str, int | str]:
    try:
        width, height = _CANVASES[orientation]
    except KeyError as error:
        raise OrientationError("orientation must be landscape or portrait") from error
    return {"width": width, "height": height, "orientation": orientation}


def validate_orientation_contract(
    orientation: str, canvas: Mapping[str, Any]
) -> dict[str, int | str]:
    expected = canvas_for_orientation(orientation)
    observed = {
        "width": canvas.get("width"),
        "height": canvas.get("height"),
        "orientation": canvas.get("orientation", orientation),
    }
    if observed != expected:
        raise OrientationError(
            f"orientation {orientation} contradicts canvas "
            f"{observed.get('width')}x{observed.get('height')} "
            f"({observed.get('orientation')})"
        )
    return expected
