"""Static-still motion planning (M4A, hold-only).

Images in new production are completely static. Every scene image is a still:

- zoom in / zoom out / pan / drift / crop animation / pseudo-camera motion are
  all BLOCKED -- there is no camera move in new production.
- Transition is a default hard cut; a small number of emotional continuities
  may use a 6-8 frame dissolve, and a dissolve must never be used to mask a
  semantic scene mismatch.
- Every hold keeps ``motion: "hold"`` and ``zoom: 0.0``.

The legacy director/render path keeps its own vocabulary; this module is the
*new-production* motion contract (M4A simple-zoom pipeline).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

ALLOWED_MOTIONS = {"hold"}
# Legacy motions are not translated: they BLOCK new production.
BLOCKED_MOTIONS = {
    "zoom-in",
    "zoom-out",
    "pan-left",
    "pan-right",
    "pan-up",
    "pan-down",
    "slow_zoom_in",
    "slow_zoom_out",
    "static",
}
MAX_ZOOM = 0.0  # images are completely static
OUTPUT_WIDTH = 1920
OUTPUT_HEIGHT = 1080

# Subtitle safe zone for the 1920x1080 canvas (M9): captions stay inside a
# centered band that never collides with the 16:9 overscan edges.
SAFE_MARGIN_X = 120
SAFE_MARGIN_Y = 90


class SimpleZoomError(RuntimeError):
    pass


def caption_safe_zone(
    width: int = OUTPUT_WIDTH,
    height: int = OUTPUT_HEIGHT,
) -> dict[str, int]:
    """Centered caption safe rectangle (excludes zoom-motion edge drift)."""

    return {
        "x": SAFE_MARGIN_X,
        "y": SAFE_MARGIN_Y,
        "width": width - 2 * SAFE_MARGIN_X,
        "height": height - 2 * SAFE_MARGIN_Y,
    }


@dataclass(frozen=True)
class ZoomEntry:
    hold_id: str
    motion: str
    zoom: float
    start: float
    end: float
    duration: float
    rationale: str


def _resolve_motion(
    hold: Mapping[str, Any],
    intent: str | None,
    *,
    default: str,
) -> tuple[str, str]:
    """Pick a motion for one hold from intent or duration default."""

    requested = hold.get("motion")
    if requested is not None:
        if requested in BLOCKED_MOTIONS:
            raise SimpleZoomError(
                f"hold {hold.get('hold_id')!r} requests blocked motion {requested!r}; "
                "pan/legacy moves are not allowed in new production"
            )
        if requested not in ALLOWED_MOTIONS:
            raise SimpleZoomError(
                f"hold {hold.get('hold_id')!r} requests unknown or blocked motion {requested!r}; "
                "new production is hold-only"
            )
        return requested, "explicit hold motion"
    if intent is not None:
        raise SimpleZoomError(
            f"hold {hold.get('hold_id')!r} visual intent {intent!r} is not allowed; "
            "new production is hold-only"
        )
    return default, "static-still default"


def plan_simple_zoom(
    holds: Sequence[Mapping[str, Any]],
    *,
    intents: Mapping[str, str] | None = None,
    output_width: int = OUTPUT_WIDTH,
    output_height: int = OUTPUT_HEIGHT,
) -> dict[str, Any]:
    """Build a ``simple-zoom-plan.v1`` document from hold timing.

    ``holds`` entries need ``hold_id/start/end``; optional ``motion``
    (explicit, must be ``hold``), ``narrative_function`` and ``emotion`` inform
    the rationale. ``intents`` is rejected in new production because there are
    no camera moves.
    """

    if not holds:
        raise SimpleZoomError("simple zoom plan requires at least one hold")
    if output_width <= 0 or output_height <= 0:
        raise SimpleZoomError("simple zoom plan output size must be positive")
    intents = intents or {}
    entries: list[dict[str, Any]] = []
    previous: str | None = None
    for index, hold in enumerate(holds):
        hold_id = hold.get("hold_id")
        if not isinstance(hold_id, str) or not hold_id:
            raise SimpleZoomError(f"hold {index} has no hold_id")
        try:
            start = float(hold["start"])
            end = float(hold["end"])
        except (KeyError, TypeError, ValueError):
            raise SimpleZoomError(f"hold {hold_id!r} needs numeric start/end") from None
        duration = end - start
        if duration <= 0:
            raise SimpleZoomError(f"hold {hold_id!r} has non-positive duration")
        intent = intents.get(hold_id)
        if intent is not None:
            raise SimpleZoomError(
                f"hold {hold_id!r} intent must not be provided; new production is hold-only"
            )
        motion, reason = _resolve_motion(hold, intent, default="hold")
        zoom = 0.0
        narrative = hold.get("narrative_function")
        emotion = hold.get("emotion")
        rationale = reason
        if emotion:
            rationale = f"{reason}; emotion={emotion}"
        if narrative:
            rationale = f"{rationale}; {narrative}"
        entries.append({
            "hold_id": hold_id,
            "motion": motion,
            "zoom": round(zoom, 4),
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(duration, 3),
            "rationale": rationale,
        })
        previous = motion
    return {
        "schema_version": "simple-zoom-plan.v1",
        "output_size": {"width": output_width, "height": output_height},
        "motions": entries,
    }


def validate_simple_zoom_plan(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a ``simple-zoom-plan.v1`` document (hold-only, static stills)."""

    if not isinstance(payload, Mapping):
        raise SimpleZoomError("simple zoom plan must be an object")
    if payload.get("schema_version") != "simple-zoom-plan.v1":
        raise SimpleZoomError("simple zoom plan schema_version is invalid")
    size = payload.get("output_size")
    if not isinstance(size, Mapping) or size.get("width") != OUTPUT_WIDTH or size.get("height") != OUTPUT_HEIGHT:
        raise SimpleZoomError(f"simple zoom plan output must be {OUTPUT_WIDTH}x{OUTPUT_HEIGHT}")
    motions = payload.get("motions")
    if not isinstance(motions, list) or not motions:
        raise SimpleZoomError("simple zoom plan motions must be a nonempty list")
    previous_end = 0.0
    seen: set[str] = set()
    for entry in motions:
        if not isinstance(entry, Mapping):
            raise SimpleZoomError("simple zoom plan motion must be an object")
        hold_id = entry.get("hold_id")
        if not isinstance(hold_id, str) or hold_id in seen:
            raise SimpleZoomError("simple zoom plan hold_id is invalid or duplicated")
        seen.add(hold_id)
        motion = entry.get("motion")
        if motion not in ALLOWED_MOTIONS:
            raise SimpleZoomError(f"motion {motion!r} is not in the new-production allowed set")
        zoom = float(entry.get("zoom", 0.0))
        if zoom < 0.0 or zoom > MAX_ZOOM:
            raise SimpleZoomError(f"zoom {zoom} exceeds the {MAX_ZOOM:.0%} cap")
        if motion == "hold" and zoom != 0.0:
            raise SimpleZoomError("a hold must have zero zoom")
        start = float(entry.get("start", 0.0))
        end = float(entry.get("end", 0.0))
        duration = float(entry.get("duration", 0.0))
        if end <= start or duration <= 0:
            raise SimpleZoomError(f"hold {hold_id!r} timing is invalid")
        if start + 1e-6 < previous_end:
            raise SimpleZoomError(f"hold {hold_id!r} overlaps or moves backward")
        previous_end = end
        if not isinstance(entry.get("rationale"), str):
            raise SimpleZoomError(f"hold {hold_id!r} has no rationale")
    return payload


def build_zoompan_filter(
    entry: Mapping[str, Any],
    *,
    frames: int,
    fps: int = 30,
) -> str:
    """Return an ffmpeg filter for a static still at 1920x1080.

    New production never emits ``zoompan``: the only accepted motion is
    ``hold``, rendered as plain scale/setsar. Any other motion is rejected.
    """

    motion = entry["motion"]
    zoom = float(entry.get("zoom", 0.0))
    if motion not in ALLOWED_MOTIONS:
        raise SimpleZoomError(f"cannot render motion {motion!r}")
    if frames < 1:
        raise SimpleZoomError("static render requires at least one frame")
    if motion == "hold":
        return f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT},setsar=1"
    raise SimpleZoomError("new production is hold-only; zoompan is never emitted")
