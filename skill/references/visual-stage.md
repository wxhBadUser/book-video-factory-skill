# Phase 3：名著视觉圣经、锚点与 12 张 LookDev

Phase 3 的目标是把当前、已批准的 Phase 2 HBG 项目转化为可审查的视觉预生产包。它只完成视觉圣经、锚点任务、十二张 LookDev、真实资产登记、HBG 联系表和人工视觉批准。

所有视觉输入、任务和人工审查还必须遵守 `scene-consistency-contract.md`。本阶段冻结的是后续每个 Scene Signature 的事实底座，不只是“好看的一组图”。

**Phase 3 不生成 TTS、VTT、HyperFrames、FFmpeg 成片或最终 MP4。** 只有当前 `visual_anchor_lookdev` 批准有效时，下一阶段才是 `ready_for_narration`（`narration_provider_policy=minimax_required` 的新项目）或 `ready_for_edge_tts`（显式 `legacy_edge` 的旧项目）。

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

并为后续 Scene Signature 明确可追溯的事实来源：人物身份根及人生阶段、服装/帽子连续状态、持续地点锚点、时代物质约束、关键道具锚点和禁止跨时代/跨地域实体。缺失这些事实时，不得用泛化提示词补造。

### 角色、风格和地点的参考卫生

- Style Master 只能从人物中性的 LookDev 建立：源图不得是 character anchor，且其 `anchor_refs` 不得包含持续角色；风格参考只控制渲染语言，不控制任何角色身份。
- Character Identity 必须按 `character_id + life_stage` 建根，根图必须是当前批准的 `FRONT`；3/4、全身、服装与场景变体必须绑定根图 SHA-256。先锁人生阶段，再派生视角。
- 像福贵这样的跨年龄角色必须保留青年 → 中年 → 老年的连续身份链，不允许分别随机生成“几个像农村男人的人”。
- Location Anchor 必须是人物中性的 `scene_anchor`；不可把一次性刑场、婚礼、人物肖像或抽象风景作为持续地点参考。
- 生成请求使用的 style / identity / location 参考必须按任务顺序传入。能取得 provider 回执时记录回执；宿主无法给回执时必须如实标为“传入声明 + 本地输入 SHA”，不能升级成已证实的字节传输。

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

长任务必须有界巡检（通常每 60–90 秒），每次记录任务状态、已生成文件与错误原因；运行环境的代理、浏览器或认证故障只在该项目的排错记录中解决，不抽象成所有书籍都必须使用的命令。

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

### 2.1 候选图卫生与选择

原始生成图先进入项目 `staging`，保留不裁切原件；需要归一化时输出新文件。每张候选都先检查可解码性、1920×1080/RGB(A)、SHA-256、四边黑边/信箱边与可见文字、水印、logo、UI、伪文字。再检查 Scene Signature 的人物、时代、地点、道具和禁止实体。

技术检查、视觉 MCP/模型描述与 Agent 目检都只是审查证据。它们不能把 `pending` 改为 `pass`，更不能替代用户对语义、现实与审美的决定。若某变体有黑边、内嵌字幕或语义污染，只重生成该任务，保留所有候选和明确的选用/拒绝理由。

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

同时确认这些锚点能作为后续任务的事实引用：同一人物不是“相似长相”，同一地点不是“相似风景”，同一时代不是“泛旧感”。如果身份、人生阶段、时代、地域或锚点道具未成立，拒绝视觉基础，而不是把问题留给场景生成。

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
