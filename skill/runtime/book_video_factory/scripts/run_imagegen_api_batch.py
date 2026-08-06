#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path
import _bootstrap  # noqa: F401
from book_video_factory.imagegen_api_batch import ImageGenBatchError, run_imagegen_api_batch

def main() -> int:
    parser=argparse.ArgumentParser(description="Delegate an optional batch to the installed system ImageGen CLI")
    parser.add_argument("--input",type=Path,required=True); parser.add_argument("--out-dir",type=Path,required=True); parser.add_argument("--concurrency",type=int,default=5); args=parser.parse_args()
    try: result=run_imagegen_api_batch(args.input,args.out_dir,concurrency=args.concurrency)
    except (ImageGenBatchError,OSError,ValueError) as error:
        print(json.dumps({"status":"failed","error":str(error)},ensure_ascii=False)); return 2
    print(json.dumps({"status":"complete","returncode":result.returncode,"stdout":result.stdout,"stderr":result.stderr},ensure_ascii=False)); return 0
if __name__=="__main__": raise SystemExit(main())
