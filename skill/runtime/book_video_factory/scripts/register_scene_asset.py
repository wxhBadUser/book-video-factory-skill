#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path
import _bootstrap  # noqa: F401
from book_video_factory.production_visuals import SceneAssetError, register_scene_asset

def main() -> int:
    p=argparse.ArgumentParser(description="Register one real Host ImageGen production scene PNG")
    p.add_argument("--project",type=Path,required=True); p.add_argument("--task-id",required=True); p.add_argument("--source",type=Path,required=True); p.add_argument("--tool-call-id",required=True); p.add_argument("--provider",choices=("host-imagegen","gemini-web","flow-web"),default="host-imagegen")
    p.add_argument("--style-reference-id",action="append",default=[]); p.add_argument("--identity-reference-task-id",action="append",default=[])
    p.add_argument("--reference-input",action="append",default=[],help="Repeatable role:path of a concrete reference image actually consumed by generation")
    p.add_argument("--generation-attempt-id"); p.add_argument("--generation-mode"); p.add_argument("--provider-receipt",help="Actual provider/host call receipt for the reference-conditioned generation"); a=p.parse_args()
    try: r=register_scene_asset(a.project,task_id=a.task_id,source=a.source,tool_call_id=a.tool_call_id,provider=a.provider,style_reference_ids=a.style_reference_id,identity_reference_task_ids=a.identity_reference_task_id,reference_inputs=a.reference_input,generation_attempt_id=a.generation_attempt_id,generation_mode=a.generation_mode,provider_receipt=a.provider_receipt)
    except (SceneAssetError,OSError,ValueError) as error:
        print(json.dumps({"status":"failed","error":str(error)},ensure_ascii=False)); return 2
    print(json.dumps({"status":r.status,"task_id":r.task_id,"asset_path":str(r.asset_path),"registered_count":r.registered_count,"task_count":r.total_count,"next_stage_status":r.next_stage_status},ensure_ascii=False)); return 0
if __name__=="__main__": raise SystemExit(main())
