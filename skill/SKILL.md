---
name: book-video-factory
description: Use when turning one classic book into an auditable Chinese single-narrator long video with source-grounded research, a locked script.narrator-essay.v1 manuscript, ImageGen literary frames, continuous Edge TTS and VTT, narration-bound dense storyboards, HBG opening and motion, fixed BGM, streaming FFmpeg rendering, and final encoded-MP4 review.
---

# 名著单主播口播视频工厂

## 唯一默认目标

用户说：

```text
拆解《<书名>》
```

把这本书转化为一条 16:9 单主播名著口播长视频。不要让用户选择其他产品路线、语音体系或渲染器。

固定合同：

- Pipeline：`classic-narrator-hbg-v1`
- Script：`script.narrator-essay.v1`
- Audio：连续 Edge TTS + VTT
- Image：宿主 ImageGen，普通任务可用 2×2，高风险任务必须单图
- Preview：HBG HyperFrames
- Master：HBG 流式 FFmpeg
- State：现有 `workflow.py + gates.py + manifests.py`

## 第一次使用

解析 `SKILL_ROOT` 为本文件所在目录，然后运行：

```bash
python3 <SKILL_ROOT>/scripts/bootstrap_workspace.py --workspace .
python3 <SKILL_ROOT>/scripts/doctor.py --profile planning --json
```

Bootstrap 必须把以下两部分一起部署：

- `skill/runtime/book_video_factory` → `book_video_factory/`
- `vendor/hbg-life-simulation` → `vendor/hbg-life-simulation/`

不得从网络下载隐藏资产，不得读取、打印或复制凭据。

创建项目：

```bash
python3 <SKILL_ROOT>/scripts/bootstrap_workspace.py \
  --workspace . \
  --slug <safe-slug> \
  --book-title '<书名>' \
  --author '<作者>'
```

## 必须按顺序执行

### 1. 中文全文证据（文案引擎 v2）

先按 `references/fulltext-acquisition.md` 找到并落盘该书**中文全文**（`chapters/` + `全文.txt` + `SOURCE.md` + SHA-256）。必须通过顺序/水印/乱码检查；站点打乱段落的文本（如《活着》九九藏书网案例）必须换用 EPUB 底本重建。没有可靠全文时阻断正式写作；不得凭模型记忆开写，不得把影视改编细节混入原著事实。每本书同时产出证据级「全文→口播」拆解（`docs/reference_cases/全文到口播拆解/<NN>_<书名>_全文到口播拆解.md`），全部引文逐字来自全文并标注章节。

### 2. 文案生成 v2：六步证据链工作流

先读 `references/script-workflow-v2.md`（权威工作流）与 `references/narrator-essay-authoring.md`（写作手艺标准），再按六步执行：

1. **找中文全文**（第 1 节；无全文=阻塞，禁止凭记忆开写）
2. **全文→口播拆解**：全书结构/命运线/最强场面≥8/关键引语库15-20句/删留建议/翻案点设计/失实风险清单
3. **三路线竞争→选线**：点击问题≠深层命题，含中点/翻案证据/理论锚/结尾类型/主播人格；产出 `docs/methodology/<书名>_三路线竞争.md`
4. **写稿**：八幕+事件卡+三稿制（V1 故事证据链→V2 人格节奏→V3 门禁去味）
5. **对抗审查**：15项评分≥72、事实可靠性=5（一票否决）、六类失真反查（数字/动机归因/素材混用/净化/引语/译名）、引语逐字回原文、主播露己≥3；不过就按 P0 整改重审
6. **去AI味 + 人工审核**：ra-人话 禁词表扫描 + `docs/methodology/<书名>_人工审核清单.md` → RELEASE 定稿

输出（五件套，与下游合同一致，不可改名）：

- `SCRIPT_PERFORMANCE.md`
- `SCRIPT_RELEASE.md`
- `SCRIPT_AUDIT.md`
- `SCRIPT_REVIEW.json`
- `SCRIPT_LOCK.json`

过程资产（新增，供追溯与复跑）：拆解文档、三路线文档、三稿过程稿、对抗审查报告、引语核验结果、人工审核清单（存 `docs/methodology/` 与 `docs/reference_cases/`）。《活着》完整样例见 `docs/methodology/` 与 `docs/reference_cases/全文到口播拆解/07_活着_全文到口播拆解.md`。

`SCRIPT_RELEASE.md` 必须零工程标签，可直接进入 Edge TTS。不得复制参考创作者的固定话术、比喻、口头禅或声音身份。
### 3. 正式内容包验证与编译

先阅读 `references/content-brain.md`。Agent 生成研究、路线、锚点、事件卡和三版稿后，不得直接进入配音。先运行：

