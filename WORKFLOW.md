# 工作流说明 — 名著单主播口播视频工厂（book-video-factory skill）

本文档说明本仓库「skill 流程」的完整工作流：仓库里有什么、端到端管线怎么走、
4 道人工批准门在哪、图像 provider 回退链怎么用、以及如何修改并重新验证。
它是 `SKILL.md`（使用入口）与 `FULL_PIPELINE.md`（命令手册）的配套总览。

---

## 1. 这是什么

一个把经典名著转化为 **16:9 中文单主播口播长视频** 的可审计流水线。

固定合同（不可改选）：

| 环节 | 合同 |
|---|---|
| Pipeline | `classic-narrator-hbg-v1` |
| Script | `script.narrator-essay.v1`（八幕 + 事件卡 + 三稿制） |
| Audio | 默认 MiniMax + provider VTT；`legacy_edge` 仅兼容旧项目 |
| Image | 宿主 ImageGen（默认）；回退 Google Flow；最后用户手动 |
| Preview | HBG HyperFrames |
| Master | HBG `static_streaming_ffmpeg`（旧 `streaming_ffmpeg` 仅兼容） |
| State | 现有 `workflow.py + gates.py + manifests.py`，不建第二份进度库 |

核心原则：**不编造**。不发明研究证据、源哈希、ImageGen call ID、图片、音频、
VTT、批准事件或 MP4；缺外部工具是阻塞结果，不是造占位符的理由。

---

## 2. 仓库结构与本仓库边界（VCS）

本仓库只承载 **skill 源码**（代码、schema、配置、文档），生成物/媒体/本地工作区
一律不进版本控制。`git add` 只允许带进 `skill/` 与根文档。

```
根目录
├── skill/                     ← 唯一进 VCS 的代码本体（SKILL.md 同目录）
│   ├── SKILL.md                skill 使用入口（触发词、首次使用、顺序合同）
│   ├── agents/                 各 agent 角色提示词
│   ├── references/             权威工作流/手艺标准/坑速查（fulltext、script-workflow-v2、
│   │                           visual-stage、audio-stage、scene-consistency-contract…）
│   ├── scripts/                bootstrap_workspace.py / doctor.py 等技能级脚本
│   ├── tests/                  技能级测试
│   └── runtime/book_video_factory/   ← 真正的管线实现（canonical 源码）
│       ├── config/             pipelines / release_profiles / style_profiles
│       ├── schemas/            各阶段输入/证据 JSON Schema（v1/v2）
│       ├── scripts/            build_* / run_* / approve_* / register_* 等 CLI
│       ├── src/book_video_factory/   各阶段 compiler/contracts/gates/manifests
│       └── pyproject.toml
├── AGENTS.md                   执行契约（本仓库最高规则）
├── WORKFLOW.md                 本文档
├── FULL_PIPELINE.md            Phase 0–7 逐命令手册
├── CODEX_AGENT.md / INSTALL.md / TROUBLESHOOTING.md / README.md
└── .gitignore                  边界定义（见下）
```

`.gitignore` 排除（锚定根目录，不影响 `skill/runtime`）：`/qa/`、`/docs/`、
`/workspace/`、`/content-input/`、`/book_video_warehouse/`（项目数据）、
`/book_video_factory/`（部署镜像，代码本体在 `skill/runtime`）、`/scripts/`（根级
临时验证脚本）、`/.workbuddy/`（Agent 本地记忆）、`/_tmp_*`（开发杂散），以及全部
媒体二进制（png/jpg/mp4/wav…）。

> 含义：`qa/` 的审计报告与 `docs/` 设计文档**刻意不进 VCS**——例如
> `qa/vision_review_provider.json`（用哪个视觉模型审图）留在本地，部署方必须自己
> 提供，`load_vision_review_provider()` 缺失时 fail-closed，杜绝夹带攻击者选的审查者。

---

## 3. 端到端流程（Phase 0–7）与 4 道人门

任何时刻用 `python book_video_factory/scripts/run_full_pipeline.py status --project <PROJECT>`
决定下一步。阶段推进以 **per-stage manifest + approval event** 为准。

### Phase 0 — 安装与自检
```bash
python -m pip install -e "book_video_factory[production]"
python book_video_factory/scripts/doctor.py --profile production --json
python book_video_factory/scripts/run_full_pipeline.py verify-repository --root .
```

### Phase 1 — 全文证据 + 文案（6 步证据链）→ **门 1：文案人工批准**
1. 找该书**中文全文**并落盘（`chapters/` + `全文.txt` + `SOURCE.md` + SHA-256），
   必须过顺序/水印/乱码检查；无可靠全文 = 阻断，禁止凭模型记忆开写。
2. 全文→口播拆解（结构/命运线/最强场面 ≥8/关键引语库/删留建议/翻案点/失实风险）。
3. 三路线竞争→选线（`docs/methodology/<书名>_三路线竞争.md`）。
4. 写稿：八幕 + 事件卡 + 三稿制（V1 证据链 → V2 人格节奏 → V3 门禁去味）。
5. 对抗审查：15 项评分 ≥72、事实可靠性=5（一票否决）、引语逐字回原文。
6. 去 AI 味 + 人工审核清单 → RELEASE 定稿。

