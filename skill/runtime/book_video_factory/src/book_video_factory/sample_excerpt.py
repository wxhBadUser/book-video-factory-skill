"""Hash-bound, no-rewrite excerpts for a short representative book-video sample.

The production script remains the approved long-form release.  A 45--90 second
sample must therefore be an explicit selection from that release, never a
silently shortened or rewritten substitute.  This module accepts only
one-based sentence ranges and derives every selected character from the frozen
``SCRIPT_PACKAGE.json``.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .gates import approval_covers_path, current_approvals
from .manifests import safe_project_output, sha256_file, write_immutable_json


class SampleExcerptError(RuntimeError):
    """The requested short sample is unapproved, unsafe, or not source exact."""


_SAMPLE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])")
_SPACE_RE = re.compile(r"\s+")
_REQUIRED_SCRIPT_APPROVAL_SUBJECTS = (
    "02_story_script_故事脚本/SCRIPT_RELEASE.md",
    "02_story_script_故事脚本/SCRIPT_AUDIT.md",
    "02_story_script_故事脚本/SCRIPT_METRICS.json",
    "02_story_script_故事脚本/SCRIPT_LOCK.json",
    "02_story_script_故事脚本/CONTENT_PACKAGE_MANIFEST.json",
)


@dataclass(frozen=True)
class SampleExcerptResult:
    status: str
    path: Path
    duration_seconds: float
    source_approval_event_id: str


@dataclass(frozen=True)
class SampleNarrationUnitsResult:
    status: str
    path: Path
    unit_count: int


def _load_object(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise SampleExcerptError(f"{label} is missing or unsafe")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SampleExcerptError(f"{label} is unreadable") from error
    if not isinstance(value, dict):
        raise SampleExcerptError(f"{label} must be an object")
    return value


def _require_current_script_approval(root: Path, release_id: str) -> dict[str, Any]:
    approval = current_approvals(root, release_id).get("script")
    if approval is None:
        raise SampleExcerptError("a current human script approval is required before selecting a sample")
    for relative in _REQUIRED_SCRIPT_APPROVAL_SUBJECTS:
        if not approval_covers_path(root, approval, root / relative):
            raise SampleExcerptError(f"script approval does not cover required subject: {relative}")
    event_id = approval.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        raise SampleExcerptError("current script approval has no event_id")
    return approval


def _sentences(text: Any, label: str) -> list[str]:
    if not isinstance(text, str) or not text.strip():
        raise SampleExcerptError(f"{label} text is empty")
    sentences = [part.strip() for part in _SENTENCE_SPLIT_RE.split(text) if part.strip()]
    if not sentences:
        raise SampleExcerptError(f"{label} has no sentences")
    return sentences


def _source_sections(package: Mapping[str, Any], release_id: str) -> list[tuple[str, str, list[str]]]:
    if package.get("release_id") != release_id:
        raise SampleExcerptError("script package release_id does not match the requested sample")
    script = package.get("script")
    if not isinstance(script, Mapping):
        raise SampleExcerptError("script package has no script")
    performance = script.get("performance_version")
    if not isinstance(performance, Mapping) or not isinstance(performance.get("sections"), list):
        raise SampleExcerptError("script package has no performance sections")
    result: list[tuple[str, str, list[str]]] = []
    seen: set[str] = set()
    for index, item in enumerate(performance["sections"]):
        if not isinstance(item, Mapping):
            raise SampleExcerptError(f"performance section {index} is invalid")
        section_id = item.get("section_id")
        function = item.get("narrative_function")
        if not isinstance(section_id, str) or not section_id or section_id in seen:
            raise SampleExcerptError("performance section IDs must be unique nonempty strings")
        if not isinstance(function, str) or not function:
            raise SampleExcerptError(f"performance section {section_id} has no narrative_function")
        seen.add(section_id)
        result.append((section_id, function, _sentences(item.get("text"), f"section {section_id}")))
    if not result:
        raise SampleExcerptError("script package has no performance sections")
    return result


def _validate_input(
    payload: Mapping[str, Any],
    *,
    release_id: str,
    script_package_sha256: str,
    script_release_sha256: str,
) -> tuple[str, float, float, float, list[dict[str, int | str]]]:
    required = {
        "schema_version", "release_id", "sample_id", "target_duration_seconds",
        "min_duration_seconds", "max_duration_seconds", "characters_per_minute",
        "source", "selection",
    }
    if set(payload) != required or payload.get("schema_version") != "sample-excerpt-input.v1":
        raise SampleExcerptError("sample input fields or schema_version are invalid")
    if payload.get("release_id") != release_id:
        raise SampleExcerptError("sample input release_id does not match the frozen package")
    sample_id = payload.get("sample_id")
    if not isinstance(sample_id, str) or _SAMPLE_ID_RE.fullmatch(sample_id) is None:
        raise SampleExcerptError("sample_id must be a safe lowercase identifier")
    numeric = {key: payload.get(key) for key in (
        "target_duration_seconds", "min_duration_seconds", "max_duration_seconds", "characters_per_minute"
    )}
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in numeric.values()):
        raise SampleExcerptError("sample duration and pace fields must be numbers")
    target = float(numeric["target_duration_seconds"])
    minimum = float(numeric["min_duration_seconds"])
    maximum = float(numeric["max_duration_seconds"])
    cpm = float(numeric["characters_per_minute"])
    if not (45.0 <= minimum <= target <= maximum <= 90.0):
        raise SampleExcerptError("sample duration bounds must stay within 45-90 seconds and contain the target")
    if not 160.0 <= cpm <= 280.0:
        raise SampleExcerptError("characters_per_minute must be within the professional narration range 160-280")
    source = payload.get("source")
    if not isinstance(source, Mapping) or set(source) != {"script_package_sha256", "script_release_sha256"}:
        raise SampleExcerptError("sample source bindings are invalid")
    if source.get("script_package_sha256") != script_package_sha256:
        raise SampleExcerptError("sample input has a stale script_package_sha256")
    if source.get("script_release_sha256") != script_release_sha256:
        raise SampleExcerptError("sample input has a stale script_release_sha256")
    raw_selection = payload.get("selection")
    if not isinstance(raw_selection, list) or not raw_selection:
        raise SampleExcerptError("sample selection must be a nonempty sentence-range list")
    selection: list[dict[str, int | str]] = []
    for index, item in enumerate(raw_selection):
        if not isinstance(item, Mapping) or set(item) != {"section_id", "sentence_start", "sentence_end"}:
            raise SampleExcerptError(f"selection[{index}] fields are invalid")
        section_id = item.get("section_id")
        start = item.get("sentence_start")
        end = item.get("sentence_end")
        if (
            not isinstance(section_id, str) or not section_id
            or isinstance(start, bool) or not isinstance(start, int)
            or isinstance(end, bool) or not isinstance(end, int)
            or start < 1 or end < start
        ):
            raise SampleExcerptError(f"selection[{index}] must use one-based inclusive sentence positions")
        selection.append({"section_id": section_id, "sentence_start": start, "sentence_end": end})
    return sample_id, target, minimum, maximum, selection


def build_sample_excerpt(project: Path, input_path: Path, output_path: Path | None = None) -> SampleExcerptResult:
    """Create one hash-bound no-rewrite sample excerpt after script approval."""

    root = project.expanduser().resolve()
    package_path = root / "02_story_script_故事脚本/SCRIPT_PACKAGE.json"
    release_path = root / "02_story_script_故事脚本/SCRIPT_RELEASE.md"
    package = _load_object(package_path, "script package")
    release_id = package.get("release_id")
    if not isinstance(release_id, str) or not release_id:
        raise SampleExcerptError("script package release_id is invalid")
    approval = _require_current_script_approval(root, release_id)
    input_file = safe_project_output(root, input_path)
    payload = _load_object(input_file, "sample input")
    package_sha = sha256_file(package_path)
    release_sha = sha256_file(release_path)
    sample_id, target, minimum, maximum, selection = _validate_input(
        payload,
        release_id=release_id,
        script_package_sha256=package_sha,
        script_release_sha256=release_sha,
    )
    sections = _source_sections(package, release_id)
    sentence_index: dict[tuple[str, int], tuple[int, str, str]] = {}
    sequence = 0
    for section_id, function, sentences in sections:
        for sentence_number, sentence in enumerate(sentences, start=1):
            sentence_index[(section_id, sentence_number)] = (sequence, function, sentence)
            sequence += 1
    selected: list[tuple[int, str, str, int, str]] = []
    for item in selection:
        section_id = str(item["section_id"])
        for number in range(int(item["sentence_start"]), int(item["sentence_end"]) + 1):
            record = sentence_index.get((section_id, number))
            if record is None:
                raise SampleExcerptError(f"selection references an unknown sentence: {section_id}#{number}")
            global_index, function, sentence = record
            selected.append((global_index, section_id, function, number, sentence))
    indexes = [item[0] for item in selected]
    if len(indexes) != len(set(indexes)):
        raise SampleExcerptError("sample selection repeats a source sentence")
    if indexes != sorted(indexes) or indexes != list(range(min(indexes), max(indexes) + 1)):
        raise SampleExcerptError("sample selection must be one contiguous source-sentence span")
    text = "".join(item[4] for item in selected)
    spoken_characters = len(_SPACE_RE.sub("", text))
    cpm = float(payload["characters_per_minute"])
    duration = round(spoken_characters / cpm * 60.0, 3)
    if duration < minimum or duration > maximum:
        raise SampleExcerptError(
            f"sample estimated duration {duration:.3f}s is outside the approved {minimum:.3f}-{maximum:.3f}s range"
        )
    event_id = str(approval["event_id"])
    result = {
        "schema_version": "sample-excerpt.v1",
        "release_id": release_id,
        "sample_id": sample_id,
        "source": {
            "script_package_path": package_path.relative_to(root).as_posix(),
            "script_package_sha256": package_sha,
            "script_release_path": release_path.relative_to(root).as_posix(),
            "script_release_sha256": release_sha,
            "script_approval_event_id": event_id,
        },
        "timing": {
            "target_duration_seconds": target,
            "min_duration_seconds": minimum,
            "max_duration_seconds": maximum,
            "characters_per_minute": cpm,
            "estimated_duration_seconds": duration,
        },
        "spoken_characters": spoken_characters,
        "selection": [
            {
                "source_sequence": index,
                "section_id": section_id,
                "narrative_function": function,
                "sentence_number": number,
                "text": sentence,
                "text_sha256": hashlib.sha256(sentence.encode("utf-8")).hexdigest(),
            }
            for index, section_id, function, number, sentence in selected
        ],
        "spoken_text": text,
        "integrity": {
            "selection_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "no_rewrite": True,
            "source_sentence_span": [indexes[0], indexes[-1]],
        },
    }
    target_path = safe_project_output(
        root,
        output_path or root / "02_story_script_故事脚本/SAMPLE_EXCERPT.json",
    )
    if target_path.exists():
        existing = _load_object(target_path, "existing sample excerpt")
        if existing != result:
            raise SampleExcerptError("existing sample excerpt differs; create a new approved sample release instead")
        return SampleExcerptResult("unchanged", target_path, duration, event_id)
    write_immutable_json(target_path, result)
    return SampleExcerptResult("created", target_path, duration, event_id)


def _current_source_sentence_records(
    root: Path,
    package: Mapping[str, Any],
    release_id: str,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for section_id, function, sentences in _source_sections(package, release_id):
        for number, sentence in enumerate(sentences, start=1):
            records.append({
                "source_sequence": len(records),
                "section_id": section_id,
                "narrative_function": function,
                "sentence_number": number,
                "text": sentence,
                "text_sha256": hashlib.sha256(sentence.encode("utf-8")).hexdigest(),
            })
    return records


def _verify_current_excerpt(root: Path, excerpt: Mapping[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    required = {
        "schema_version", "release_id", "sample_id", "source", "timing",
        "spoken_characters", "selection", "spoken_text", "integrity",
    }
    if set(excerpt) != required or excerpt.get("schema_version") != "sample-excerpt.v1":
        raise SampleExcerptError("sample excerpt fields or schema_version are invalid")
    release_id = excerpt.get("release_id")
    if not isinstance(release_id, str) or not release_id:
        raise SampleExcerptError("sample excerpt release_id is invalid")
    sample_id = excerpt.get("sample_id")
    if not isinstance(sample_id, str) or _SAMPLE_ID_RE.fullmatch(sample_id) is None:
        raise SampleExcerptError("sample excerpt sample_id is invalid")
    approval = _require_current_script_approval(root, release_id)
    source = excerpt.get("source")
    if not isinstance(source, Mapping):
        raise SampleExcerptError("sample excerpt source binding is invalid")
    package_path = root / "02_story_script_故事脚本/SCRIPT_PACKAGE.json"
    release_path = root / "02_story_script_故事脚本/SCRIPT_RELEASE.md"
    expected_source = {
        "script_package_path": package_path.relative_to(root).as_posix(),
        "script_package_sha256": sha256_file(package_path),
        "script_release_path": release_path.relative_to(root).as_posix(),
        "script_release_sha256": sha256_file(release_path),
        "script_approval_event_id": approval["event_id"],
    }
    if dict(source) != expected_source:
        raise SampleExcerptError("sample excerpt source approval or hashes are stale")
    package = _load_object(package_path, "script package")
    source_records = _current_source_sentence_records(root, package, release_id)
    selection = excerpt.get("selection")
    if not isinstance(selection, list) or not selection:
        raise SampleExcerptError("sample excerpt selection is empty")
    selected: list[dict[str, Any]] = []
    for item in selection:
        if not isinstance(item, Mapping):
            raise SampleExcerptError("sample excerpt selection record is invalid")
        sequence = item.get("source_sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or not 0 <= sequence < len(source_records):
            raise SampleExcerptError("sample excerpt source_sequence is invalid")
        if dict(item) != source_records[sequence]:
            raise SampleExcerptError("sample excerpt sentence is not an exact current source sentence")
        selected.append(dict(item))
    sequences = [item["source_sequence"] for item in selected]
    if sequences != list(range(sequences[0], sequences[-1] + 1)):
        raise SampleExcerptError("sample excerpt selection is not a contiguous source span")
    spoken = "".join(str(item["text"]) for item in selected)
    integrity = excerpt.get("integrity")
    if not isinstance(integrity, Mapping) or integrity.get("no_rewrite") is not True:
        raise SampleExcerptError("sample excerpt no_rewrite declaration is invalid")
    if (
        excerpt.get("spoken_text") != spoken
        or integrity.get("selection_text_sha256") != hashlib.sha256(spoken.encode("utf-8")).hexdigest()
        or integrity.get("source_sentence_span") != [sequences[0], sequences[-1]]
    ):
        raise SampleExcerptError("sample excerpt text integrity is stale")
    return sample_id, selected


def _emotion_hint(record: Mapping[str, Any], *, is_last: bool) -> str | None:
    text = str(record["text"])
    function = str(record["narrative_function"])
    if "死" in text or "离开" in text:
        return "sad"
    if is_last or function in {"revelation", "reinterpretation", "theory"}:
        return "reflective"
    return None


def build_sample_narration_units(
    project: Path,
    excerpt_path: Path,
    output_path: Path | None = None,
) -> SampleNarrationUnitsResult:
    """Derive exact sentence units for MiniMax performance planning.

    The resulting JSON is an input to ``build_narration_performance_plan.py``.
    It retains the display text unchanged and carries only performance metadata
    (emotion, pauses and grouping are computed downstream).
    """

    root = project.expanduser().resolve()
    excerpt_file = safe_project_output(root, excerpt_path)
    excerpt = _load_object(excerpt_file, "sample excerpt")
    sample_id, selected = _verify_current_excerpt(root, excerpt)
    units = []
    for index, record in enumerate(selected, start=1):
        text = str(record["text"])
        units.append({
            "unit_id": f"{sample_id}-u{index:03d}",
            "text": text,
            "spoken_text": text,
            "chapter_id": sample_id,
            "narrative_function": str(record["narrative_function"]),
            "emotion_hint": _emotion_hint(record, is_last=index == len(selected)),
            # A narrator quoting "老人说：..." is still a single-narrator
            # performance, not a role dialogue.  Marking punctuation as a
            # dialogue boundary would keep later sentences in one artificial
            # beat and flatten the emotional pacing.
            "is_dialogue_start": False,
            "is_dialogue_end": False,
            "is_impact": False,
            "is_reflection": _emotion_hint(record, is_last=index == len(selected)) == "reflective",
            "is_paragraph_start": index == 1,
        })
    output = {
        "schema_version": "sample-narration-units.v1",
        "release_id": excerpt["release_id"],
        "sample_excerpt_path": excerpt_file.relative_to(root).as_posix(),
        "sample_excerpt_sha256": sha256_file(excerpt_file),
        "sample_id": sample_id,
        "units": units,
    }
    target_path = safe_project_output(
        root,
        output_path or root / "04_audio/SAMPLE_NARRATION_UNITS.json",
    )
    if target_path.exists():
        existing = _load_object(target_path, "existing sample narration units")
        if existing != output:
            raise SampleExcerptError("existing sample narration units differ; create a new sample release instead")
        return SampleNarrationUnitsResult("unchanged", target_path, len(units))
    write_immutable_json(target_path, output)
    return SampleNarrationUnitsResult("created", target_path, len(units))
