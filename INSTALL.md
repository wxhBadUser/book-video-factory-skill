# Installation

## Required

- Python 3.11+
- Node.js
- FFmpeg and FFprobe
- Pillow (`python -m pip install -e book_video_factory` installs it)
- `edge-tts` for Phase 4 production audio
- Network access to the Edge speech service
- A Codex/ChatGPT host with ImageGen access for Phase 3 and Phase 6 images

## Optional renderer

- HyperFrames for the full HTML-composition renderer. Pin the exact version in `RENDER_INPUT.json`.
- Without HyperFrames, use the HBG streaming FFmpeg renderer and provide an approved opening preview video.

## Workspace installation

```bash
python -m pip install -e "book_video_factory[production]"
python skill/scripts/bootstrap_workspace.py --workspace ./workspace
python workspace/book_video_factory/scripts/doctor.py --profile production --json
```

The bootstrap mirrors the active Python Runtime and the pristine HBG vendor. It removes obsolete managed files but does not delete user projects.
