# Troubleshooting

## Status is blocked after a file edit

Approvals and downstream manifests bind SHA-256. Restore the exact approved file or intentionally start a new release and regenerate dependent stages. Do not edit generated manifests to match the changed file.

## `edge-tts` is missing

Install it in the same Python environment used by Codex, rerun Doctor, then rerun Phase 4. Never replace it with silent fixture media.

## Host ImageGen is unavailable

Stop with the task queue ready. Another compatible ChatGPT/Codex host may execute the tasks later. Do not create colored placeholders or fake call IDs.

## HBG vendor integrity fails

Restore `vendor/hbg-life-simulation` from the locked upstream snapshot. Do not update individual files or rewrite the missing function locally.

## Streaming render says opening video is missing

Run `python book_video_factory/scripts/run_render_stage.py preview --project <PROJECT> --input <PROJECT>/07_render/RENDER_INPUT.json`. The command renders the HBG-generated opening composition with the pinned HyperFrames version and verifies motion/dimensions with HBG before writing `OPENING_PREVIEW_MANIFEST.json`. Do not copy an unrelated MP4 into the preview path.

## Render work directory already exists

Confirm no matching process is alive. Preserve it for diagnosis. Remove only that exact inactive `ffmpeg-work-<output>` directory; never clear the whole render tree.

## Final QA reports black or silence

Open the named encoded frames and inspect the detected intervals. Fix the render inputs/mix and create a new revisioned output. Do not delete QA evidence or weaken thresholds.
