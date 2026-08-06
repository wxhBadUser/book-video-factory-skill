# Phase 3：名著视觉圣经、锚点与 12 张 LookDev

Phase 3 的目标是把当前、已批准的 Phase 2 HBG 项目转化为可审查的视觉预生产包。它只完成视觉圣经、锚点任务、十二张 LookDev、真实资产登记、HBG 联系表和人工视觉批准。

**Phase 3 不生成 Edge TTS、VTT、HyperFrames、FFmpeg 成片或最终 MP4。** 只有当前 `visual_anchor_lookdev` 批准有效时，下一阶段才是 `ready_for_edge_tts`。

## 固定执行顺序

### 1. 编写视觉阶段输入

内容 Agent 根据冻结稿、人物身份和上传的金标准视觉证据编写 `VISUAL_STAGE_INPUT.json`。必须明确：

- 每本书独立的时代、地域和视觉母语；
- 色板、照明、材质、构图和重复母题；
- 禁止特征和负面约束；
- 每个持续出镜人物的身份不变量；
- 场景和关键物件锚点；
- 恰好 12 个 LookDev 类别；
- 每个任务的 required / forbidden entities；
- 所有参考图只能作为 style evidence。

运行：

```bash
python3 <WORKSPACE>/book_video_factory/scripts/build_visual_stage.py \
  --project <PROJECT_DIR> \
  --visual-input <VISUAL_STAGE_INPUT.json>
```

成功后生成 `BOOK_VISUAL_PROFILE.json`、`ANCHOR_TASKS.jsonl`、`LOOKDEV_TASKS.jsonl`、`PROMPTS.md` 和视觉阶段 Manifest。此时没有任何图片资产。

### 2. 使用 host ImageGen 真实生成

Codex 必须逐项读取任务 JSONL 和 `PROMPTS.md`，调用当前宿主提供的 host ImageGen。Python Runtime 不实现私有图片 API，不读取隐藏图片密钥，也不伪造生成结果。

生成顺序：

1. 人物、场景和物件锚点；
2. 锚点成功登记后再生成依赖这些锚点的 LookDev；
3. 每个任务只登记真实、可解码、1920×1080 的 PNG；
4. 失败任务只重试自身，不把失败描述成已生成。

绝对禁止：

- 不得伪造 tool call ID；
- 不得伪造输出 Hash；
- 不得伪造图片文件；
- 不得用改扩展名的损坏文件冒充图片；
- 不得把联系表或参考图登记成单张生成结果；
- 参考图不得复制为生产资产；
- style reference 不得冒充人物 identity reference。

每次真实出图后运行：

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

登记器只保存真实文件、真实尺寸、真实 Hash 和机器诊断。`semantic_review_status`、`reality_review_status` 与 `human_review_status` 必须保持 `pending`。

### 3. 用 HBG 构建联系表和审查证据

所有锚点与 12 张 LookDev 都登记后运行：

```bash
python3 <WORKSPACE>/book_video_factory/scripts/build_visual_review.py \
  --project <PROJECT_DIR>
```

该命令直接复用：

```bash
bash vendor/hbg-life-simulation/scripts/make_contact_sheet.sh ...
```

输出 `LOOKDEV_CONTACT_SHEET.jpg` 和 `VISUAL_REVIEW_REPORT.json`。机器色彩与文件诊断只提供证据，不能自动替代语义、现实和审美审查。

Codex 必须把联系表和关键单图展示给用户，逐项检查：

- 人物身份与年龄、服装是否稳定；
- 场景、物件和时代是否正确；
- 手、道具、支撑、反射和物理关系是否可信；
- 图片是否表达当前 Beat，而非无关风景；
- 是否达到该书视觉 Profile 和金标准文学电影质感；
- 是否存在文字、水印、AI 网红脸或廉价滤镜。

### 4. 记录明确的人类决定

先创建 `visual-review-decision.v1` JSON，完整填写报告要求的 semantic、reality、aesthetic 三组检查。没有用户明确决定时不得默认批准。

运行：

```bash
python3 <WORKSPACE>/book_video_factory/scripts/approve_visual_stage.py \
  --project <PROJECT_DIR> \
  --release-id <RELEASE_ID> \
  --reviewer '<HUMAN_REVIEWER>' \
  --decision-file <VISUAL_REVIEW_DECISION.json> \
  --note '<REVIEW_NOTE>'
```

只有标准 approval event 与全部证据 Hash 一致时，系统才生成 `ANCHOR_APPROVAL.json`。

- 不得伪造人工批准；
- 不得让 Agent 自己把 `pending` 改为 `pass`；
- 不得直接手写 `ANCHOR_APPROVAL.json`；
- 不得省略语义、现实或审美检查；
- 批准后任何 Profile、任务、资产、报告或联系表改变，旧批准立即失效。

## Host ImageGen wave protocol（Phase 6 continuation）

Phase 3 的依赖锚点仍按本文件顺序生成。进入 Phase 6 后，先运行 `plan_generation_run.py`，读取 `06_visual_production/GENERATION_RUN_MANIFEST.json`，再运行 `next_generation_wave.py`。每波 `最多 5` 个无依赖冲突 job，只有独立 job 可并行。

每个返回图必须 `显示给用户`，原图先保存在项目 `staging`，并绑定真实 `tool call ID` 和文件 `SHA-256`。失败 job 不得清除成功 sibling；当前 wave `全部完成或明确失败` 后才能通过 `finalize_generation_wave.py` 结算并请求下一波。Python Runtime `不实现私有图片 API`。

## 交付边界

Phase 3 真正完成时只能声称：

- 视觉 Profile 已编译；
- 锚点与 12 张 LookDev 已由 host ImageGen 真实生成并登记；
- HBG 联系表已构建；
- 用户已对当前 Hash 绑定证据明确批准或拒绝。

不得声称音频、VTT、全量分镜图片、视频或 MP4 已完成。