```bash
python3 <WORKSPACE>/book_video_factory/scripts/build_content_package.py \
  --project <PROJECT_DIR> \
  --input-dir <INPUT_DIR> \
  --release-id <RELEASE_ID> \
  --validate-only
```

验证通过后去掉 `--validate-only` 正式编译。正式包必须包含 `SCRIPT_RELEASE.md`、`SCRIPT_AUDIT.md`、`SCRIPT_LOCK.json` 和 `CONTENT_PACKAGE_MANIFEST.json`。Level C、未解析来源、虚构 source ID、`derived candidate`、`待定`、TODO 或其他 placeholder 内容必须阻断。

Phase 1 只生成正式内容包，**不得生成音频、图片或视频**。Phase 2 只负责把已批准内容包编译为 HBG 原生项目制品；Edge TTS、VTT 和后续媒体生产必须等到主角锚点生成并批准后的后续阶段。

### 4. 脚本批准与冻结

机器锁定不等于人工批准。脚本批准绑定 Release 稿、Audit 稿、指标、盲审结果和全部上游证据的 SHA-256。锁稿后任何一个字被修改，都必须使音频、字幕、分镜和下游批准失效。

### 5. 已批准内容包 → HBG 原生项目

先阅读 `references/content-brain.md` 的 Phase 2 交接合同。只有当前 `script` 批准同时覆盖以下五个冻结产物时，才允许执行 Bridge：

- `SCRIPT_RELEASE.md`
- `SCRIPT_AUDIT.md`
- `SCRIPT_METRICS.json`
- `SCRIPT_LOCK.json`
- `CONTENT_PACKAGE_MANIFEST.json`

在项目初始化生成的 `HBG_BRIDGE_INPUT.example.json` 基础上填写章节、人物身份和语义 Beat，保存为项目外或项目内明确指定的 `HBG_BRIDGE_INPUT.json`，然后运行：

```bash
python3 <WORKSPACE>/book_video_factory/scripts/build_hbg_bridge.py   --project <PROJECT_DIR>   --bridge-input <HBG_BRIDGE_INPUT.json>
```

Phase 2 输出 HBG 原生根制品：

- `SCRIPT_SOURCE.md`
- `SCRIPT.md`
- `CHARACTERS.md`
- `PROJECT_SPEC.json`
- `STORYBOARD_BASE.json`
- `HBG_STYLE.json`
- `HBG_BRIDGE_MANIFEST.json`
- `manifests/stages/hbg-bridge-<release>.json`

Bridge 必须逐项验证 Phase 1 内容包、阶段 Manifest、脚本锁、发布稿、人工批准和 HBG Vendor Hash。`SCRIPT_SOURCE.md` 必须与冻结发布稿逐字节一致；`SCRIPT.md` 只能增加章节标题；语义分镜必须由内容 Agent 提供，Bridge 不补写画面。相同输入可幂等重跑，输入变化或用户修改过的输出必须拒绝覆盖。

**Phase 2 不生成音频、图片或视频。** 不得在本阶段调用 Edge TTS、ImageGen、HyperFrames 或 FFmpeg。

### 6. 名著视觉圣经、锚点与 12 张 LookDev

先阅读 `references/visual-stage.md`。Phase 3 必须严格执行：

```text
编译每书视觉 Profile 和任务
→ 使用 host ImageGen 真实生成锚点
→ 登记真实文件、tool call ID 和 Prompt Hash
→ 生成 12 张 LookDev
→ 复用 HBG make_contact_sheet.sh 构建联系表
→ 展示给用户进行语义、现实和审美审查
→ 记录明确批准或拒绝
```

编译视觉阶段：

```bash
python3 <WORKSPACE>/book_video_factory/scripts/build_visual_stage.py \
  --project <PROJECT_DIR> \
  --visual-input <VISUAL_STAGE_INPUT.json>
```

每次 host ImageGen 真实出图后登记：

```bash
python3 <WORKSPACE>/book_video_factory/scripts/register_visual_asset.py \
  --project <PROJECT_DIR> \
  --task-id <TASK_ID> \
  --source <REAL_OUTPUT.png> \
  --tool-call-id <REAL_HOST_TOOL_CALL_ID> \
  --prompt-sha256 <TASK_PROMPT_SHA256> \
  --style-reference-id <TASK_STYLE_REFERENCE_ID> \
  --identity-reference-task-id <REGISTERED_IDENTITY_TASK_ID>
```

每个 `--style-reference-id` 和 `--identity-reference-task-id` 必须按任务 JSON 中的顺序逐项重复传入；无人物依赖的任务不传 identity 参数。登记器会把参考图、Profile 和已登记人物锚点的 Hash 固化为证据，但这仍是宿主调用声明，不是外部平台的密码学证明。

