#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,subprocess,sys
from pathlib import Path
import _bootstrap  # noqa: F401
from book_video_factory.pipeline_runtime import pipeline_status

def main()->int:
    p=argparse.ArgumentParser(description="Derive the only legal next action for the Phase 0-7 book-video pipeline")
    sub=p.add_subparsers(dest="command",required=True)
    for name in ("status","next"):
        q=sub.add_parser(name); q.add_argument("--project",type=Path,required=True)
    verify=sub.add_parser("verify-repository"); verify.add_argument("--root",type=Path,default=Path.cwd())
    a=p.parse_args()
    if a.command in {"status","next"}:
        try: payload=pipeline_status(a.project)
        except Exception as error:
            print(json.dumps({"status":"failed","error":str(error)},ensure_ascii=False)); return 2
        print(json.dumps(payload,ensure_ascii=False,indent=2)); return 0
    root=a.root.expanduser().resolve(); scripts=[f"verify_phase{i}_{name}.py" for i,name in []]
    commands=[
        [sys.executable,str(root/"scripts/verify_phase0_architecture.py"),"--root",str(root)],
        [sys.executable,str(root/"scripts/verify_phase1_content_brain.py"),"--root",str(root)],
        [sys.executable,str(root/"scripts/verify_phase2_hbg_bridge.py"),"--root",str(root)],
        [sys.executable,str(root/"scripts/verify_phase3_visual_stage.py"),"--root",str(root)],
        [sys.executable,str(root/"scripts/verify_phase4_audio_stage.py"),"--root",str(root)],
        [sys.executable,str(root/"scripts/verify_phase5_director_stage.py"),"--root",str(root)],
        [sys.executable,str(root/"scripts/verify_phase6_scene_visuals.py"),"--root",str(root)],
        [sys.executable,str(root/"scripts/verify_phase7_render_stage.py"),"--root",str(root)],
        [sys.executable,str(root/"scripts/verify_vfinal_architecture.py"),"--root",str(root)],
    ]
    reports=[]
    for command in commands:
        completed=subprocess.run(command,capture_output=True,text=True)
        try: report=json.loads(completed.stdout)
        except json.JSONDecodeError: report={"status":"fail","output":completed.stdout,"error":completed.stderr}
        reports.append(report)
        if completed.returncode!=0:
            print(json.dumps({"status":"failed","reports":reports},ensure_ascii=False,indent=2)); return 2
    print(json.dumps({"status":"pass","reports":reports},ensure_ascii=False,indent=2)); return 0
if __name__=="__main__": raise SystemExit(main())
