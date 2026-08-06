#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path
import _bootstrap  # noqa: F401
from book_video_factory.production_visuals import AssetNormalizationError, StagingResolutionError, normalize_scene_asset, resolve_staged_asset

def main() -> int:
    parser=argparse.ArgumentParser(description="Resolve one staged provider output and normalize it to the exact scene canvas")
    parser.add_argument("--project",type=Path,required=True);parser.add_argument("--expected-output-target",required=True);parser.add_argument("--output-target",required=True);parser.add_argument("--orientation",choices=("landscape","portrait"),default="landscape");parser.add_argument("--strategy",choices=("letterbox","crop"),default="letterbox");parser.add_argument("--asset-tier",choices=("standard","hero","identity"),default="standard");args=parser.parse_args()
    try:
        resolved=resolve_staged_asset(args.project,args.expected_output_target)
        result=normalize_scene_asset(args.project,resolved.path,args.output_target,orientation=args.orientation,strategy=args.strategy,asset_tier=args.asset_tier)
    except (StagingResolutionError,AssetNormalizationError,OSError,ValueError) as error:
        print(json.dumps({"status":"failed","error":str(error)},ensure_ascii=False));return 2
    print(json.dumps({"status":"created","source":str(result.source_path),"output":str(result.output_path),"source_sha256":result.source_sha256,"final_sha256":result.final_sha256,"delivered_size":result.delivered_size,"final_size":result.final_size,"orientation":result.orientation,"strategy":result.strategy,"asset_tier":result.asset_tier},ensure_ascii=False));return 0
if __name__=="__main__":raise SystemExit(main())
