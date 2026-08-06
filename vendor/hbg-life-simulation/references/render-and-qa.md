# Long render and encoded-media QA

Use this process for videos longer than four minutes or roughly 7,200 frames at 30 fps.

## Render preflight

Before disk preflight, run the style gate:

```bash
# Default landscape project
node scripts/init_project_style.mjs PROJECT_DIR

# Use only for an explicit portrait request
node scripts/init_project_style.mjs PROJECT_DIR --orientation portrait

node scripts/validate_style_system.mjs PROJECT_DIR
```

The default initialization is landscape 1920×1080. Portrait 1080×1920 requires the explicit flag. This gate rejects a declared orientation that contradicts the canvas, an opening with the wrong dimensions/orientation, an opening shorter than `bodyStart`, unsafe orientation-specific caption ratios, zero ASS box padding, and opening sequences without measurable flash/selected-life motion.

Run:

```bash
scripts/preflight_long_render.sh PROJECT_DIR 12
```

The 12 GiB default is a conservative floor for a high-quality 1080p, 8–12 minute image-heavy render. Treat it as a gate, not an estimate. Increase it for 4K, 60 fps, unusually detailed frames, or non-streaming capture.

The preflight script reports existing `renders/work-*` directories but never deletes them. Before removing one, match its render job ID to a completed, interrupted, or failed process and confirm no matching render process is alive. Remove only the exact abandoned directory. Never broadly clear `renders/`, a workspace root, source assets, or completed MP4 files.

## Long-form render

Run:

```bash
scripts/render_long_video.sh PROJECT_DIR renders/story-v1-complete.mp4
```

Defaults:

- high quality;
- `PRODUCER_ENABLE_CHUNKED_ENCODE=true`;
- `FFMPEG_ENCODE_TIMEOUT_MS=7200000`;
- hardware FFmpeg encoding on macOS;
- a new revisioned output path supplied by the caller.

Override only when needed:

```bash
HBG_RENDER_QUALITY=standard HBG_USE_GPU=0 HBG_MIN_FREE_GIB=16 \
  scripts/render_long_video.sh PROJECT_DIR renders/story-v2-complete.mp4
```

If capture succeeds but encoding times out, do not repeat the same command blindly. Enable chunked encoding, raise the timeout, and use a supported hardware encoder. If the renderer has already deleted its captured frames, the next run must recapture; make the corrected settings before restarting.

## Low-disk streaming fallback

Use the bundled streaming renderer when HyperFrames frame capture would exceed safe free disk space or when a measured test shows that the renderer materializes the entire long timeline as image files:

```bash
HBG_VALIDATE_ONLY=1 node scripts/render_streaming_ffmpeg.mjs PROJECT_DIR renders/story-v1.mp4
node scripts/render_streaming_ffmpeg.mjs PROJECT_DIR renders/story-v1.mp4
```

The project must already contain final `PROJECT_SPEC.json`, `STORYBOARD.json`, `HBG_STYLE.json`, `audio_meta.json`, all scene assets, an explicitly approved opening video, narration, BGM, ratchet SFX, and reveal audio. Story-specific paths and chapter metadata must come from the spec/audio metadata rather than renderer fallbacks. The script renders one short MP4 per still with deterministic zoom/pan, concatenates them, burns synchronized ASS captions and chapter labels, mixes the fixed-level audio bed, and streams the final H.264/AAC output without materializing every timeline frame.

Defaults come from the selected orientation template and the project's locked `HBG_STYLE.json`. Override paths or mix settings only when the project differs:

```bash
HBG_OPENING_VIDEO=qa/opening-final.mp4 \
HBG_BGM_AUDIO=assets/audio/bgm/story.m4a \
HBG_BGM_VOLUME=0.22 \
node scripts/render_streaming_ffmpeg.mjs PROJECT_DIR renders/story-v2.mp4
```

The streaming renderer removes its exact `ffmpeg-work-<output-name>` directory only after a non-empty final MP4 exists. Set `HBG_KEEP_STREAM_WORK=1` to preserve intermediates for diagnosis. On failure, preserve the work directory until the cause is known; remove only that exact inactive directory.

The streaming ASS style reads `HBG_STYLE.json`. Landscape defaults to 48 px white bold text, a semi-opaque warm-black box, 12 px box padding, and `MarginV=58` at 1920×1080. The validated portrait treatment uses the same 48 px type and box treatment but `MarginV=280` at 1080×1920. `BorderStyle=3` with zero outline/padding is forbidden because the background box disappears. Environment overrides are diagnostic escape hatches, not the normal authoring path.

## Encoded MP4 verification

Run with meaningful semantic timestamps:

```bash
scripts/verify_final_video.sh OUTPUT.mp4 QA_DIR \
  0.5:opening-lead \
  3.2:opening-flash \
  5.3:selected-title \
  449.5:hands-start \
  455:hands-mid \
  462:hands-end \
  572.2:phone \
  648.4:keyboard \
  663:ending
```

The script writes:

- `ffprobe.json` for resolution, codecs, frame rate, duration, frame count, and audio/video streams;
- `blackdetect.txt` for black intervals;
- `silencedetect.txt` for long silence despite the intended BGM bed;
- `ebur128.txt` for integrated loudness, loudness range, and true peak;
- named encoded PNG frames plus `contact-sheet.jpg`.

Open every suspicious contact-sheet panel at full resolution. For hand or prop corrections, inspect at least the start, midpoint, and end of the encoded motion. Passing source-image review is insufficient because zoom/pan can reveal cropped defects.

For the validated HBG mix, preserve at least 3 dB true-peak headroom. A constant BGM track should span the full composition and should not create one-second-or-longer digital silence in the final mix.
