#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path
import _bootstrap  # noqa: F401
from book_video_factory.production_visuals import GenerationScheduleError, finalize_generation_wave

def main() -> int:
    parser=argparse.ArgumentParser(description="Settle every job in the current production image wave")
    parser.add_argument("--project",type=Path,required=True); parser.add_argument("--results",type=Path,required=True); args=parser.parse_args()
    try:
        results=json.loads(args.results.read_text(encoding="utf-8"))
        result=finalize_generation_wave(args.project,results)
    except (GenerationScheduleError,OSError,ValueError,json.JSONDecodeError) as error:
        print(json.dumps({"status":"failed","error":str(error)},ensure_ascii=False)); return 2
    print(json.dumps({"status":"settled","settled_job_ids":result.settled_job_ids,"completed_count":result.completed_count,"failed_count":result.failed_count,"next_ready_count":result.next_ready_count},ensure_ascii=False)); return 0
if __name__=="__main__": raise SystemExit(main())
