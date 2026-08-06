#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path
import _bootstrap  # noqa: F401
from book_video_factory.director_stage import DirectorStageConflict, DirectorStageError, compile_director_stage

def main() -> int:
    parser=argparse.ArgumentParser(description="Compile Phase 4 real audio into Phase 5 director timeline and production image tasks")
    parser.add_argument("--project",type=Path,required=True); args=parser.parse_args()
    try: result=compile_director_stage(args.project)
    except (DirectorStageError,DirectorStageConflict,OSError,ValueError) as error:
        print(json.dumps({"status":"failed","error":str(error)},ensure_ascii=False)); return 2
    print(json.dumps({"status":result.status,"manifest_path":str(result.manifest_path),"stage_manifest_path":str(result.stage_manifest_path),"next_stage_status":result.next_stage_status},ensure_ascii=False)); return 0
if __name__=="__main__": raise SystemExit(main())
