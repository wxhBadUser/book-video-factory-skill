# Production acceptance

## Repository acceptance

```bash
python book_video_factory/scripts/run_full_pipeline.py verify-repository --root .
python -m pytest book_video_factory/tests skill/tests -q
```

Required results:

- Phase 0–7 scanners: zero critical findings.
- VFinal scanner: zero critical findings.
- HBG upstream lock: every file present and unchanged.
- Managed Runtime: byte-for-byte parity for active production modules.

## Project acceptance

A project may be called complete only when all conditions are true:

1. Level-A source evidence and locked script package are current.
2. Current script approval binds the complete frozen package.
3. HBG Bridge outputs pass native HBG validation.
4. Phase 3 anchors and exactly 12 LookDev images have explicit approval.
5. Phase 4 contains real continuous narration, raw VTT, display-restored captions, and a final audio-driven storyboard; no estimated timing exists.
6. Phase 5 has one production image task per final storyboard scene and one HBG-native motion per scene.
7. Every production scene image is a real 1920×1080 PNG with Host ImageGen evidence and approved semantic/reality/identity review.
8. The HBG render workspace is derived without modifying locked source evidence.
9. The final MP4 is H.264/yuv420p, 1920×1080, 30 fps, with AAC 48 kHz audio, correct duration, no unapproved black interval, no long digital silence, and an encoded-frame contact sheet.
10. `10_delivery_交付/FINAL_MASTER_APPROVAL.json` binds the current video, QA report, render manifest, and explicit human approval event.
11. `run_full_pipeline.py status` reports `complete`.

Passing synthetic tests does not prove an external Edge or ImageGen service was exercised. The project manifests explicitly distinguish external calls from fixture runners.