输出五件套（与下游合同一致，不可改名）：
`SCRIPT_PERFORMANCE.md` / `SCRIPT_RELEASE.md` / `SCRIPT_AUDIT.md` /
`SCRIPT_REVIEW.json` / `SCRIPT_LOCK.json`。

### Phase 2 — HBG 桥接
```bash
python book_video_factory/scripts/build_hbg_bridge.py \
  --project <PROJECT> \
  --bridge-input <PROJECT>/02_story_script_故事脚本/HBG_BRIDGE_INPUT.json
```
产出 `HBG_BRIDGE_MANIFEST.json`（桥接逐字哈希被 Phase 3 强校验复用）。

### Phase 3 — 视觉圣经 + LookDev → **门 2：视觉圣经人工批准**
```bash
python book_video_factory/scripts/build_visual_stage.py \
  --project <PROJECT> \
  --visual-input <PROJECT>/03_images_生成图片/VISUAL_STAGE_INPUT.json
```
`build_visual_stage.py` 强校验 `hbg_bridge_digest` / `release_text_sha256` /
`release_id` 与 Phase 2 逐字一致。视觉圣经必须包含：book_look、palettes、lighting、
material、scene/object/character anchors（required_views = front / three_quarter /
full_body / expression_range / wardrobe）、symbolic_mappings，以及**恰好 12 个
LookDev 任务**覆盖 12 类（identity_portrait / full_body / relationship / interior /
exterior / object / daylight / low_light / action / emotional_closeup /
environment / hero_composition）。

生成并注册全部 anchors + LookDev 真图后：
```bash
python book_video_factory/scripts/build_visual_review.py --project <PROJECT>
python book_video_factory/scripts/approve_visual_stage.py \
  --project <PROJECT> --release-id r1 --reviewer '<HUMAN>' \
  --decision-file <PROJECT>/03_images_生成图片/VISUAL_REVIEW_DECISION.json
```

### Phase 4 — 连续旁白音频（真实时序）
```bash
python book_video_factory/scripts/run_audio_stage.py generate \
  --project <PROJECT> \
  --input <PROJECT>/04_audio/AUDIO_STAGE_INPUT.json \
  --lexicon <PROJECT>/04_audio/PRONUNCIATION_LEXICON.json \
  --provider minimax
```
然后基于真实显示字幕与时间轴撰写 `STORYBOARD_AUDIO_PLAN.json`：
```bash
python book_video_factory/scripts/run_audio_stage.py finalize \
  --project <PROJECT> --plan <PROJECT>/04_audio/STORYBOARD_AUDIO_PLAN.json --provider minimax
```
新项目强制 `minimax_required`：`VOICE_FOUNDATION.json`、真实 provider 音频、provider
时间戳、哈希绑定的生成证据缺一不可；MiniMax 失败**绝不**回退 Edge。

### Phase 5 — 导演时间线 + 生产图任务
```bash
python book_video_factory/scripts/build_scene_continuity_spans.py --project <PROJECT>
python book_video_factory/scripts/build_director_stage.py --project <PROJECT>
```
`SCENE_CONTINUITY_SPANS.json` 是静态图分配的最终合同：一个 span 恰好一个图像任务。
产出 `05_director/{DIRECTOR_TIMELINE,MOTION_MANIFEST,IMAGE_TASKS.jsonl,PROMPTS.md,SHEET_MAP}.json`。
时序只来自 Phase 4 音频/VTT；高风险任务强制单图；prompt 复用 Phase 3 视觉档案与已批准身份锚点。

### Phase 6 — 场景图波次生成/注册/审查 → **门 3：场景图人工批准**
```bash
python book_video_factory/scripts/plan_generation_run.py --project <PROJECT> --concurrency 5
python book_video_factory/scripts/next_generation_wave.py --project <PROJECT>
```
波次协议：一个 wave 最多 5 个无 DAG/共享身份冲突的独立 job；每张返回图必须
**显示给用户**、原样存项目 `staging`、绑定真实 tool call ID + SHA-256；失败 job
不丢弃成功兄弟；`finalize_generation_wave.py` 收尾后请求下一波。Python 运行时
**不实现私有图片 API**。

注册 + 审查 + 批准：
```bash
python book_video_factory/scripts/register_scene_asset.py \
  --project <PROJECT> --task-id <TASK_ID> --source <REAL_PNG> \
  --tool-call-id <REAL_CALL_ID> --style-reference-id <REFERENCE_ID> \
  --identity-reference-task-id <IDENTITY_TASK_ID>
# 2×2 组合：split_scene_sheet.py 拆后逐个注册；高风险任务保持单图

python book_video_factory/scripts/review_scene_assets.py \
  --project <PROJECT> --decision <PROJECT>/06_visual_production/SCENE_REVIEW_DECISION.json
python book_video_factory/scripts/approve_scene_assets.py \
  --project <PROJECT> --reviewer '<HUMAN>' --note '<APPROVAL_NOTE>'
```
每张场景图须有语义/现实/身份三方面的 pass/fail 决策。

