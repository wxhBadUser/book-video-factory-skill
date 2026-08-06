#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path
import _bootstrap  # noqa: F401
from book_video_factory.production_visuals import GenerationScheduleError, plan_generation_run

def main() -> int:
    parser=argparse.ArgumentParser(description="Plan the production image generation DAG")
    parser.add_argument("--project",type=Path,required=True); parser.add_argument("--concurrency",type=int,default=5); args=parser.parse_args()
    try: result=plan_generation_run(args.project,concurrency=args.concurrency)
    except (GenerationScheduleError,OSError,ValueError) as error:
        print(json.dumps({"status":"failed","error":str(error)},ensure_ascii=False)); return 2
    print(json.dumps({"status":result.status,"concurrency":result.concurrency,"plan":str(result.plan_path),"jobs":str(result.jobs_path),"run_manifest":str(result.run_manifest_path)},ensure_ascii=False)); return 0
if __name__=="__main__": raise SystemExit(main())
