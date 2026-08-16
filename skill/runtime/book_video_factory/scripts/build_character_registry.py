"""P0-4 CLI: emit CHARACTER_REGISTRY.json for a project.

Reads 03_images_生成图片/VISUAL_FOUNDATION_INPUT.json (agent-authored,
already the source of CHARACTER_IDENTITY_MANIFEST) and materializes the
character section as a typed registry. Fallback: parse CHARACTERS.md headings
(# C001｜名字) into stub entries (identity anchors to be bound later).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from book_video_factory.visual_foundation.character_registry import (  # noqa: E402
    CharacterRegistryError, parse_character_registry,
)


def _from_foundation_input(path: Path) -> list[dict]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    for key in ("characters", "character_identities", "identity"):
        if isinstance(doc.get(key), list) and doc[key]:
            return doc[key]
    raise CharacterRegistryError(f"no character list in {path.name}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--source", default="foundation", choices=["foundation", "characters-md"])
    args = parser.parse_args()

    root = args.project.resolve()
    if args.source == "foundation":
        path = root / "03_images_生成图片" / "VISUAL_FOUNDATION_INPUT.json"
        raw = _from_foundation_input(path)
    else:
        text = (root / "CHARACTERS.md").read_text(encoding="utf-8")
        raw = [
            {
                "character_id": m.group(1).strip(),
                "name": m.group(2).strip(),
                "narrative_role": "", "life_stage": "",
                "immutable_traits": [], "allowed_changes": [], "wardrobe_states": [],
                "identity_anchor_task_ids": [], "identity_board_task_id": "",
            }
            for m in re.finditer(r"^##\s+(C\d{3})｜(.+)$", text, re.M)
        ]
        if not raw:
            raise CharacterRegistryError("CHARACTERS.md has no '# C001｜名字' headings")
    registry = parse_character_registry(raw)
    doc = registry.to_document(release_id=args.release_id)
    out = root / "03_images_生成图片" / "CHARACTER_REGISTRY.json"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"characters": doc["character_count"],
                      "ids": list(registry.ids())}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
