#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path
import _bootstrap  # noqa: F401
from book_video_factory.production_visuals import SceneReviewError, approve_scene_assets

def main() -> int:
    p=argparse.ArgumentParser(description="Record explicit human approval of all production scene images")
    p.add_argument("--project",type=Path,required=True); p.add_argument("--reviewer",required=True); p.add_argument("--note",required=True); a=p.parse_args()
    try: path=approve_scene_assets(a.project,reviewer=a.reviewer,note=a.note)
    except (SceneReviewError,OSError,ValueError) as error:
        print(json.dumps({"status":"failed","error":str(error)},ensure_ascii=False)); return 2
    print(json.dumps({"status":"approved","approval_path":str(path),"next_stage_status":"ready_for_render"},ensure_ascii=False)); return 0
if __name__=="__main__": raise SystemExit(main())
