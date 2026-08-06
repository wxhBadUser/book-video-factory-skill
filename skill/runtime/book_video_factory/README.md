# Book Video Factory Runtime

这个目录是名著单主播视频的内容与流程 Runtime。

它负责：

- 原著来源、章节研究、人物关系和事实台账；
- 三条叙事路线、命运锚点和事件卡；
- `script.narrator-essay.v1` 口播稿合同；
- 盲审、原创性检查和脚本 Hash 锁；
- 项目、Release、Manifest、Approval 和 Gate；
- ImageGen 任务规划与机器诊断；
- 调用仓库根目录 `vendor/hbg-life-simulation` 的生产能力。

它不复制 HBG 已经实现的 Edge TTS、VTT、宫格拆图、HyperFrames 和流式 FFmpeg。

## 初始化项目

```bash
python scripts/init_project.py \
  --warehouse ../book_video_warehouse \
  --slug old-man-and-the-sea \
  --book-title '老人与海' \
  --author '欧内斯特·海明威'
```

固定默认值：

- Style：`classic-narrator-hbg-v1`
- Release：`book-classic-narrator-hbg-16x9-v1`
- Generation lane：`host-imagegen`
- Workflow mode：`single-book`

## Phase 1 正式内容包

准备九个正式输入 JSON 后，先运行只读验证：

```bash
python scripts/build_content_package.py \
  --project <PROJECT> \
  --input-dir <INPUT_DIR> \
  --release-id <RELEASE_ID> \
  --validate-only
```

再移除 `--validate-only` 生成 `SCRIPT_RELEASE.md`、`SCRIPT_AUDIT.md`、`SCRIPT_LOCK.json`、`CONTENT_PACKAGE_MANIFEST.json` 等完整产物。Level C、未解析全文、伪造事件、未知 source ID 和 placeholder 内容全部 fail closed。

机器锁定不等于人工批准。`SCRIPT_LOCK.json` 在人工审阅前必须保持 `human_approved=false`。Phase 1 不生成音频、图片或视频。

## 依赖检查

```bash
python scripts/doctor.py --profile planning --json
python scripts/doctor.py --profile local-render --json
```

## 工作流状态

```bash
python scripts/workflow.py evaluate --project <PROJECT>
```

`project.json.status` 不是审批权威。实际状态由 Release 范围内的 Manifest、检查结果和 Hash 绑定审批派生。


## Phase 2 Book → HBG Bridge

只有当前脚本批准覆盖冻结 Release、Audit、指标、脚本锁和内容包 Manifest 后，才运行：

```bash
python scripts/build_hbg_bridge.py   --project <PROJECT>   --bridge-input <HBG_BRIDGE_INPUT.json>
```

Bridge 直接调用仓库根目录 vendored HBG 的 `init_project_style.mjs` 和 `build_storyboard_base.mjs`，不在 Python 中复制它们。成功后产生 HBG 原生 `SCRIPT_SOURCE.md`、`SCRIPT.md`、`CHARACTERS.md`、`PROJECT_SPEC.json`、`STORYBOARD_BASE.json` 和 `HBG_STYLE.json`，并以 Manifest 绑定全部输入输出 Hash。

**Phase 2 不生成音频、图片或视频。**
## Phase 3 视觉预生产

在 Phase 2 Bridge 和脚本批准保持当前有效的前提下：

```bash
python scripts/build_visual_stage.py --project <PROJECT> --visual-input <VISUAL_STAGE_INPUT.json>
python scripts/register_visual_asset.py --project <PROJECT> --task-id <TASK_ID> \
  --source <REAL_OUTPUT.png> --tool-call-id <REAL_TOOL_CALL_ID> \
  --prompt-sha256 <PROMPT_SHA256> \
  --style-reference-id <TASK_STYLE_REFERENCE_ID> \
  --identity-reference-task-id <REGISTERED_IDENTITY_TASK_ID>
python scripts/build_visual_review.py --project <PROJECT>
python scripts/approve_visual_stage.py --project <PROJECT> --release-id <RELEASE_ID> \
  --reviewer '<REVIEWER>' --decision-file <VISUAL_REVIEW_DECISION.json>
```

每个 `--style-reference-id` 和 `--identity-reference-task-id` 必须按任务 JSON 中的顺序逐项重复传入；无人物依赖的任务不传 identity 参数。登记器会把参考图、Profile 和已登记人物锚点的 Hash 固化为证据，但这仍是宿主调用声明，不是外部平台的密码学证明。

图片必须由宿主 host ImageGen 真实生成。Runtime 不实现图片 API，不伪造 tool call ID、文件、Hash 或人工批准。联系表直接调用 HBG `make_contact_sheet.sh`。Phase 3 不生成音频、VTT 或视频。
