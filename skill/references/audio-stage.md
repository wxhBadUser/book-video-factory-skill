# Phase 4：MiniMax/Legacy Edge、Provider VTT 与真实语音分镜

Phase 4 只处理连续旁白、发音替换、原始 VTT、显示字幕和真实语音驱动的最终分镜。它不生成图片、HyperFrames、FFmpeg 成片或最终 MP4。

## 先验条件

运行前必须满足：

- Phase 3 当前 `visual_anchor_lookdev` 人工批准有效；
- `SCRIPT.md`、`PROJECT_SPEC.json`、`HBG_STYLE.json`、`STORYBOARD_BASE.json` 未被修改；
- HBG Vendor 与 `UPSTREAM_LOCK.json` 完全一致；
- 新项目有当前 `04_audio/VOICE_FOUNDATION.json`，且 MiniMax voice_id 与输入一致；
- Python、Node、FFmpeg、ffprobe 可用；
- `minimax_required` 环境有 `MINIMAX_API_KEY`（国内端点还需 `MINIMAX_GROUP_ID`）；
- 只有明确标记 `legacy_edge` 的旧项目才要求 `edge-tts` 与 Edge 语音服务。

检查环境：

```bash
python3 <WORKSPACE>/book_video_factory/scripts/doctor.py --profile production --json
```

Doctor 返回 blocked 时不得伪造音频、不得伪造 VTT、不得用测试夹具冒充生产输出。

## 准备输入

项目初始化会生成：

```text
04_audio/AUDIO_STAGE_INPUT.example.json
04_audio/PRONUNCIATION_LEXICON.example.json
04_audio/STORYBOARD_AUDIO_PLAN.example.json
```

将前两份示例复制为正式文件：

```text
04_audio/AUDIO_STAGE_INPUT.json
04_audio/PRONUNCIATION_LEXICON.json
```

必须把示例中的零 Hash 替换为当前真实文件 Hash。发音词典中的显示文本和朗读辅助文本分离，辅助读音不得出现在最终字幕中。

## Pass A：生成真实连续音频和原始 VTT

```bash
python3 <WORKSPACE>/book_video_factory/scripts/run_audio_stage.py generate \
  --project <PROJECT_DIR> \
  --input <PROJECT_DIR>/04_audio/AUDIO_STAGE_INPUT.json \
  --lexicon <PROJECT_DIR>/04_audio/PRONUNCIATION_LEXICON.json \
  --provider minimax
```

成功后状态必须是：

```text
awaiting_audio_storyboard_plan
```

主要证据：

```text
assets/audio/
04_audio/provider_evidence/AUDIO_GENERATION_EVIDENCE.json
04_audio/NARRATION_PERFORMANCE_PLAN.json
04_audio/AUDIO_PRELIMINARY_MANIFEST.json
04_audio/AUDIO_STORYBOARD_GAPS.json
audio_meta.json
STORYBOARD.json
```

MiniMax provider 时间戳与对应 VTT 是新项目的时间真源。不得使用估算时间轴，
不得根据平均字数机械铺镜头。明确的 `legacy_edge` 项目仍使用 Edge VTT。

## Codex 编写真实语音分镜

Codex 必须读取：

- 冻结脚本；
- Phase 2 Beat；
- `audio_meta.json` 中的显示字幕与真实开始/结束时间；
- `AUDIO_STORYBOARD_GAPS.json`；
- 人物身份锚点；
- 每条字幕的真实开始和结束时间。

然后将完整计划保存到固定路径：

```text
04_audio/STORYBOARD_AUDIO_PLAN.json
```

普通镜头目标 3.2–5.5 秒；8–12 秒需要强情绪或复杂画面负载；超过 12 秒必须写明主动停留原因；超过 16 秒直接失败。zoom 或 pan 不能冒充新的视觉覆盖。

该镜头时长纪律适用于全书正式片分镜。45–90 秒视觉样片按 `references/scene-consistency-contract.md` 样片视觉密度契约规划多张同签名子镜：以观众不出戏和叙事需要为准（通常 4–6 张，是参考不是机械指标），不能因“同场景”而 60 秒量级只给一张图。

## Pass B：冻结最终真实音频时间线

```bash
python3 <WORKSPACE>/book_video_factory/scripts/run_audio_stage.py finalize \
  --project <PROJECT_DIR> \
  --plan <PROJECT_DIR>/04_audio/STORYBOARD_AUDIO_PLAN.json \
  --provider minimax
```

Pass B 必须复用 Pass A 的音频缓存。任何音频 Hash 改变都必须失败。成功后状态为：

```text
ready_for_image_task_planning
```

正式证据：

```text
04_audio/STORYBOARD_BASE.audio-final.json
04_audio/CAPTION_BINDINGS.json
04_audio/AUDIO_TIMELINE_AUDIT.json
04_audio/AUDIO_STAGE_MANIFEST.json
audio_meta.json
STORYBOARD.json
```

查询状态：

```bash
python3 <WORKSPACE>/book_video_factory/scripts/run_audio_stage.py status \
  --project <PROJECT_DIR> \
  --release-id <RELEASE_ID>
```

## 绝对禁止

- 不得伪造音频；
- 不得伪造 VTT；
- `minimax_required` 缺凭据或 provider 失败时必须阻断，绝不回退 Edge；
- `legacy_edge` 不得在没有 Edge TTS 时返回成功；
- 不得使用估算时间轴；
- 不得修改 HBG Vendor；
- 不得让发音辅助文本泄漏到显示字幕；
- 不得创建第二套 progress 状态文件；
- 不得在 Pass B 重新合成音频；
- 不得把测试 Fake Runner 当作生产 Provider；
- Phase 4 不生成图片、HyperFrames、FFmpeg 成片或最终 MP4。