构建 HBG 联系表和审查报告：

```bash
python3 <WORKSPACE>/book_video_factory/scripts/build_visual_review.py --project <PROJECT_DIR>
```

用户明确决定后才运行：

```bash
python3 <WORKSPACE>/book_video_factory/scripts/approve_visual_stage.py \
  --project <PROJECT_DIR> \
  --release-id <RELEASE_ID> \
  --reviewer '<HUMAN_REVIEWER>' \
  --decision-file <VISUAL_REVIEW_DECISION.json>
```

不得伪造 tool call ID、输出 Hash、图片文件或人工批准。参考图不得复制为生产资产。机器诊断不能替代语义、现实和审美批准。只有当前 `visual_anchor_lookdev` 批准有效时，才允许进入 Edge TTS。

### 7. 连续 Edge TTS、VTT 与真实语音分镜

先阅读 `references/audio-stage.md`。Phase 4 使用两次正式调用：

```bash
python3 <WORKSPACE>/book_video_factory/scripts/run_audio_stage.py generate \
  --project <PROJECT_DIR> \
  --input <PROJECT_DIR>/04_audio/AUDIO_STAGE_INPUT.json \
  --lexicon <PROJECT_DIR>/04_audio/PRONUNCIATION_LEXICON.json
```

Pass A 直接复用 HBG `build_narration.mjs`，生成一条连续正文 Edge TTS、原始 VTT、显示安全字幕和初步真实时间线。成功状态必须是 `awaiting_audio_storyboard_plan`。VTT 是唯一时间真源；不得使用估算时间轴。

Codex 必须根据冻结稿、Phase 2 Beat、真实字幕时间和视觉锚点编写：

```text
04_audio/STORYBOARD_AUDIO_PLAN.json
```

随后运行：

```bash
python3 <WORKSPACE>/book_video_factory/scripts/run_audio_stage.py finalize \
  --project <PROJECT_DIR> \
  --plan <PROJECT_DIR>/04_audio/STORYBOARD_AUDIO_PLAN.json

python3 <WORKSPACE>/book_video_factory/scripts/run_audio_stage.py status \
  --project <PROJECT_DIR> \
  --release-id <RELEASE_ID>
```

Pass B 必须复用 Pass A 音频缓存，音频 Hash 不得改变。普通镜头目标 3.2–5.5 秒；超过 12 秒必须解释；超过 16 秒直接失败；zoom/pan 不能替代缺失画面。成功状态为 `ready_for_image_task_planning`。

不得伪造音频、不得伪造 VTT、不得在没有 Edge TTS 时返回成功、不得把发音辅助文本显示为字幕。**Phase 4 不生成图片、HyperFrames、FFmpeg 成片或最终 MP4。**

### 8. 高密度语义分镜

最终 `STORYBOARD.json`、`CAPTION_BINDINGS.json` 和 `AUDIO_TIMELINE_AUDIT.json` 必须共同证明：同一个 Beat 同时绑定旁白、字幕、画面语义、required entities、forbidden entities、风险标记和真实镜头时间。

### 9. ImageGen、宫格和现实检查

普通、同场景、低风险镜头可规划 2×2。以下必须单图：

- 手和物体接触；
- 多人身体接触；
- 绳索、刀具、鱼叉、书信等功能道具；
- 镜面、复杂水面、火焰、动物接触；
- 主角关键肖像、高潮、死亡和 Hero Shot。

复用：

```bash
bash vendor/hbg-life-simulation/scripts/split_2x2.sh ...
bash vendor/hbg-life-simulation/scripts/make_contact_sheet.sh ...
```

每项图片必须检查身份、时代、必需实体、禁止实体、手部、道具方向、支撑关系、文字、水印和当前字幕语义。机器色彩指标不能替代现实与审美审查。

### 10. “读名著”快闪开头

复用 `vendor/hbg-life-simulation/references/opening-system.md` 的时间和运动机制，但使用原创书籍系列文案。标题和作者由 HTML 绘制，不让 ImageGen 生成文字。

顺序固定：价值引导 → 命题快闪与齿轮音效 → 选中本期书名 → 完整揭晓 → 正文首图。

### 11. 组合、混音与渲染

预览：

```bash
node vendor/hbg-life-simulation/scripts/build_composition.mjs
node vendor/hbg-life-simulation/scripts/validate_style_system.mjs <PROJECT_DIR>
```

正式长片：

```bash
HBG_VALIDATE_ONLY=1 node vendor/hbg-life-simulation/scripts/render_streaming_ffmpeg.mjs <PROJECT_DIR> <OUTPUT.mp4>
node vendor/hbg-life-simulation/scripts/render_streaming_ffmpeg.mjs <PROJECT_DIR> <OUTPUT.mp4>
```

