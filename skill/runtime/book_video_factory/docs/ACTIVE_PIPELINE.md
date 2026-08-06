# Active Pipeline

唯一活跃路线是 `classic-narrator-hbg-v1`：内容 Runtime 负责研究、口播稿与状态闸门，HBG Vendor 负责 Edge TTS/VTT、开头、字幕、运动、音频混合和长片渲染。

参见仓库根目录：

- `docs/architecture/BOOK_HBG_FUSION_BLUEPRINT_V1.md`
- `docs/architecture/UPSTREAM_REUSE_MANIFEST_V1.json`
- `vendor/hbg-life-simulation/UPSTREAM_LOCK.json`


## 已批准内容交接

Phase 1 产物通过现有 `script` 审批后，由 `scripts/build_hbg_bridge.py` 编译为 HBG 原生项目制品。Bridge 只调用 vendored HBG 初始化器和分镜校验器，不生成音频、图片或视频，不创建第二套状态权威。
## 视觉锚点与 LookDev

Phase 3 使用 `scripts/build_visual_stage.py` 编译每书视觉 Profile、锚点任务和恰好 12 张 LookDev 任务。Codex 调用 host ImageGen 后，必须通过 `register_visual_asset.py` 登记真实输出，再通过 `build_visual_review.py` 直接调用 HBG `make_contact_sheet.sh`。只有 `approve_visual_stage.py` 记录的当前 `visual_anchor_lookdev` 人工批准可以解锁 Edge TTS。Phase 3 不生成音频、VTT 或视频。

## 连续音频、VTT 与真实语音分镜

Phase 4 由 `scripts/run_audio_stage.py generate` 和 `finalize` 两步组成。Pass A 在 Phase 3 当前视觉批准有效后直接复用 HBG `build_narration.mjs` 生成连续 Edge TTS、原始 VTT 和显示字幕；Pass B 只接受 Codex 写入固定路径的 `04_audio/STORYBOARD_AUDIO_PLAN.json`，复用原音频缓存并生成最终字幕绑定与真实时间线。没有 Edge TTS、网络或真实媒体证据时必须停止。Phase 4 不生成图片、HyperFrames、FFmpeg 成片或最终 MP4。
