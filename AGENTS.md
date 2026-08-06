# Codex operating contract — Book Video Factory

This repository has one active product: a Chinese single-narrator literary video built from a lawful full-text source. Read `CODEX_AGENT.md`, `FULL_PIPELINE.md`, and `docs/PITFALLS_AND_LESSONS.md` (环境/工具/管线踩坑速查) before changing code or running a project.

## Non-negotiable rules

1. Reuse `vendor/hbg-life-simulation` for Edge TTS/VTT, contact sheets, motion composition, HyperFrames, streaming FFmpeg, and encoded-media evidence. Do not reimplement those engines.
2. Use the existing `workflow.py`, gates, manifests, and approval events. Do not create a second progress database.
3. Never invent research evidence, source hashes, ImageGen call IDs, images, audio, VTT, approvals, or MP4 files.
4. A missing external tool is a blocking result, not permission to create a placeholder.
5. Do not continue past a human gate until the current approval event binds every required SHA-256.
6. Do not modify `vendor/hbg-life-simulation`; its `UPSTREAM_LOCK.json` must remain valid.
7. Read `python book_video_factory/scripts/run_full_pipeline.py status --project <PROJECT>` before deciding the next action.

## Host ImageGen wave protocol

- Phase 6 must read `06_visual_production/GENERATION_RUN_MANIFEST.json`, then call `next_generation_wave.py`; one returned wave contains at most `最多 5` independent jobs.
- Only jobs without DAG or shared-identity conflicts may run in parallel. Display every returned image to the user (`显示给用户`) and save the original in project `staging` before publication.
- Register the real Host ImageGen `tool call ID` and the exact file `SHA-256`. A failed job must not discard successful siblings.
- Request another wave only after the current wave is `全部完成或明确失败`. Python runtime does `不实现私有图片 API` and never fabricates a result.

## Required workflow

```text
Level-A source and research
→ locked single-narrator script + script approval
→ Book→HBG Bridge
→ visual bible, anchors, 12 LookDev images + visual approval
→ continuous Edge TTS, VTT, display captions, audio-driven storyboard
→ director timeline, motion manifest, production image tasks
→ real scene images, semantic/reality/identity review + scene approval
→ HBG render workspace, HBG render, HBG encoded QA
→ final MP4 evidence + explicit local master approval
```

## Safe completion language

Only say a stage is complete after its scanner/tests and current manifest hashes pass. Only say a video exists after `FINAL_RENDER_MANIFEST.json` binds a nonempty MP4 and `FINAL_QA_REPORT.json` passes. Only say the project is complete after `FINAL_MASTER_APPROVAL.json` and its `local_master_review` event bind the current encoded evidence.