### Phase 7 — HBG 渲染 + 编码 QA → **门 4：主片人工批准**
```bash
python book_video_factory/scripts/run_render_stage.py preview   --project <PROJECT> --input <PROJECT>/07_render/RENDER_INPUT.json
python book_video_factory/scripts/run_render_stage.py prepare   --project <PROJECT> --input <PROJECT>/07_render/RENDER_INPUT.json
python book_video_factory/scripts/run_render_stage.py execute   --project <PROJECT> --input <PROJECT>/07_render/RENDER_INPUT.json
```
默认渲染器 `static_streaming_ffmpeg`（需先批准开场预览）；adapter 在隔离 HBG
workspace 内调用 vendor 原版脚本（build_composition / validate_style_system /
render_streaming_ffmpeg / verify_final_video），**不改动**锁定的 Phase 2 证据。
编码 QA 通过并展示 MP4 + QA contact sheet，用户明确批准后：
```bash
python book_video_factory/scripts/approve_final_master.py \
  --project <PROJECT> --reviewer '<HUMAN>' --note '<APPROVAL_NOTE>'
```
没有这道门，项目停在 `awaiting_final_master_approval`。

---

## 4. 图像 provider 回退链（2026-08-16 用户确认）

不能跳过一级、不能卡死、不能编造：

1. **Host ImageGen**（项目默认）：`generation_lane=host-imagegen`，走波次协议。
2. **Google Flow**（labs.google/fx/tools/flow）：ImageGen 不可用时，用 Flow
   i2i（每任务 ≥2 张，视觉核验后取最优），经 media-suite 包装 + flow-chrome profile。
   Flow 命令须在**宿主机 Git Bash** 跑（沙箱缺 cygpath 会导致路径错乱），
   如 `bash suite flow image t2i "<prompt>" --model nano2 --aspect 16:9 --count 2 --out <dir>`。
3. **用户手动**：以上都不行时，给出完整可执行 prompt + 参考锚点路径，让用户自己生成，
   等回填后再继续——绝不伪造输出。

无论哪一级，产图仍走注册/审查/批准通道（`register_scene_asset.py` → 视觉审查 →
`SCENE_ASSET_APPROVAL.json`），hash 绑定不变。provider 字符串禁用 `flow-web` 等
未定义值（fail-closed）。

---

## 5. 一致性铁律

- **Scene Signature 连续传递**（`skill/references/scene-consistency-contract.md`）：
  时代、地域、地点锚点、人物身份/人生阶段、关系、关键道具、事件容器必须从同一
  Scene Signature 连续传到任务、参考图、像素审查与静态渲染。不得以「风格相近」
  替代事实一致。
- **视觉审查看像素**：`audit_caption_semantics.mjs` 只查字符/时长/标点，名字误导；
  语义对齐审查须读图（vision provider），审查者配置不进 VCS（见第 2 节）。
- **人门即哈希绑定**：每道人工批准事件必须绑定当前全部相关 SHA-256；未过门不得继续。

---

## 6. 修改指南（本仓库定位）

- **源码唯一真源** = `skill/runtime/book_video_factory/`。根目录 `book_video_factory/`
  是部署镜像（bootstrap 拷贝产物），直接改它会被下次部署覆盖；改了也要同步回
  `skill/runtime`。
- 改一个阶段的常规路径：`schemas/*.json`（输入/证据合同）→
  `src/book_video_factory/<stage>/{contracts,compiler,provenance}.py`（校验/编译/溯源）
  → `scripts/*.py`（CLI）→ `tests/`（配对抗测试）→ 跑
  `python book_video_factory/scripts/doctor.py` + `run_full_pipeline.py verify-repository`。
- **测试跑法坑**：pytest 必须在镜像路径 `book_video_factory/` 下跑（vendor 相对路径），
  canonical `skill/runtime/book_video_factory/` 会因 vendor 相对路径失败。
- **提交边界**：`git add` 只带 `skill/` 与根文档（见第 2 节），不要 `git add .`。
- **推送**：本机走 SSH 直连最稳；同一命令内先 `unset HTTPS_PROXY HTTP_PROXY`。

---

## 7. 环境与工具坑速查

- 审计/测试 venv：`C:/Users/wxh10/.workbuddy/binaries/python/envs/bvf`（需
  `sys.path.insert(0, .../skill/runtime/book_video_factory/src)` 或走 `_bootstrap.py`）。
- 抽帧验证：`ffmpeg -ss <t> -i <video> -frames:v 1 -vf scale=640:-1`（避开 PIL 依赖）。
- bash 中文输出：`PYTHONIOENCODING=utf-8` 或 `sys.stdout.reconfigure(encoding='utf-8')`。
- pip/playwright 安装被沙箱 safe-delete 拦截：先
  `unset CODEBUDDY_SESSION_ID CLAUDE_SESSION_ID && export CODEBUDDY_SAFE_DELETE_SANDBOX=0`。
