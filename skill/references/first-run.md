# 第一次运行

## 1. 初始化

```bash
python3 <SKILL_ROOT>/scripts/bootstrap_workspace.py --workspace <WORKSPACE>
python3 <SKILL_ROOT>/scripts/doctor.py --profile planning --json
```

确认工作区同时存在：

- `book_video_factory/`
- `vendor/hbg-life-simulation/UPSTREAM_LOCK.json`
- `vendor/hbg-life-simulation/scripts/build_narration.mjs`
- `vendor/hbg-life-simulation/scripts/render_streaming_ffmpeg.mjs`

## 2. 创建项目壳

```bash
python3 <SKILL_ROOT>/scripts/bootstrap_workspace.py \
  --workspace <WORKSPACE> \
  --slug <slug> \
  --book-title '<书名>' \
  --author '<作者>'
```

项目只能使用：

- `single-book`
- `classic-narrator-hbg-v1`
- `book-classic-narrator-hbg-16x9-v1`
- `host-imagegen`

## 3. 不要提前运行媒体生产

新项目中的 `PROJECT_SPEC.json`、`SCRIPT_SOURCE.md` 和 `STORYBOARD_BASE.json` 是草案壳。以下条件满足前，不得调用正式生图、配音或渲染：

- 可靠原著来源已登记；
- 事实和引语台账完成；
- Release 稿和 Audit 稿批准；
- `SCRIPT_LOCK.json` 已生成；
- 人物与视觉 Profile 已建立。

## 4. 执行顺序

```text
source → research → route → anchors → event cards → script → script approval
→ visual profile → anchor/LookDev approval → Edge TTS/VTT → storyboard
→ image tasks → picture lock → opening/audio/render → final QA → master approval
```

不要建立第二个状态文件。所有阶段通过现有 Workflow、Gates、Manifests 和 Hash 审批派生。
