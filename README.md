# 名著单主播口播视频工厂

把一本世界名著转化为一条**单主播、强故事、高密度文学影像**的中文长视频。

本仓库只维护一条默认生产线：

```text
可靠原著来源
→ 金标准口播稿
→ 脚本冻结
→ 人物与视觉锚点
→ 连续 Edge TTS + VTT
→ 真实语音驱动分镜
→ ImageGen 单图 / 2×2 宫格
→ “读名著”快闪开头
→ 同步字幕、zoom / pan、章节和恒定 BGM
→ HBG HyperFrames 预览或流式 FFmpeg 正式渲染
→ 最终编码 MP4 质检
```

## 架构

```text
book_video_factory/
  内容大脑：原著研究、事实台账、三路线竞争、命运锚点、事件卡、
  script.narrator-essay.v1、盲审、Hash 审批和唯一状态机

vendor/hbg-life-simulation/
  视频生产底盘：Edge TTS、VTT、语义字幕、密集分镜校验、2×2 拆图、
  快闪开头、zoom / pan、固定 BGM、HyperFrames 和流式 FFmpeg

skill/
  Agent 入口、安装器、运行手册和清理后的 Runtime 快照
```

HBG 以完整、保留 MIT 许可证、SHA-256 锁定的上游快照引入。生产代码直接调用其脚本，不在 Python 中重写同样的媒体能力。

## Phase 0–7 完整交付范围

Phase 1 在 Phase 0 的供应商锁定和单主线架构上，完成正式内容大脑：

- Level A 正式来源门禁和实际可解析覆盖检查；
- 禁止自动补齐虚假事件或 placeholder 内容；
- 三条路线、命运锚点、omission map 和事件卡交叉引用；
- Performance、Release、Audit 三版同构稿件；
- 220–245 字/分钟金标准语速合同与确定性指标；
- 原子、幂等、拒绝冲突覆盖的正式内容包编译器；
- 绑定九类输入 Hash 的 `SCRIPT_LOCK.json`；
- `build_content_package.py` 正式 CLI。

机器锁定不等于人工批准。后续阶段由同一 Manifest/Approval 证据链驱动，直到 HBG 成片和最终母版人工批准。

### 正式内容包

先验证：

```bash
python workspace/book_video_factory/scripts/build_content_package.py \
  --project workspace/book_video_warehouse/projects/old-man-and-the-sea \
  --input-dir ./content-input \
  --release-id r1 \
  --validate-only
```

验证通过后移除 `--validate-only`。成功产物包括 `SCRIPT_RELEASE.md`、`SCRIPT_AUDIT.md`、`SCRIPT_LOCK.json` 和 `CONTENT_PACKAGE_MANIFEST.json`。


## Phase 2：Book → HBG Bridge

完成 `script` 人工批准后，填写项目中的 `HBG_BRIDGE_INPUT.example.json`，保存为正式 `HBG_BRIDGE_INPUT.json`，再运行：

```bash
python workspace/book_video_factory/scripts/build_hbg_bridge.py   --project workspace/book_video_warehouse/projects/old-man-and-the-sea   --bridge-input ./HBG_BRIDGE_INPUT.json
```

Bridge 会验证内容包、批准覆盖范围、脚本锁和 HBG Vendor Hash，在 staging 中复用上游样式初始化器与分镜校验器，然后原子发布 `SCRIPT_SOURCE.md`、`SCRIPT.md`、`CHARACTERS.md`、`PROJECT_SPEC.json`、`STORYBOARD_BASE.json` 和 `HBG_STYLE.json`。

**Phase 2 不生成音频、图片或视频。** Edge TTS/VTT 和媒体生产必须等待后续阶段。

## Phase 3：视觉圣经、锚点与 12 张 LookDev

Phase 3 复用现有视觉提示词底层、HBG `make_contact_sheet.sh`、Manifest 和 approval event。Python 只编译任务、登记真实资产和验证证据，不实现私有图片 API。

```bash
python workspace/book_video_factory/scripts/build_visual_stage.py \
  --project <PROJECT> \
  --visual-input <VISUAL_STAGE_INPUT.json>

python workspace/book_video_factory/scripts/register_visual_asset.py \
  --project <PROJECT> --task-id <TASK_ID> --source <REAL_OUTPUT.png> \
  --tool-call-id <REAL_TOOL_CALL_ID> --prompt-sha256 <PROMPT_SHA256> \
  --style-reference-id <TASK_STYLE_REFERENCE_ID> \
  --identity-reference-task-id <REGISTERED_IDENTITY_TASK_ID>

python workspace/book_video_factory/scripts/build_visual_review.py --project <PROJECT>
python workspace/book_video_factory/scripts/approve_visual_stage.py \
  --project <PROJECT> --release-id <RELEASE_ID> --reviewer '<REVIEWER>' \
  --decision-file <VISUAL_REVIEW_DECISION.json>
```

每个 `--style-reference-id` 和 `--identity-reference-task-id` 必须按任务 JSON 中的顺序逐项重复传入；无人物依赖的任务不传 identity 参数。登记器会把参考图、Profile 和已登记人物锚点的 Hash 固化为证据，但这仍是宿主调用声明，不是外部平台的密码学证明。

必须先由 host ImageGen 真实生成并登记主角、配角、场景、物件锚点和恰好 12 张 LookDev，再用 HBG 生成联系表并交给用户审查。自动诊断不等于批准。**Phase 3 不生成 Edge TTS、VTT、全量分镜图片、视频或最终 MP4。**

## Phase 4：连续 Edge TTS、VTT 与真实语音分镜

