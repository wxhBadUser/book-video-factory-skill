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

## Image provider fallback chain（用户 2026-08-16 确认）

生图按以下优先级，不能跳过一级直接给用户，也不能卡死：

1. **Host ImageGen**（项目默认 provider）：`generation_lane=host-imagegen`，走 wave 协议。能用就用。
2. **Google Flow**（`labs.google/fx/tools/flow`，本机已验证过多次）：当 imagegen 不可用或失败时，用 Flow i2i（每任务 ≥2 张，视觉核验后取最优），经 `media-suite` 包装入口 + flow-chrome profile（详见用户全局 CLAUDE.md「Google 生图关键踩坑」）。
3. **用户手动生成**：以上都不行时——**不要编造、不要占位、不要伪造输出**——给用户完整可执行的提示词（prompt）+ 参考内容（批准锚点/风格图路径），让用户自己生成，等用户回填后再继续。

无论走哪一级，产出的图片仍必须走现有注册/审查/批准通道（`register_scene_asset` → 视觉审查 → `SCENE_ASSET_APPROVAL.json`），hash 绑定不变。生成失败是阻塞结果，不是占位理由。

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

## Subagent task mailbox (fallback when message payload is dropped)

The collaboration message channel can wake an agent without delivering the message body (observed 2026-08-14: spawn/followup/send_message all returned success, but the child agent saw only workspace environment info and an empty task).

Protocol:
1. If a subagent is spawned or woken and its incoming task text is empty/unclear (e.g. "没有收到具体任务内容"), it MUST check `qa/final-workflow-audit/SUBAGENT_TASK.md` in the repository root before replying "no task".
2. If that file exists, treat it as the authoritative task. Read it completely, follow its instructions, and write the requested deliverable to the output path specified inside it. Do not modify production code unless the task explicitly authorizes it.
3. After finishing, reply with a one-paragraph summary (what was verified, output path, key findings).
4. The root agent writes/updates this file before spawning or waking a subagent, and verifies the output file after the agent completes.

This is a workaround for the broken payload channel; keep it until the collaboration message channel is confirmed fixed.

