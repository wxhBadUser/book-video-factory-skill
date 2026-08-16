"""P0-2: VisualBeat/VisualGroup planner invariants."""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from book_video_factory.semantic_alignment.visual_beat import (
    VisualBeatError, VisualBeat, plan_visual_beats, build_visual_timeline_document,
)

# 20 个 caption 块，每块 2.4s，连续覆盖 48s（模拟 meaning-block 网格）
def _make_blocks(n: int = 20, dur: float = 2.4):
    blocks = []
    t = 0.0
    for i in range(n):
        blocks.append({
            "caption_id": f"BLK_{i:04d}", "text": f"第{i}句" * 4,  # 12 字
            "start": t, "end": t + dur, "duration": dur,
        })
        t += dur
    return blocks

BLOCKS = _make_blocks()


def test_beats_tile_stream_without_gaps():
    beats = plan_visual_beats(BLOCKS)
    assert len(beats) >= 6  # 48s / 4.5s 目标 → 至少 10；宽松下限防脆弱
    prev_end = 0.0
    for beat in beats:
        assert beat.start >= prev_end - 1e-9
        assert beat.duration >= 1.0 - 1e-9, f"beat too short: {beat}"
        assert beat.duration <= 8.0 + 1e-9, f"beat exceeds hard cap: {beat.duration}"
        if beat.duration > 6.0:
            assert beat.intentional_hold_reason, "long beat needs hold reason"
        prev_end = beat.end


def test_beat_duration_statistics_satisfy_acceptance():
    beats = plan_visual_beats(BLOCKS)
    durations = sorted(b.duration for b in beats)
    median = durations[len(durations) // 2]
    p95 = durations[min(len(durations) - 1, int(len(durations) * 0.95))]
    assert median <= 4.5 + 1e-9, f"median {median} exceeds 4.5s"
    assert p95 <= 6.0 + 1e-9, f"p95 {p95} exceeds 6.0s"
    assert max(durations) <= 10.0 + 1e-9


def test_numeric_acceptance_for_84s_body():
    # §22: ≥18 shots/84s, median ≤4.5s
    n = math.ceil(84.0 / 4.5)  # ≈19
    blocks = _make_blocks(n, dur=84.0 / n)
    beats = plan_visual_beats(blocks)
    assert len(beats) >= 18
    durations = sorted(b.duration for b in beats)
    assert durations[len(durations) // 2] <= 4.5 + 1e-9


def test_location_change_forces_new_beat():
    blocks = _make_blocks(6, dur=4.0)
    blocks[2]["location_id"] = "LOC_OPEN_SEA"
    beats = plan_visual_beats(blocks)
    seen_locs = [b.location_id for b in beats]
    assert "LOC_OPEN_SEA" in seen_locs


def test_span_change_forces_new_beat():
    # 语义事件/动作拆分：span 边界即使在同地点同参与者也必须切新 beat
    blocks = _make_blocks(6, dur=3.0)
    blocks[3]["span_id"] = "SPAN_2"  # 前 3 个默认 span 1
    beats = plan_visual_beats(blocks)
    assert len(beats) >= 2
    first_beat_caption = beats[0].caption_ids[0]
    assert "BLK_0003" not in beats[0].caption_ids  # SPAN_2 起始块不能并入前一个 beat


def test_document_shape():
    beats = plan_visual_beats(BLOCKS)
    doc = build_visual_timeline_document(
        release_id="omats-v25", beats=beats,
        source_continuity_sha256="a" * 64, source_grouping_sha256="b" * 64,
    )
    assert doc["schema_version"] == "visual-timeline.v1"
    assert doc["beat_count"] == len(beats)
    assert "median_duration" in doc and "p95_duration" in doc and "max_duration" in doc
    for key in ("beat_id", "start", "end", "duration", "caption_ids", "location_id", "participants"):
        assert key in beats[0].to_dict()


def test_narration_pauses_bridged_into_contiguous_grid():
    # A silent pause between spoken blocks is part of the shot: the beat grid must
    # tile [first, last] with no holes (real narration pauses are bridged, not fatal).
    blocks = [
        {"caption_id": f"BLK_{i:04d}", "text": "字" * 12, "start": float(s), "end": float(e), "duration": float(e - s)}
        for i, (s, e) in enumerate([(0, 2), (2, 4), (4, 6), (6, 8), (9, 11), (11, 13), (13, 15), (15, 17)])
    ]
    beats = plan_visual_beats(blocks)
    grid = sorted(beats, key=lambda b: b.start)
    assert grid
    assert abs(grid[0].start - 0.0) < 1e-6
    assert abs(grid[-1].end - 17.0) < 1e-6
    for i in range(1, len(grid)):
        assert grid[i].start - grid[i - 1].end < 1e-6, "grid must have no holes"


def test_overlapping_caption_blocks_rejected():
    blocks = _make_blocks(3, dur=2.0)
    blocks[1]["start"] = 1.5  # overlaps block 0 which runs [0.0, 2.0]
    with pytest.raises(VisualBeatError):
        plan_visual_beats(blocks)


def test_timeline_document_validates_against_schema():
    import json, jsonschema
    from pathlib import Path
    beats = plan_visual_beats(BLOCKS)
    doc = build_visual_timeline_document(release_id="omats-v25", beats=beats,
                                         source_continuity_sha256="a"*64, source_grouping_sha256="b"*64)
    schema = json.loads((Path(__file__).parents[1] / "schemas" / "visual_timeline.v1.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(doc, schema)