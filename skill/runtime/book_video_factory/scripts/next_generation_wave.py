#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path
import _bootstrap  # noqa: F401
from book_video_factory.production_visuals import GenerationScheduleError, next_generation_wave

def main() -> int:
    parser=argparse.ArgumentParser(description="Return the next bounded production image generation wave")
    parser.add_argument("--project",type=Path,required=True); parser.add_argument("--limit",type=int); args=parser.parse_args()
    try: result=next_generation_wave(args.project,limit=args.limit)
    except (GenerationScheduleError,OSError,ValueError) as error:
        print(json.dumps({"status":"failed","error":str(error)},ensure_ascii=False)); return 2
    print(json.dumps({"status":"ready" if result.jobs else "settled","concurrency":result.concurrency,"remaining_count":result.remaining_count,"jobs":result.jobs},ensure_ascii=False)); return 0
if __name__=="__main__": raise SystemExit(main())
