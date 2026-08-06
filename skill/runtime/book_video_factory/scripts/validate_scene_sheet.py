#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path
import _bootstrap  # noqa: F401
from book_video_factory.production_visuals import SheetValidationError, validate_scene_sheet

def main() -> int:
    parser=argparse.ArgumentParser(description="Validate one exact 2x2 scene sheet before HBG splitting")
    parser.add_argument("--project",type=Path,required=True);parser.add_argument("--sheet-id",required=True);parser.add_argument("--source",type=Path,required=True);parser.add_argument("--orientation",choices=("landscape","portrait"),default="landscape");args=parser.parse_args()
    try:
        mapping=json.loads((args.project.expanduser().resolve()/"05_director/SHEET_MAP.json").read_text(encoding="utf-8"))
        groups=[item for item in mapping.get("sheet_groups",[]) if isinstance(item,dict) and item.get("sheet_id")==args.sheet_id]
        if len(groups)!=1: raise SheetValidationError("sheet_id is not a unique planned 2x2 group")
        order=groups[0].get("task_ids")
        result=validate_scene_sheet(args.source,orientation=args.orientation,panel_order=order,expected_panel_order=order)
    except (SheetValidationError,OSError,ValueError,json.JSONDecodeError) as error:
        print(json.dumps({"status":"failed","error":str(error)},ensure_ascii=False));return 2
    print(json.dumps({"status":"pass","source":str(result.source),"sha256":result.sha256,"width":result.width,"height":result.height,"orientation":result.orientation,"panel_order":result.panel_order,"panel_boxes":result.panel_boxes},ensure_ascii=False));return 0
if __name__=="__main__":raise SystemExit(main())