视觉批准后，Phase 4 通过 `scripts/run_audio_stage.py` 执行两次编译。`generate` 直接调用 HBG 原生 `build_narration.mjs` 生成连续正文音频、原始 VTT、显示安全字幕和初步时间线；Codex 再根据真实语音编写 `STORYBOARD_AUDIO_PLAN.json`，由 `finalize` 校验镜头密度、字幕覆盖和语义绑定，并证明音频 Hash 未改变。

```bash
python workspace/book_video_factory/scripts/doctor.py --profile production --json
python workspace/book_video_factory/scripts/run_audio_stage.py generate \
  --project <PROJECT> --input <PROJECT>/04_audio/AUDIO_STAGE_INPUT.json \
  --lexicon <PROJECT>/04_audio/PRONUNCIATION_LEXICON.json
python workspace/book_video_factory/scripts/run_audio_stage.py finalize \
  --project <PROJECT> --plan <PROJECT>/04_audio/STORYBOARD_AUDIO_PLAN.json
python workspace/book_video_factory/scripts/run_audio_stage.py status \
  --project <PROJECT> --release-id <RELEASE_ID>
```

没有 `edge-tts` 或网络访问时必须失败；不得伪造音频、VTT 或估算时间轴。**Phase 4 不生成图片、HyperFrames、FFmpeg 成片或最终 MP4。**


## Phase 5–7：导演、正式场景图与 HBG 成片

Phase 4 返回 `ready_for_image_task_planning` 后：

```bash
python workspace/book_video_factory/scripts/build_scene_continuity_spans.py --project <PROJECT>
python workspace/book_video_factory/scripts/build_director_stage.py --project <PROJECT>
```

`SCENE_CONTINUITY_SPANS.json` separates caption semantics from image count: keep one representative image while people, location, relationship and scene-defining state remain stable; do not cut merely because the narrated action changes.

Codex 按 `05_director/IMAGE_TASKS.jsonl` 和 `SHEET_MAP.json` 使用 Host ImageGen 真实出图；2×2 宫格使用 `split_scene_sheet.py` 调用 HBG 原生拆图脚本。所有场景图登记、联系表审查和人工批准完成后，填写 `07_render/RENDER_INPUT.json`，使用 HBG 原生 HyperFrames 或流式 FFmpeg 渲染：

```bash
python workspace/book_video_factory/scripts/run_render_stage.py preview --project <PROJECT> --input <PROJECT>/07_render/RENDER_INPUT.json
python workspace/book_video_factory/scripts/run_render_stage.py prepare --project <PROJECT> --input <PROJECT>/07_render/RENDER_INPUT.json
python workspace/book_video_factory/scripts/run_render_stage.py execute --project <PROJECT> --input <PROJECT>/07_render/RENDER_INPUT.json
python workspace/book_video_factory/scripts/approve_final_master.py --project <PROJECT> --reviewer '<HUMAN>' --note '<APPROVAL_NOTE>'
```

最终状态只有在编码 QA 通过且母版得到明确人工批准后才是 `complete`。完整操作见 `CODEX_AGENT.md` 和 `FULL_PIPELINE.md`。

## 快速开始

### 1. 初始化工作区

```bash
python skill/scripts/bootstrap_workspace.py --workspace ./workspace
python skill/scripts/doctor.py --profile planning --json
```

### 2. 创建一本书的项目壳

```bash
python skill/scripts/bootstrap_workspace.py \
  --workspace ./workspace \
  --slug old-man-and-the-sea \
  --book-title '老人与海' \
  --author '欧内斯特·海明威'
```

项目固定使用：

- Style：`classic-narrator-hbg-v1`
- Release：`book-classic-narrator-hbg-16x9-v1`
- Script：`script.narrator-essay.v1`
- Audio：Edge TTS + VTT
- Preview：HBG HyperFrames
- Master：`vendor/hbg-life-simulation/scripts/render_streaming_ffmpeg.mjs`

### 3. 运行测试

```bash
python -m pytest book_video_factory/tests skill/tests -q
```

## 四个人工硬闸门

1. **脚本闸门**：事实、引语、口播稿和盲审通过后锁稿。
2. **锚点闸门**：主角锚点、核心场景、关键物件和 12 张 LookDev 联系表通过后才能进入配音和全量生图。
3. **场景图片闸门**：所有正式场景图完成语义、现实和人物一致性审查后才能渲染。
4. **母版闸门**：最终编码 MP4、关键帧、音频和音画一致性通过后才能发布。

所有批准都绑定被审文件的 SHA-256；上游文件改变后，批准自动失效。

## 不重复造轮子的规则

- HBG 已有 Edge TTS/VTT，不另写一套默认 TTS。
- HBG 已有宫格拆图和联系表脚本，不另写重复实现。
- HBG 已有流式 FFmpeg，不保留第二个默认长片渲染器。
- 本仓库已有 Workflow/Gates/Manifests，不创建第二套状态文件。
- 字幕、图片、旁白和时间轴必须共享同一个 Beat ID。
- 每本书使用独立视觉 Profile，不能用一套固定油画滤镜审查所有作品。

## 关键文档

- `docs/architecture/BOOK_HBG_FUSION_BLUEPRINT_V1.md`
- `docs/architecture/UPSTREAM_REUSE_MANIFEST_V1.json`
- `docs/gold_standard/BOOK_NARRATION_GOLD_STANDARD.md`
- `skill/references/narrator-essay-authoring.md`
- `skill/references/content-brain.md`
- `vendor/hbg-life-simulation/UPSTREAM_LOCK.json`
- `THIRD_PARTY_NOTICES.md`
