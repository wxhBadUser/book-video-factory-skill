# Full Phase 0–7 production pipeline

All commands are run from the repository root. Replace `<PROJECT>` with the project directory.

## 0. Install and verify

```bash
python -m pip install -e "book_video_factory[production]"
python book_video_factory/scripts/doctor.py --profile production --json
python book_video_factory/scripts/run_full_pipeline.py verify-repository --root .
```

## 1. Research, write, and lock the script

Use the existing Level-A source, research, route, anchor, event-card, three-script, blind-review, originality, and content-package contracts. Build the package with `build_content_package.py`, then record the explicit `script` approval.

## 2. Compile the HBG project

```bash
python book_video_factory/scripts/build_hbg_bridge.py \
  --project <PROJECT> \
  --bridge-input <PROJECT>/02_story_script_故事脚本/HBG_BRIDGE_INPUT.json
```

## 3. Build and approve the visual bible

```bash
python book_video_factory/scripts/build_visual_stage.py \
  --project <PROJECT> \
  --visual-input <PROJECT>/03_images_生成图片/VISUAL_STAGE_INPUT.json
```

Generate and register every anchor and LookDev PNG using the exact task records. Then:

```bash
python book_video_factory/scripts/build_visual_review.py --project <PROJECT>
python book_video_factory/scripts/approve_visual_stage.py \
  --project <PROJECT> --release-id r1 --reviewer '<HUMAN>' \
  --decision-file <PROJECT>/03_images_生成图片/VISUAL_REVIEW_DECISION.json
```

## 4. Generate continuous narration and the real timeline

```bash
python book_video_factory/scripts/run_audio_stage.py generate \
  --project <PROJECT> \
  --input <PROJECT>/04_audio/AUDIO_STAGE_INPUT.json \
  --lexicon <PROJECT>/04_audio/PRONUNCIATION_LEXICON.json
```

Author `04_audio/STORYBOARD_AUDIO_PLAN.json` from the real display captions and timing, then:

```bash
python book_video_factory/scripts/run_audio_stage.py finalize \
  --project <PROJECT> \
  --plan <PROJECT>/04_audio/STORYBOARD_AUDIO_PLAN.json
```

## 5. Compile the director timeline and production image tasks

```bash
python book_video_factory/scripts/build_director_stage.py --project <PROJECT>
```

Outputs:

```text
05_director/DIRECTOR_TIMELINE.json
05_director/MOTION_MANIFEST.json
05_director/IMAGE_TASKS.jsonl
05_director/PROMPTS.md
05_director/SHEET_MAP.json
```

Timing comes exclusively from Phase 4 audio/VTT. High-risk tasks are forced to single-image generation. All prompts reuse the Phase 3 visual profile and approved identity anchors.

## 6. Generate, register, review, and approve every scene image

First plan and inspect the bounded Host ImageGen wave:

```bash
python book_video_factory/scripts/plan_generation_run.py --project <PROJECT> --concurrency 5
python book_video_factory/scripts/next_generation_wave.py --project <PROJECT>
```

`06_visual_production/GENERATION_RUN_MANIFEST.json` is the current run projection. Each wave returns `最多 5` independent jobs. Run only dependency-safe jobs in parallel. Every returned image must be `显示给用户`, saved unchanged in project `staging`, and bound to its real `tool call ID` and `SHA-256`. A failed job does not invalidate successful siblings. Call `finalize_generation_wave.py` with every result, and request another wave only when the current wave is `全部完成或明确失败`. Runtime Python `不实现私有图片 API`.

For each real Host ImageGen result:

```bash
python book_video_factory/scripts/register_scene_asset.py \
  --project <PROJECT> --task-id <TASK_ID> --source <REAL_PNG> \
  --tool-call-id <REAL_CALL_ID> \
  --style-reference-id <REFERENCE_ID> \
  --identity-reference-task-id <IDENTITY_TASK_ID>
```

Repeat reference options in the exact order declared by the task. For a planned 2×2 group, generate the sheet and run:

```bash
python book_video_factory/scripts/split_scene_sheet.py \
  --project <PROJECT> --sheet-id <SHEET_ID> --source <REAL_SHEET.png>
```

Then register all four split PNGs. High-risk tasks remain individual. After all tasks are registered, create a decision JSON from `SCENE_REVIEW_DECISION.example.json`; every scene requires semantic, reality, and identity pass/fail decisions.

```bash
python book_video_factory/scripts/review_scene_assets.py \
  --project <PROJECT> \
  --decision <PROJECT>/06_visual_production/SCENE_REVIEW_DECISION.json
```

Show the HBG contact sheet to the user. After explicit approval:

```bash
python book_video_factory/scripts/approve_scene_assets.py \
  --project <PROJECT> --reviewer '<HUMAN>' --note '<APPROVAL_NOTE>'
```

## 7. Prepare and execute the HBG render

Fill `07_render/RENDER_INPUT.json` from its example. The streaming renderer requires an approved opening preview video. HyperFrames requires an exact pinned version and adequate disk.

For the default streaming route, first generate the opening preview from the HBG composition and the pinned HyperFrames version:

```bash
python book_video_factory/scripts/run_render_stage.py preview \
  --project <PROJECT> --input <PROJECT>/07_render/RENDER_INPUT.json

python book_video_factory/scripts/run_render_stage.py prepare \
  --project <PROJECT> --input <PROJECT>/07_render/RENDER_INPUT.json

python book_video_factory/scripts/run_render_stage.py execute \
  --project <PROJECT> --input <PROJECT>/07_render/RENDER_INPUT.json
```

The adapter builds an isolated HBG workspace and invokes the pristine vendored scripts:

```text
build_composition.mjs
validate_style_system.mjs
render_streaming_ffmpeg.mjs or render_long_video.sh
verify_final_video.sh
```

It does not modify the locked Phase 2 project evidence.

After encoded QA passes, show the MP4 and QA contact sheet to the user. Only after explicit approval run:

```bash
python book_video_factory/scripts/approve_final_master.py \
  --project <PROJECT> --reviewer '<HUMAN>' --note '<APPROVAL_NOTE>'
```

Without this fourth human gate the project remains `awaiting_final_master_approval`.

## Resume

```bash
python book_video_factory/scripts/run_full_pipeline.py status --project <PROJECT>
```