默认使用一条恒定 BGM，不逐句自动压低。先审 15–20 秒开头预览，再正式渲染。

### 12. 最终编码检查

```bash
bash vendor/hbg-life-simulation/scripts/verify_final_video.sh <OUTPUT.mp4> <QA_DIR> <time:label>...
```

检查最终编码文件，而不是只看浏览器预览。至少审查分辨率、帧率、音视频流、时长、黑帧、静音、响度、字幕安全区、开头运动、高风险镜头、章节、高潮和尾帧。

最终 `local_master_review` 必须绑定 MP4 Hash；重新编码后旧批准失效。

## 四个人工硬闸门

1. `script`：口播稿和事实批准。
2. `visual_anchor_lookdev`：锚点和 12 张 LookDev 批准。
3. `scene_visual`：所有正式叙事图片的语义、现实和人物一致性批准。
4. `local_master_review`：最终编码 MP4 批准。

自动检查通过不等于视频完成。

## 当前阶段边界

Phase 1 完成正式内容包，Phase 2 完成 Book→HBG Bridge，Phase 3 完成视觉圣经、真实锚点与 LookDev 批准。Phase 4 完成连续 Edge TTS、VTT、显示字幕和真实语音驱动分镜；Phase 5 编译导演时间线和正式图片任务；Phase 6 完成正式场景资产与人工批准；Phase 7 复用 HBG 渲染并在编码 QA 后等待最终母版批准。

### 13. Phase 5：真实音频导演时间线

Phase 4 返回 `ready_for_image_task_planning` 后运行：

```bash
python3 <WORKSPACE>/book_video_factory/scripts/build_director_stage.py --project <PROJECT_DIR>
```

必须生成 `DIRECTOR_TIMELINE.json`、`MOTION_MANIFEST.json`、`IMAGE_TASKS.jsonl`、`PROMPTS.md` 和 `SHEET_MAP.json`。时间只来自真实 Edge VTT；导演编译器不得生成音频、图片或视频。

### 14. Phase 6：正式场景图片

Codex 必须读取每个 production image task，使用宿主 ImageGen 的真实调用、指定风格参考和人物身份锚点。2×2 任务使用：

先运行 `plan_generation_run.py`，读取 `06_visual_production/GENERATION_RUN_MANIFEST.json`，再运行 `next_generation_wave.py`。每波 `最多 5` 个无依赖冲突的 job；只有独立 job 可以并行。同一主身份的竞争任务必须按 DAG 串行。

每个 ImageGen 返回图必须 `显示给用户`，原图先保存到项目 `staging`，再登记真实 `tool call ID` 和文件 `SHA-256`。单个失败不得撤销同波成功结果。当前 wave `全部完成或明确失败` 后才能运行 `finalize_generation_wave.py` 并请求下一波。Python Runtime `不实现私有图片 API`。

```bash
python3 <WORKSPACE>/book_video_factory/scripts/split_scene_sheet.py \
  --project <PROJECT_DIR> --sheet-id <SHEET_ID> --source <REAL_SHEET.png>
```

该命令直接调用 HBG `split_2x2.sh`。随后逐图执行 `register_scene_asset.py`。所有图片都必须完成语义、现实和人物一致性审查，并通过 `scene_visual` 人工批准，才允许渲染。

### 15. Phase 7：HBG 渲染、编码 QA 与母版批准

```bash
python3 <WORKSPACE>/book_video_factory/scripts/run_render_stage.py preview \
  --project <PROJECT_DIR> --input <PROJECT_DIR>/07_render/RENDER_INPUT.json
python3 <WORKSPACE>/book_video_factory/scripts/run_render_stage.py prepare \
  --project <PROJECT_DIR> --input <PROJECT_DIR>/07_render/RENDER_INPUT.json
python3 <WORKSPACE>/book_video_factory/scripts/run_render_stage.py execute \
  --project <PROJECT_DIR> --input <PROJECT_DIR>/07_render/RENDER_INPUT.json
```

Python 适配层只建立隔离 HBG workspace 和校验证据。实际组合、HyperFrames、流式 FFmpeg 和最终媒体检查继续调用 vendored HBG 脚本。编码 QA 通过后必须展示 MP4 和 QA 联系表，再运行：

```bash
python3 <WORKSPACE>/book_video_factory/scripts/approve_final_master.py \
  --project <PROJECT_DIR> --reviewer '<HUMAN>' --note '<APPROVAL_NOTE>'
```

没有 `FINAL_MASTER_APPROVAL.json` 时项目不得宣称 `complete`。
