"""P0-2: turn meaning-block captions into VisualBeat / VisualGroup shots.

A VisualGroup is a contiguous run of caption meaning-blocks sharing one
spatial/action proposition (same location + participants + event state). A
VisualBeat is the minimal visual unit a shot can hold. The emitted
VISUAL_TIMELINE.json is the authoritative shot grid: median <=4.5s, P95 <=6s,
beats above 6s carry an intentional_hold_reason, >10s is an error (fail
closed). The planner packs runs tight against target_seconds and splits on
semantic-event (span) / location / participant changes.
The director stage consumes this grid so one VisualBeat == one scene image.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import median
from typing import Any, Mapping, Sequence

TARGET_BEAT_SECONDS = 4.5
HOLD_LIMIT_SECONDS = 6.0   # beats above the P95 design ceiling need a reason
HARD_LIMIT_SECONDS = 10.0


class VisualBeatError(ValueError):
    """Raised on any visual-beat construction failure (fail closed)."""


@dataclass(frozen=True)
class VisualBeat:
    beat_id: str
    start: float
    end: float
    duration: float
    caption_ids: tuple[str, ...]
    caption_texts: tuple[str, ...]
    visual_proposition: str
    location_id: str
    participants: tuple[str, ...]
    motion: str = "hold"
    intentional_hold_reason: str = ""
    source_span_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # Emit JSON-native shapes: tuples -> lists. source_span_id is planner
        # bookkeeping; the closed visual-timeline.v1 schema
        # (additionalProperties: false) does not carry it in the doc.
        data["caption_ids"] = list(data["caption_ids"])
        data["caption_texts"] = list(data["caption_texts"])
        data["participants"] = list(data["participants"])
        data.pop("source_span_id", None)
        return data


def _norm_seconds(value: Any) -> float:
    return round(float(value), 3)


def plan_visual_beats(
    captions: Sequence[Mapping[str, Any]],
    *,
    target_seconds: float = TARGET_BEAT_SECONDS,
    hold_seconds: float = HOLD_LIMIT_SECONDS,
    hard_seconds: float = HARD_LIMIT_SECONDS,
    beat_prefix: str = "VB",
) -> list[VisualBeat]:
    if not captions:
        raise VisualBeatError("no caption blocks to plan")
    ordered = sorted(
        ({**dict(c), "start": _norm_seconds(c["start"]), "end": _norm_seconds(c["end"])} for c in captions),
        key=lambda c: (c["start"], c["end"]),
    )
    # fail closed on overlapping caption blocks (a shot grid cannot tile overlaps)
    for index in range(1, len(ordered)):
        if ordered[index]["start"] < ordered[index - 1]["end"] - 1e-6:
            raise VisualBeatError("overlapping caption blocks")
    # Absorb real narration pauses: the shot grid must tile the body contiguously
    # (a shot covers the silence between spoken sentences too). A caption's shot
    # therefore extends to the next caption's start; only genuine overlaps (checked
    # above, on raw ends) are fatal.
    for index in range(len(ordered) - 1):
        ordered[index]["end"] = max(ordered[index]["end"], ordered[index + 1]["start"])

    beats: list[VisualBeat] = []
    run: list[dict[str, Any]] = []

    def _close(reason: str) -> None:
        nonlocal run
        if not run:
            return
        start = run[0]["start"]
        end = run[-1]["end"]
        duration = end - start
        if duration > hard_seconds + 1e-9:
            raise VisualBeatError(
                f"beat would exceed hard {hard_seconds}s even at minimum granularity: "
                f"{run[0]['caption_id']}..{run[-1]['caption_id']} {duration:.2f}s"
            )
        location = next((c.get("location_id") or "" for c in run if c.get("location_id")), "")
        participants = tuple(
            sorted({p for c in run for p in (c.get("participants") or ())})
        )
        visual_core = next((c.get("visual_core") for c in run if c.get("visual_core")), "")
        beats.append(
            VisualBeat(
                beat_id=f"{beat_prefix}_{len(beats) + 1:03d}",
                start=start,
                end=end,
                duration=round(duration, 3),
                caption_ids=tuple(c["caption_id"] for c in run),
                caption_texts=tuple(c["text"] for c in run),
                visual_proposition=visual_core or reason,
                location_id=location,
                participants=participants,
                intentional_hold_reason=(
                    f"narrative hold: {len(run)} captions, {duration:.1f}s" if duration > hold_seconds else ""
                ),
            )
        )
        run = []

    for caption in ordered:
        if run:
            probe = caption["end"] - run[0]["start"]
            loc_changed = caption.get("location_id") and run[0].get("location_id") and caption["location_id"] != run[0]["location_id"]
            participants_changed = set(caption.get("participants") or ()) != set(run[0].get("participants") or ())
            span_changed = caption.get("span_id") and run[0].get("span_id") and caption["span_id"] != run[0]["span_id"]
            if loc_changed or participants_changed or span_changed:
                _close("semantic_event_change")
            elif probe > target_seconds:
                # 下一块会让 run 从起点超 target，就在当前块边界关闭（紧贴 target 打包）
                _close("target_reached")
        run.append(caption)
    _close("stream_end")

    durations = sorted(b.duration for b in beats)
    if durations and durations[len(durations) // 2] > 4.5 + 1e-9:
        raise VisualBeatError(f"median beat duration {durations[len(durations) // 2]:.2f}s exceeds 4.5s")
    return beats


def build_visual_timeline_document(
    *,
    release_id: str,
    beats: Sequence[VisualBeat | Mapping[str, Any]],
    source_continuity_sha256: str,
    source_grouping_sha256: str = "",
) -> dict[str, Any]:
    normalized = [b if isinstance(b, VisualBeat) else VisualBeat(**{k: tuple(v) if isinstance(v, list) else v for k, v in dict(b).items()}) for b in beats]
    durations = sorted(b.duration for b in normalized)
    return {
        "schema_version": "visual-timeline.v1",
        "release_id": release_id,
        "source_continuity_sha256": source_continuity_sha256,
        "source_grouping_sha256": source_grouping_sha256,
        "beat_count": len(normalized),
        "median_duration": round(median(durations), 3),
        "p95_duration": round(durations[min(len(durations) - 1, int(len(durations) * 0.95))], 3),
        "max_duration": round(max(durations), 3),
        "holds": sum(1 for b in normalized if b.intentional_hold_reason),
        "beats": [b.to_dict() for b in normalized],
    }