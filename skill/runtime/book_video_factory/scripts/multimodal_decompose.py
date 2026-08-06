#!/usr/bin/env python3
"""多模态参考视频拆解：对单条参考视频生成 metadata/音频/关键帧/联系表/分析合同。

原则：
- 真实运行 FFmpeg/ffprobe，不伪造结果；
- 基于 SHA256 幂等：已处理且 Hash 一致则跳过；
- 缺失能力明确 TODO，不得假装完成（如全量 ASR 需 faster-whisper）；
- coverage 标记为 partial 时，不得声称完整视觉/音频 Gold Standard。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import _bootstrap  # noqa: F401

DEFAULT_KEYFRAME_INTERVAL_SECONDS = 30


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd: list[str]) -> tuple[int, str, str]:
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def ffprobe_metadata(video: Path) -> dict:
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(video),
    ]
    code, out, err = run(cmd)
    if code != 0:
        return {"error": err.strip(), "ffprobe_failed": True}
    return json.loads(out)


def extract_audio(video: Path, out_wav: Path) -> bool:
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le", str(out_wav),
    ]
    code, _, err = run(cmd)
    if code != 0:
        print(f"  audio extract failed: {err.strip()[:100]}")
        return False
    return True


def extract_sampled_keyframes(
    video: Path, out_dir: Path, interval_s: int, max_frames: int
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    dur_code, dur_out, _ = run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", str(video),
    ])
    try:
        duration = float(dur_out.strip())
    except ValueError:
        duration = 0.0
    if duration <= 0:
        return []
    timestamps = []
    t = interval_s
    while t < duration and len(timestamps) < max_frames:
        timestamps.append(t)
        t += interval_s
    frames: list[Path] = []
    for idx, ts in enumerate(timestamps):
        out = out_dir / f"frame_{idx:03d}_t{int(ts):04d}s.jpg"
        code, _, err = run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", str(ts), "-i", str(video),
            "-frames:v", "1", "-q:v", "3", str(out),
        ])
        if code == 0 and out.exists():
            frames.append(out)
    return frames


def build_contact_sheet(frames: list[Path], out_path: Path, cols: int = 4) -> bool:
    """用 Pillow 生成联系表（比 ffmpeg tile 滤镜更可靠）。"""
    if not frames:
        return False
    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore
    except ImportError:
        print("  contact sheet skipped: Pillow not available")
        return False
    thumbs: list[Image.Image] = []
    for f in frames:
        try:
            img = Image.open(f).convert("RGB")
            img.thumbnail((320, 320))
            thumbs.append(img)
        except Exception as exc:
            print(f"  skip frame {f}: {exc}")
    if not thumbs:
        return False
    rows = (len(thumbs) + cols - 1) // cols
    cell_w = thumbs[0].width
    cell_h = thumbs[0].height
    pad = 4
    sheet = Image.new(
        "RGB",
        (cols * cell_w + (cols + 1) * pad, rows * cell_h + (rows + 1) * pad),
        (0, 0, 0),
    )
    for idx, t in enumerate(thumbs):
        r, c = divmod(idx, cols)
        x = pad + c * (cell_w + pad)
        y = pad + r * (cell_h + pad)
        sheet.paste(t, (x, y))
    sheet.save(out_path, "JPEG", quality=85)
    return out_path.exists()


def compute_audio_metrics(wav: Path) -> dict:
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(wav),
    ]
    code, out, _ = run(cmd)
    if code != 0:
        return {"error": "ffprobe failed"}
    d = json.loads(out)
    fmt = d.get("format", {})
    streams = d.get("streams", [])
    s = streams[0] if streams else {}
    return {
        "duration_seconds": float(fmt.get("duration", 0)),
        "sample_rate_hz": int(s.get("sample_rate", 0)),
        "channels": s.get("channels"),
        "codec": s.get("codec_name"),
        "bit_rate_bps": int(fmt.get("bit_rate", 0)),
        "size_bytes": int(fmt.get("size", 0)),
    }


def already_processed(case_dir: Path, video_sha: str) -> bool:
    """幂等：若 evidence_manifest.json 的 source_sha256 与当前一致则跳过。"""
    ev = case_dir / "evidence_manifest.json"
    if not ev.is_file():
        return False
    try:
        d = json.loads(ev.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return d.get("source_sha256") == video_sha


def decompose(
    video_path: Path,
    case_id: str,
    case_dir: Path,
    verified_book: str,
    verified_author: str,
    evidence_summary: list[str],
    *,
    keyframe_interval: int = DEFAULT_KEYFRAME_INTERVAL_SECONDS,
    max_keyframes: int = 20,
    force: bool = False,
) -> dict:
    """对单条视频运行多模态拆解。"""
    case_dir.mkdir(parents=True, exist_ok=True)
    sha = sha256_file(video_path)

    if not force and already_processed(case_dir, sha):
        print(f"[skip] {case_id} already processed (sha256 matches)")
        return json.loads((case_dir / "evidence_manifest.json").read_text(encoding="utf-8"))

    print(f"[process] {case_id} sha256={sha[:16]}")

    # 1. metadata
    meta = ffprobe_metadata(video_path)
    (case_dir / "metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # 2. audio
    audio_dir = case_dir / "audio"
    audio_dir.mkdir(exist_ok=True)
    wav = audio_dir / "narration.wav"
    extract_audio(video_path, wav)
    metrics = {}
    if wav.exists():
        metrics = compute_audio_metrics(wav)
        (audio_dir / "audio_metrics.json").write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    # 3. sampled keyframes + contact sheet
    kf_dir = case_dir / "keyframes"
    frames = extract_sampled_keyframes(video_path, kf_dir, keyframe_interval, max_keyframes)
    sheet = case_dir / "contact_sheet.jpg"
    build_contact_sheet(frames, sheet)

    # 4. analysis stubs (evidence-based minimum; full annotation requires ASR + manual review)
    analysis_dir = case_dir / "analysis"
    analysis_dir.mkdir(exist_ok=True)
    structural = (
        f"# {verified_book} — Structural Annotation (evidence stub)\n\n"
        f"## Provenance\n"
        f"- Video SHA-256: `{sha}`\n"
        f"- Verified book: {verified_book}\n"
        f"- Verified author: {verified_author}\n"
        f"- Evidence: {evidence_summary}\n\n"
        "## Status\n"
        "- This file is an evidence-derived stub.\n"
        "- Full structural annotation requires: ASR transcript (faster-whisper), "
        "shot-list (scene detection), and human review.\n"
        "- Marked `evidence_status: video_transcript_pending` until ASR completes.\n"
    )
    (analysis_dir / "structural_annotation.md").write_text(structural, encoding="utf-8")
    visual = (
        f"# {verified_book} — Visual Annotation (evidence stub)\n\n"
        f"- Sampled keyframes: {len(frames)} frames @ {keyframe_interval}s interval\n"
        "- Contact sheet: contact_sheet.jpg\n"
        "- Visual grammar inferences require full shot list + human review.\n"
        "- evidence_status: partial_keyframes_only\n"
    )
    (analysis_dir / "visual_annotation.md").write_text(visual, encoding="utf-8")
    audio_note = (
        f"# {verified_book} — Audio Annotation (evidence stub)\n\n"
        f"- Extracted narration WAV: 16kHz mono PCM (ASR-ready)\n"
        f"- Audio metrics: {metrics}\n"
        "- Emotion curve + rhythm_30s.csv require faster-whisper ASR + energy analysis.\n"
        "- evidence_status: audio_extracted_asr_pending\n"
    )
    (analysis_dir / "audio_annotation.md").write_text(audio_note, encoding="utf-8")

    # 5. evidence_manifest
    em = {
        "schema_version": "evidence-manifest.v1",
        "case_id": case_id,
        "verified_book": verified_book,
        "verified_author": verified_author,
        "source_filename": video_path.name,
        "source_sha256": sha,
        "decomposed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "evidence": evidence_summary,
        "artifacts": {
            "metadata_json": "metadata.json",
            "audio_narration_wav": "audio/narration.wav" if wav.exists() else None,
            "audio_metrics_json": "audio/audio_metrics.json" if metrics else None,
            "keyframes_dir": f"keyframes/ ({len(frames)} frames)",
            "contact_sheet_jpg": "contact_sheet.jpg" if sheet.exists() else None,
            "structural_annotation": "analysis/structural_annotation.md",
            "visual_annotation": "analysis/visual_annotation.md",
            "audio_annotation": "analysis/audio_annotation.md",
        },
        "evidence_status": "video_extracted_asr_pending",
        "todo": [
            "Run faster-whisper ASR → transcript_raw.srt + transcript_clean.md + subtitle_units.csv",
            "Run scene detection → shot_list.csv",
            "Compute emotion curve from ASR + audio energy → analysis/emotion_curve.json",
            "Compute style fingerprint (luma/palette/saturation) → analysis/style_fingerprint.json",
            "Human review pass before promoting to gold standard",
        ],
    }
    (case_dir / "evidence_manifest.json").write_text(
        json.dumps(em, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[done] {case_id} -> {case_dir}")
    return em


def main() -> int:
    parser = argparse.ArgumentParser(description="多模态参考视频拆解")
    parser.add_argument("--ref-dir", default=str(Path(__file__).resolve().parents[3] / "book_video_warehouse" / "reference_cases"))
    parser.add_argument("--identity-map", default="")
    parser.add_argument("--video-source-dir", default=str(Path(__file__).resolve().parents[3] / "ref"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    ref_dir = Path(args.ref_dir)
    identity_map_path = (
        Path(args.identity_map)
        if args.identity_map
        else ref_dir / "reference_video_identity_map.json"
    )
    if not identity_map_path.is_file():
        print(f"identity map not found: {identity_map_path}")
        return 1
    imap = json.loads(identity_map_path.read_text(encoding="utf-8"))
    ref_src = Path(args.video_source_dir)

    n_done = 0
    for v in imap["videos"]:
        case_id = v["case_id"]
        video_path = ref_src / v["original_filename"]
        if not video_path.is_file():
            print(f"[miss] {case_id}: source video not at {video_path}")
            continue
        case_dir = ref_dir / case_id
        decompose(
            video_path=video_path,
            case_id=case_id,
            case_dir=case_dir,
            verified_book=v["verified_book_title"],
            verified_author=v["verified_author"],
            evidence_summary=v["evidence"],
            force=args.force,
        )
        n_done += 1
    print(f"decomposed {n_done} videos; coverage={imap.get('multimodal_reference_coverage')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())