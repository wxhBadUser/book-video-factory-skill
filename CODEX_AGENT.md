# Codex production agent

## User-facing goal

When the user asks, for example, `制作《老人与海》名著视频`, create or resume one project and advance it through the only legal next stage. Do not generate a second workflow or rewrite HBG.

## Start every run

```bash
python book_video_factory/scripts/doctor.py --profile production --json
python book_video_factory/scripts/run_full_pipeline.py verify-repository --root .
python book_video_factory/scripts/run_full_pipeline.py status --project <PROJECT_DIR>
```

The status command is authoritative for the next action. Re-run it after every successful stage.

## Human interactions

Ask for user approval only at these gates:

1. Locked script and fact audit.
2. Character/scene/object anchors and the 12-image LookDev contact sheet.
3. Full production-scene contact sheet after semantic, reality, and identity review.
4. Encoded final master before publication.

Show the relevant files/contact sheet. Do not convert silence from the user into approval.

## Host ImageGen execution

Read each JSON object in the task queue. Use the exact `prompt`, declared style references, and declared identity-reference assets. After a real tool call, register the exact output file and actual tool call identifier. Never place a reference overview image into a production output path.

Phase 3 queue:

```text
03_images_生成图片/ANCHOR_TASKS.jsonl
03_images_生成图片/LOOKDEV_TASKS.jsonl
```

Phase 6 queue:

```text
05_director/IMAGE_TASKS.jsonl
```

For 2×2 tasks, generate one native 2×2 sheet only for the task IDs in `SHEET_MAP.json`, then run `python book_video_factory/scripts/split_scene_sheet.py --project <PROJECT> --sheet-id <SHEET_ID> --source <REAL_SHEET.png>`. Register each exact split PNG. The wrapper calls the vendored HBG `split_2x2.sh`; do not crop in custom Python. High-risk and single tasks must be generated individually.

### Host ImageGen wave protocol

Plan Phase 6 first, then read `06_visual_production/GENERATION_RUN_MANIFEST.json` and run `next_generation_wave.py`. A wave returns `最多 5` jobs. Invoke only independent jobs in parallel; DAG dependencies and shared identity keys remain serialized.

For every result, `显示给用户`, preserve the original file in project `staging`, and record the real `tool call ID` plus file `SHA-256` before registration. One failed job does not roll back successful jobs. Do not request the next wave until the current wave is `全部完成或明确失败`. The Python runtime `不实现私有图片 API`.

## External-dependency behavior

- Missing `edge-tts` or network access: stop at Phase 4 and report the exact Doctor failure.
- Missing Host ImageGen capability: stop with the task queue ready; never fabricate images.
- Missing HyperFrames before the opening preview exists: stop and report the exact dependency failure. The streaming renderer requires the HBG-generated, HBG-validated preview manifest.
- Missing FFmpeg/FFprobe or insufficient disk: stop before rendering.

## Opening preview before streaming render

When `run_full_pipeline.py status` returns `awaiting_opening_preview`, run the exact `run_render_stage.py preview` command it provides. This command reuses the vendored HBG composition builder, pinned HyperFrames long-render wrapper, and HBG style validator; it does not use a custom preview renderer. Do not place an unrelated MP4 at the configured preview path.

## Final success condition

A project is complete only when:

```text
08_render_合成/final/FINAL_RENDER_MANIFEST.json
09_qc/FINAL_QA_REPORT.json
08_render_合成/final/<revision>.mp4
10_delivery_交付/FINAL_MASTER_APPROVAL.json
```

all exist, their hashes agree, the encoded master has explicit human approval, and `run_full_pipeline.py status` returns `complete`.
