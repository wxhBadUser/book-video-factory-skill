"""P0-2 CLI: build VISUAL_TIMELINE.json from the real chain outputs.

Joins CAPTION_BINDINGS.json (per-block start/end on the master timeline)
with SCENE_CONTINUITY_SPANS.json (semantic-event membership, location,
participants) so each VisualBeat carries event + spatial context. The real
loader re-validates the whole grouping->continuity chain (stale => error).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from book_video_factory.semantic_alignment.scene_continuity import (  # noqa: E402
    build_span_review_groups, load_current_scene_continuity_document,
)
from book_video_factory.semantic_alignment.visual_beat import (  # noqa: E402
    build_visual_timeline_document, plan_visual_beats,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Build VISUAL_TIMELINE.json")
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--release-id", required=True)
    args = parser.parse_args()

    root = args.project.resolve()
    continuity = load_current_scene_continuity_document(root)  # 重新校验 grouping+contract 链
    bindings = json.loads((root / "04_audio" / "CAPTION_BINDINGS.json").read_text(encoding="utf-8"))
    raw_captions = bindings.get("captions")
    captions_by_id: dict[str, dict] = raw_captions if isinstance(raw_captions, dict) else {
        str(item["caption_id"]): item for item in raw_captions or []
    }

    # caption_id -> span membership (span_id / location / participants / visual_core)
    # 用 build_span_review_groups 拿每个 span 解引用后的 caption_ids（hash-bound）
    grouping = json.loads((root / "04_audio" / "CAPTION_GROUPING_AUDIT.json").read_text(encoding="utf-8"))
    membership: dict[str, dict] = {}
    for span_id, payload in build_span_review_groups(continuity=continuity, grouping=grouping).items():
        for cid in payload["caption_ids"]:
            membership[cid] = {
                "span_id": payload["span_id"],
                "location_id": payload["location"],
                "participants": payload["characters"],
                "visual_core": payload["representative_visual_core"],
            }

    rows: list[dict] = []
    for cid, caption in captions_by_id.items():
        start = float(caption["start"])
        end = float(caption["end"])
        mem = membership.get(cid, {})
        rows.append({
            "caption_id": cid,
            "text": str(caption.get("text", "")),
            "start": start,
            "end": end,
            "duration": round(end - start, 3),
            "span_id": mem.get("span_id", ""),
            "location_id": mem.get("location_id", ""),
            "participants": mem.get("participants", []),
            "visual_core": mem.get("visual_core", ""),
        })
    uncovered = [row["caption_id"] for row in rows if not row["span_id"]]
    if uncovered:
        raise SystemExit(f"[fail] {len(uncovered)} captions outside any span: {uncovered[:5]}...")
    beats = plan_visual_beats(rows)
    doc = build_visual_timeline_document(
        release_id=args.release_id,
        beats=beats,
        source_continuity_sha256=_sha256(root / "04_audio" / "SCENE_CONTINUITY_SPANS.json"),
        source_grouping_sha256=continuity.get("source_grouping_sha256", ""),
    )
    out = root / "04_audio" / "VISUAL_TIMELINE.json"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"beat_count": doc["beat_count"], "median": doc["median_duration"],
                      "p95": doc["p95_duration"], "max": doc["max_duration"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())