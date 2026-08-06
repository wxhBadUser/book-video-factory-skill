# Phase 1：正式内容大脑

Phase 1 的唯一职责是把可靠原著证据和经过人工创作的单主播文稿，编译成可审计、可重复、不可静默覆盖的正式内容包。

## 边界

Phase 1 结束于 `SCRIPT_LOCK.json` 和 `CONTENT_PACKAGE_MANIFEST.json`。在人工脚本批准前，**不得生成音频、图片或视频**。Phase 2 只负责批准后的 HBG 项目桥接；主角锚点生成与批准、Edge TTS、VTT、ImageGen 和渲染属于后续阶段。

机器锁定不等于人工批准。内容编译器只证明结构、来源、交叉引用、指标和 Hash 合同成立；它不能替代编辑判断，也不能把机器 PASS 写成 `human_approved=true`。

## 正式输入

在一个只含输入 JSON 的目录中准备：

- `source_manifest.json`
- `book_research.json`
- `creative_decision.json`
- `fate_anchors.json`
- `event_cards.json`
- `script_package.json`
- `content_quality_report.json`
- `originality_report.json`
- `blind_review.json`

正式包只接受来源覆盖充分的 Level A 研究。Level B 只能作为补充研究草案，Level C（摘要、模型记忆或无法核实的二手材料）必须阻断正式写作。

禁止 placeholder 内容。`derived candidate`、`待定`、`TODO`、空泛编号问题、虚构 source ID、自动补足候选事件等都必须失败，不能为了满足数量合同而生成假证据。

## 先验证，不写项目

```bash
python book_video_factory/scripts/build_content_package.py \
  --project <PROJECT_DIR> \
  --input-dir <INPUT_DIR> \
  --release-id <RELEASE_ID> \
  --validate-only
```

`--validate-only` 在临时项目中运行全部合同，不得向真实项目写任何文件。

验证包括：

1. 来源等级、文件存在性与实际可解析覆盖；
2. 20–40 个真实候选事件及其章节/事实来源；
3. 三条真正不同的叙事路线及证据引用；
4. 选中路线必须是满足条件的最高分路线；
5. 8–12 个命运锚点与完整 omission map；
6. 一锚一卡的事件卡和来源交集；
7. Performance、Release、Audit 三版相同 section graph；
8. Release 稿零工程标签；
9. 字数、估算时长、中点和理论占比；
10. 内容质量、原创性和盲审结果；
11. 九类上游输入的 SHA-256。

## 正式编译

```bash
python book_video_factory/scripts/build_content_package.py \
  --project <PROJECT_DIR> \
  --input-dir <INPUT_DIR> \
  --release-id <RELEASE_ID>
```

正式输出至少包含：

```text
01_research_资料搜集/
  SOURCE_MANIFEST.json
  BOOK_RESEARCH.json
  FACT_LEDGER.json
  CREATIVE_DECISION.json
  FATE_ANCHORS.json
  EVENT_CARDS.json

02_story_script_故事脚本/
  SCRIPT_PACKAGE.json
  SCRIPT_PERFORMANCE.md
  SCRIPT_RELEASE.md
  SCRIPT_AUDIT.md
  SCRIPT_METRICS.json
  CONTENT_QUALITY_REPORT.json
  ORIGINALITY_REPORT.json
  BLIND_REVIEW.json
  SCRIPT_LOCK.json
  CONTENT_PACKAGE_MANIFEST.json
```

编译使用临时 staging 目录。任何校验失败不得留下半套产物。完全相同的输入再次执行应返回 unchanged；输入变化或已有文件冲突必须拒绝覆盖。

## 脚本批准

`SCRIPT_LOCK.json` 必须保持：

```text
machine_locked = true
human_approved = false
next_stage_status = blocked_by_script_approval
```

只有用户或授权编辑对 `SCRIPT_RELEASE.md`、`SCRIPT_AUDIT.md`、指标和盲审结果完成审阅，才能通过现有 workflow/approval 系统记录人工批准。不得创建第二套 progress 或 approval 文件。

## Agent 写作职责

Python 不负责凭空写出名著口播稿。内容 Agent 必须依据：

```text
原著证据
→ 三条路线竞争
→ 命运锚点
→ 事件卡
→ Performance / Release / Audit 三版稿
```

编译器负责阻止 Agent 跳过证据、伪造事件、泄漏标签、漏掉 omission map 或把未批准文稿送入媒体生产。


# Phase 2：已批准内容包到 HBG 原生项目

Phase 2 只有一个职责：把 Phase 1 已冻结且已人工批准的内容包，确定性编译成 vendored HBG 可以直接读取的项目制品。它不重新写稿，不生成媒体，也不创建第二套状态系统。

## 交接前提

当前 `script` 批准必须属于同一 Release，并覆盖：

- `02_story_script_故事脚本/SCRIPT_RELEASE.md`
- `02_story_script_故事脚本/SCRIPT_AUDIT.md`
- `02_story_script_故事脚本/SCRIPT_METRICS.json`
- `02_story_script_故事脚本/SCRIPT_LOCK.json`
- `02_story_script_故事脚本/CONTENT_PACKAGE_MANIFEST.json`

Bridge 会重新计算内容包全部输出、阶段 Manifest、脚本锁、发布稿以及 HBG `UPSTREAM_LOCK.json` 中全部 Vendor 文件的 SHA-256。只有路径存在并不构成有效交接。

## Bridge 输入

项目初始化会生成 `HBG_BRIDGE_INPUT.example.json`。内容 Agent 必须据此提供：

- 系列和开头文案；
- Edge TTS 声线配置，但本阶段不调用 TTS；
- 覆盖全部冻结 Section 的章节分组；
- 人物不可变身份、服装、关系和锚点状态；
- 与冻结稿 cue 有序对应的语义 Beat；
- required / forbidden entities；
- 风险标记与 `single / 2x2` 路由。

高风险 Beat 必须使用 `single`。Bridge 只能校验和编译，不得自动补写人物特征、画面描述、字幕意图或风险判断。

## 执行

```bash
python book_video_factory/scripts/build_hbg_bridge.py   --project <PROJECT_DIR>   --bridge-input <HBG_BRIDGE_INPUT.json>
```

Bridge 在隔离 staging 项目中直接调用：

```text
vendor/hbg-life-simulation/scripts/init_project_style.mjs
vendor/hbg-life-simulation/scripts/build_storyboard_base.mjs
```

只有上游校验成功后才发布根制品。正式输出包括：

```text
SCRIPT_SOURCE.md
SCRIPT.md
CHARACTERS.md
PROJECT_SPEC.json
STORYBOARD_BASE.json
HBG_STYLE.json
02_story_script_故事脚本/script.narrator-essay.v1.json
02_story_script_故事脚本/HBG_BRIDGE_INPUT.json
02_story_script_故事脚本/HBG_BRIDGE_MANIFEST.json
manifests/stages/hbg-bridge-<release>.json
```

`SCRIPT_SOURCE.md` 必须与冻结 Release 稿逐字节一致。`SCRIPT.md` 只能添加 HBG 章节标题，移除标题后正文必须与冻结稿完全相同。相同输入重复运行返回 `unchanged`；不同输入、锁后篡改或用户编辑过的输出必须拒绝覆盖。

## Phase 2 非目标

**Phase 2 不生成音频、图片或视频。** 以下行为在本阶段被禁止：

- 调用 Edge TTS 或产生 VTT；
- 生成主角锚点或剧情图片；
- 运行 HyperFrames；
- 运行流式 FFmpeg；
- 宣称已经得到成片。
