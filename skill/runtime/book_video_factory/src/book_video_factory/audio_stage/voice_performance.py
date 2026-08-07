"""Voice Performance Plan generation (Part 8).

Turns plain narration captions into per-caption prosody so the single Edge-TTS
voice finally carries the emotional beat instead of one global rate. The planner
is pure Python (no network, no clock) and emits SSML overrides that the HBG
Edge-TTS CLI already accepts via its ``--ssml`` flag.

Heuristic rules (design §9.2):

* caption ends with ``。`` and opens a new paragraph  -> ``pause_ms_before = 400``
* caption contains ``死``                          -> ``rate=-15%``, ``grief-still``, ``pause_ms_after=800``
* caption contains ``哭``                          -> ``pitch=-1Hz`` + emphasize the word after 哭
* caption mode is Abstract (§6)                    -> ``rate=-5%``, ``pause_ms_after=600``
* caption crosses a scene boundary (beat switch)   -> ``pause_ms_before=600``
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

_RATE_RE = re.compile(r"^[+-](?:0|[1-9]\d{0,2})%$")
_PITCH_RE = re.compile(r"^[+-](?:0|[1-9]\d{0,3})Hz$")
_SSML_SAFE_RE = re.compile(r"[&<>]")

DEFAULT_RATE = "+0%"
DEFAULT_PITCH = "+0Hz"


def _escape(text: str) -> str:
    return _SSML_SAFE_RE.sub(lambda m: {"&": "&amp;", "<": "&lt;", ">": "&gt;"}[m.group(0)], str(text))


def _emphasis_after(text: str, marker: str) -> str:
    """Return the short word immediately following ``marker`` (for emphasis)."""

    index = text.find(marker)
    if index < 0 or index + len(marker) >= len(text):
        return ""
    tail = text[index + len(marker):]
    match = re.match(r"[\u4e00-\u9fffA-Za-z0-9]{1,4}", tail)
    return match.group(0) if match else ""


def build_caption_ssml(
    text: str,
    *,
    rate: str = DEFAULT_RATE,
    pitch: str = DEFAULT_PITCH,
    pause_ms_before: int = 0,
    pause_ms_after: int = 0,
    emphasis_words: Sequence[str] = (),
) -> str:
    """Build an Edge-TTS SSML string for one caption."""

    if not _RATE_RE.fullmatch(rate):
        raise ValueError(f"rate must use Edge syntax like +0%: {rate!r}")
    if not _PITCH_RE.fullmatch(pitch):
        raise ValueError(f"pitch must use Edge syntax like +0Hz: {pitch!r}")
    body = _escape(text)
    for word in emphasis_words:
        if word and word in text:
            safe_word = _escape(word)
            body = body.replace(
                safe_word, f'<emphasis level="moderate">{safe_word}</emphasis>'
            )
    prosody = f'<prosody rate="{rate}" pitch="{pitch}">{body}</prosody>'
    if pause_ms_before:
        prosody = f'<break time="{pause_ms_before}ms"/>{prosody}'
    if pause_ms_after:
        prosody = f'{prosody}<break time="{pause_ms_after}ms"/>'
    return (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        f'xml:lang="zh-CN">{prosody}</speak>'
    )


def plan_caption(
    caption_id: str,
    text: str,
    *,
    is_paragraph_start: bool = False,
    is_scene_boundary: bool = False,
    mode: str | None = None,
) -> dict[str, Any]:
    """Apply the heuristic rules to one caption and return its VPP entry."""

    caption = str(text or "").strip()
    rate = DEFAULT_RATE
    pitch = DEFAULT_PITCH
    pause_before = 0
    pause_after = 0
    emphasis: list[str] = []
    emotion = "neutral"

    if caption.endswith("。") and is_paragraph_start:
        pause_before = max(pause_before, 400)
    if "死" in caption:
        rate = "-15%"
        emotion = "grief-still"
        pause_after = max(pause_after, 800)
    if "哭" in caption:
        pitch = "-1Hz"
        word = _emphasis_after(caption, "哭")
        if word:
            emphasis.append(word)
    if mode == "Abstract":
        if rate == DEFAULT_RATE:
            rate = "-5%"
        pause_after = max(pause_after, 600)
    if is_scene_boundary:
        pause_before = max(pause_before, 600)

    ssml = build_caption_ssml(
        caption,
        rate=rate,
        pitch=pitch,
        pause_ms_before=pause_before,
        pause_ms_after=pause_after,
        emphasis_words=emphasis,
    )
    return {
        caption_id: {
            "rate": rate,
            "pitch": pitch,
            "pause_ms_before": pause_before,
            "pause_ms_after": pause_after,
            "emphasis_words": emphasis,
            "emotion_hint": emotion,
            "ssml_override": ssml,
        }
    }


def plan_voice_performance(
    captions: Sequence[Mapping[str, Any]],
    *,
    audio_meta_sha256: str,
    release_id: str,
    modes: Mapping[str, str] | None = None,
    manual_overrides: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Plan every caption and assemble the ``voice-performance-plan.v1`` object.

    ``captions`` is an ordered sequence of ``{"caption_id", "text", "is_paragraph_start", "is_scene_boundary"}``.
    ``modes`` maps ``caption_id -> mode`` (e.g. from ``classify_proposition``).
    ``manual_overrides`` lets a human override any auto-planned entry.
    """

    modes = modes or {}
    manual_overrides = manual_overrides or {}
    planned: dict[str, dict[str, Any]] = {}
    for item in captions:
        caption_id = str(item.get("caption_id"))
        entry = plan_caption(
            caption_id,
            item.get("text", ""),
            is_paragraph_start=bool(item.get("is_paragraph_start", False)),
            is_scene_boundary=bool(item.get("is_scene_boundary", False)),
            mode=modes.get(caption_id),
        )[caption_id]
        if caption_id in manual_overrides:
            entry.update({k: v for k, v in manual_overrides[caption_id].items() if k != "ssml_override"})
            if "ssml_override" in manual_overrides[caption_id]:
                entry["ssml_override"] = manual_overrides[caption_id]["ssml_override"]
        planned[caption_id] = entry
    return {
        "schema_version": "voice-performance-plan.v1",
        "release_id": release_id,
        "audio_meta_sha256": audio_meta_sha256,
        "status": "draft",
        "captions": planned,
    }
