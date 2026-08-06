#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path
import _bootstrap  # noqa: F401
from book_video_factory.production_visuals import SceneReviewError, build_scene_asset_review

def main() -> int:
    p=argparse.ArgumentParser(description="Build HBG contact sheet and validate per-scene semantic/reality/identity review")
    p.add_argument("--project",type=Path,required=True); p.add_argument("--decision",type=Path,required=True); a=p.parse_args()
    try: r=build_scene_asset_review(a.project,a.decision)
    except (SceneReviewError,OSError,ValueError) as error:
        print(json.dumps({"status":"failed","error":str(error)},ensure_ascii=False)); return 2
    print(json.dumps({"status":r.status,"report_path":str(r.report_path),"contact_sheet_path":str(r.contact_sheet_path),"next_stage_status":r.next_stage_status},ensure_ascii=False)); return 0
if __name__=="__main__": raise SystemExit(main())
