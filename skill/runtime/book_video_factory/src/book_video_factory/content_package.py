"""Compile a validated, hash-bound Phase 1 content package.

The compiler consumes agent-authored artifacts, validates the complete evidence
chain, and writes one immutable machine-lockable package. It does not generate
prose, audio, images, or video.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .blind_review import (
    BlindReviewError, REVIEWER_LITERARY, REVIEWER_RETENTION,
    merge_blind_reviews, new_review,
)
from .book_research import BookResearch
from .editorial_validation import validate_editorial_graph
from .fact_ledger import FactLedger
from .manifests import sha256_file, write_stage_manifest
from .narrator_essay_contracts import validate_content_quality_report
from .originality_check import normalize
from .script_lock_gate import evaluate_script_lock
from .script_metrics import compute_script_metrics
from .style_profiles import project_workflow
from .source_ingestion import (
    FULL_TEXT_MIN_CHARS,
    SourceLevel,
    build_source_sanitization_report,
)


class ContentPackageError(RuntimeError):
    """The package cannot be compiled from the supplied evidence."""


class ContentPackageConflict(ContentPackageError):
    """A different user or release artifact already occupies a target path."""


@dataclass(frozen=True)
class ContentPackageResult:
    status: str
    package_digest: str
    manifest_path: Path
    stage_manifest_path: Path


_INPUT_NAMES = (
    "source_manifest", "research", "creative_decision", "fate_anchors",
    "event_cards", "script", "quality", "originality", "blind_review",
)


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _pretty_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_object(value: Any) -> str:
    return _sha_bytes(_canonical_json_bytes(value))


def _book_research(payload: Mapping[str, Any]) -> BookResearch:
    try:
        source_level = SourceLevel(str(payload["source_level"]))
        ledger_payload = payload["fact_ledger"]
        entries = ledger_payload["entries"] if isinstance(ledger_payload, Mapping) else []
        return BookResearch(
            book_title=str(payload["book_title"]),
            author=str(payload["author"]),
            source_level=source_level,
            research_status=str(payload["research_status"]),
            chapter_notes=list(payload["chapter_notes"]),
            character_map=list(payload["character_map"]),
            event_candidates=list(payload["event_candidates"]),
            fact_ledger=FactLedger(entries=list(entries)),
            research_summary=str(payload.get("research_summary", "")),
            coverage=str(payload.get("coverage", "")),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ContentPackageError(f"invalid research payload: {error}") from error


def _safe_relative_path(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ContentPackageError(f"{label} must be a nonempty relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ContentPackageError(f"{label} must stay inside the project")
    return path


def _validate_source_manifest(
    source: Mapping[str, Any], research: BookResearch, source_root: Path
) -> None:
    if source.get("source_level") != "A" or source.get("research_status") not in {"ready", "complete"}:
        raise ContentPackageError("formal package requires a ready source level A manifest")
    if source.get("evidence_level") != source.get("source_level"):
        raise ContentPackageError("source manifest must separate matching evidence_level and source_level")
    if not isinstance(source.get("rights_status"), str) or not source.get("rights_status"):
        raise ContentPackageError("source manifest requires rights_status")
    if not isinstance(source.get("public_release_allowed"), bool):
        raise ContentPackageError("source manifest requires boolean public_release_allowed")
    if not isinstance(source.get("rights_evidence"), list):
        raise ContentPackageError("source manifest requires rights_evidence")
    if source.get("public_release_allowed") is True and source.get("rights_status") != "cleared":
        raise ContentPackageError("public_release_allowed requires rights_status=cleared")
    if source.get("is_full_text") is not True or source.get("coverage") != "full_text_available":
        raise ContentPackageError("formal package requires extracted full-text coverage")
    if source.get("book_title") != research.book_title or source.get("author") != research.author:
        raise ContentPackageError("source manifest book identity does not match research")
    files = source.get("files")
    if not isinstance(files, list) or not files:
        raise ContentPackageError("source manifest files must be nonempty")

    base = source_root.expanduser().resolve()
    source_dir = (base / _safe_relative_path(source.get("source_dir"), "source_dir")).resolve()
    try:
        source_dir.relative_to(base)
    except ValueError as error:
        raise ContentPackageError("source_dir escapes the project") from error

    full_text_count = 0
    for index, item in enumerate(files):
        if not isinstance(item, Mapping):
            raise ContentPackageError(f"source file {index} must be an object")
        relative = _safe_relative_path(item.get("path"), f"source file {index}.path")
        path = (source_dir / relative).resolve()
        try:
            path.relative_to(source_dir)
        except ValueError as error:
            raise ContentPackageError(f"source file {index} escapes source_dir") from error
        if not path.is_file():
            raise ContentPackageError(f"source file is missing: {path}")
        expected_size = item.get("size")
        if not isinstance(expected_size, int) or isinstance(expected_size, bool) or expected_size < 0:
            raise ContentPackageError(f"source file {index} requires a nonnegative size")
        if path.stat().st_size != expected_size:
            raise ContentPackageError(f"source file size mismatch: {path}")
        digest = item.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ContentPackageError(f"source file {index} requires a sha256 digest")
        if sha256_file(path) != digest:
            raise ContentPackageError(f"source file hash mismatch: {path}")
        if item.get("kind") == "full_text":
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as error:
                raise ContentPackageError(f"full-text source is not readable UTF-8: {path}") from error
            if len(text) < FULL_TEXT_MIN_CHARS:
                raise ContentPackageError(f"full-text source is too short: {path}")
            full_text_count += 1
    if full_text_count < 1:
        raise ContentPackageError("formal source manifest requires at least one extracted full_text file")


def _validate_script_sources(script: Mapping[str, Any], research: BookResearch) -> None:
    known = {chapter["chapter_id"] for chapter in research.chapter_notes} | {
        entry["id"] for entry in research.fact_ledger.entries
    }
    reinterpretation = script.get("reinterpretation_source_ids")
    if not isinstance(reinterpretation, list) or not reinterpretation:
        raise ContentPackageError("script reinterpretation_source_ids must be nonempty")
    unknown = sorted(set(reinterpretation) - known)
    if unknown:
        raise ContentPackageError(f"script references unknown reinterpretation sources: {unknown}")
    audit = script.get("audit_version")
    sections = audit.get("sections") if isinstance(audit, Mapping) else None
    if not isinstance(sections, list):
        raise ContentPackageError("audit_version.sections must be an array")
    for section in sections:
        source_ids = section.get("source_ids") if isinstance(section, Mapping) else None
        if not isinstance(source_ids, list) or not source_ids:
            raise ContentPackageError("every audit section requires source_ids")
        unknown = sorted(set(source_ids) - known)
        if unknown:
            raise ContentPackageError(f"audit section references unknown sources: {unknown}")


def _validate_quality_evidence(quality: Mapping[str, Any]) -> None:
    validate_content_quality_report(dict(quality))
    items = quality.get("items")
    if not isinstance(items, Mapping) or len(items) != 15:
        raise ContentPackageError("quality report must contain exactly fifteen scored items")
    for key, value in items.items():
        if not isinstance(key, str) or not key.strip():
            raise ContentPackageError("quality item names must be nonempty")
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 5:
            raise ContentPackageError(f"quality item {key} must be an integer 0..5")
    expected_total = sum(items.values())
    if quality.get("total") != expected_total:
        raise ContentPackageError(
            f"quality total must equal the item sum: {expected_total}"
        )
    if quality.get("max_total") != 75 or expected_total > 75:
        raise ContentPackageError("quality max_total must be 75")
    if quality.get("human_review_required") is not True:
        raise ContentPackageError("quality report must require human review")
    if not isinstance(quality.get("blocking_issues"), list):
        raise ContentPackageError("quality blocking_issues must be an array")


def _validate_originality_evidence(
    originality: Mapping[str, Any], release_text: str
) -> None:
    normalized = normalize(release_text)
    expected_sha = _sha_bytes(normalized.encode("utf-8"))
    if originality.get("candidate_sha256") != expected_sha:
        raise ContentPackageError("originality report does not bind to the release text")
    if originality.get("candidate_chars") != len(normalized):
        raise ContentPackageError("originality candidate_chars do not match release text")
    limit = originality.get("max_consecutive_chars")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ContentPackageError("originality max_consecutive_chars is invalid")
    corpus_sources = originality.get("corpus_sources")
    per_source = originality.get("per_source")
    corpus_hashes = originality.get("corpus_hashes")
    if not isinstance(corpus_sources, list) or not corpus_sources:
        raise ContentPackageError("originality report requires a nonempty comparison corpus")
    if not isinstance(per_source, Mapping) or set(per_source) != set(corpus_sources):
        raise ContentPackageError("originality per_source must cover the comparison corpus")
    if not isinstance(corpus_hashes, Mapping) or set(corpus_hashes) != set(corpus_sources):
        raise ContentPackageError("originality corpus_hashes must cover the comparison corpus")
    if not all(isinstance(value, str) and len(value) == 64 for value in corpus_hashes.values()):
        raise ContentPackageError("originality corpus hashes are invalid")
    violations = originality.get("violations")
    longest = originality.get("longest_overlap")
    if not isinstance(violations, list) or not isinstance(longest, int):
        raise ContentPackageError("originality overlap evidence is invalid")
    expected_pass = not violations and longest <= limit
    if originality.get("passed") is not expected_pass:
        raise ContentPackageError("originality passed flag contradicts overlap evidence")


def _validate_blind_review_evidence(blind_review: Mapping[str, Any]) -> None:
    reviews = blind_review.get("reviews")
    if not isinstance(reviews, list) or len(reviews) != 2:
        raise ContentPackageError("blind review requires two actual review records")
    normalized_reviews: list[dict[str, Any]] = []
    try:
        for index, review in enumerate(reviews):
            if not isinstance(review, Mapping):
                raise ContentPackageError(f"blind review record {index} must be an object")
            normalized_reviews.append(new_review(
                str(review.get("reviewer_id", "")),
                review.get("scores") or {},
                verdict=str(review.get("verdict", "")),
                notes=str(review.get("notes", "")),
                blocking_issues=review.get("blocking_issues") or [],
            ))
        merged = merge_blind_reviews(normalized_reviews)
    except BlindReviewError as error:
        raise ContentPackageError(f"invalid blind review evidence: {error}") from error
    required_reviewers = {REVIEWER_LITERARY, REVIEWER_RETENTION}
    if set(merged["reviewers"]) != required_reviewers:
        raise ContentPackageError("blind review must use the literary and retention reviewers")
    comparisons = {
        "reviewer_count": merged["reviewer_count"],
        "reviewers": merged["reviewers"],
        "independent": merged["independent"],
        "has_disagreement": merged["has_disagreement"],
        "disagreements": merged["disagreements"],
        "verdict": merged["verdict"],
        "blocking_issues": merged["blocking_issues"],
    }
    for key, expected in comparisons.items():
        if blind_review.get(key) != expected:
            raise ContentPackageError(
                f"blind review summary contradicts its records: {key}"
            )


def render_script_markdown(title: str, version: Mapping[str, Any], *, mode: str) -> str:
    lines = [f"# {title}", ""]
    for section in version["sections"]:
        lines.append(f"## {section['section_id']}｜{section['narrative_function']}")
        lines.append("")
        if mode == "performance" and section.get("direction"):
            lines.append(
                "> 表演参数：`" + json.dumps(section["direction"], ensure_ascii=False, sort_keys=True) + "`"
            )
            lines.append("")
        if mode == "audit":
            lines.append("> 来源：" + "、".join(section.get("source_ids", [])))
            lines.append("")
        lines.append(section["text"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _existing_manifest(project: Path) -> dict[str, Any] | None:
    path = project / "02_story_script_故事脚本" / "CONTENT_PACKAGE_MANIFEST.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContentPackageConflict(f"existing package manifest is unreadable: {path}") from error
    if not isinstance(value, dict):
        raise ContentPackageConflict(f"existing package manifest is invalid: {path}")
    return value


def _verify_existing_package(
    project: Path, manifest: Mapping[str, Any], package_digest: str
) -> Path:
    if manifest.get("package_digest") != package_digest:
        raise ContentPackageConflict("a different content package already exists for this project")
    hashes = manifest.get("output_hashes")
    if not isinstance(hashes, Mapping) or not hashes:
        raise ContentPackageConflict("existing package manifest has no output hashes")
    for relative_value, expected in hashes.items():
        relative = _safe_relative_path(relative_value, "existing output path")
        path = (project / relative).resolve()
        try:
            path.relative_to(project)
        except ValueError as error:
            raise ContentPackageConflict("existing output path escapes project") from error
        if not path.is_file():
            raise ContentPackageConflict(f"existing output is missing: {relative.as_posix()}")
        if not isinstance(expected, str) or sha256_file(path) != expected:
            raise ContentPackageConflict(
                f"existing output hash mismatch: {relative.as_posix()}"
            )
    stage_relative = _safe_relative_path(
        manifest.get("stage_manifest_path"), "stage_manifest_path"
    )
    stage_path = (project / stage_relative).resolve()
    if not stage_path.is_file():
        raise ContentPackageConflict(
            f"existing stage manifest is missing: {stage_relative.as_posix()}"
        )
    expected_stage_hash = manifest.get("stage_manifest_sha256")
    if (
        not isinstance(expected_stage_hash, str)
        or len(expected_stage_hash) != 64
        or sha256_file(stage_path) != expected_stage_hash
    ):
        raise ContentPackageConflict("existing stage manifest hash mismatch")
    return stage_path


def compile_content_package(
    project: Path,
    *,
    source_manifest: Mapping[str, Any],
    research: Mapping[str, Any],
    creative_decision: Mapping[str, Any],
    fate_anchors: Mapping[str, Any],
    event_cards: Mapping[str, Any],
    script: Mapping[str, Any],
    quality: Mapping[str, Any],
    originality: Mapping[str, Any],
    blind_review: Mapping[str, Any],
    release_id: str,
    source_root: Path | None = None,
) -> ContentPackageResult:
    """Validate and atomically publish the Phase 1 package."""
    root = project.expanduser().resolve()
    if not (root / "project.json").is_file():
        raise ContentPackageError(f"not an initialized project: {root}")
    if not release_id.strip():
        raise ContentPackageError("release_id is required")

    inputs = {
        "source_manifest": dict(source_manifest), "research": dict(research),
        "creative_decision": dict(creative_decision), "fate_anchors": dict(fate_anchors),
        "event_cards": dict(event_cards), "script": dict(script), "quality": dict(quality),
        "originality": dict(originality), "blind_review": dict(blind_review),
    }
    input_hashes = {name: _sha_object(inputs[name]) for name in _INPUT_NAMES}
    package_digest = _sha_object({"release_id": release_id, "input_hashes": input_hashes})

    existing = _existing_manifest(root)
    if existing is not None:
        stage_path = _verify_existing_package(root, existing, package_digest)
        return ContentPackageResult(
            status="unchanged", package_digest=package_digest,
            manifest_path=root / "02_story_script_故事脚本/CONTENT_PACKAGE_MANIFEST.json",
            stage_manifest_path=stage_path,
        )

    try:
        research_obj = _book_research(research)
        effective_source_root = source_root or root
        _validate_source_manifest(source_manifest, research_obj, effective_source_root)
        source_sanitization_report = build_source_sanitization_report(
            dict(source_manifest), effective_source_root
        )
        if (
            source_sanitization_report["rights_status"] != source_manifest["rights_status"]
            or source_sanitization_report["public_release_allowed"] is not source_manifest["public_release_allowed"]
        ):
            raise ContentPackageError("source rights manifest disagrees with SOURCE_SANITIZATION_REPORT evidence")
        graph_report = validate_editorial_graph(
            research_obj, creative_decision, fate_anchors, event_cards
        )
        _validate_script_sources(script, research_obj)
        metrics = compute_script_metrics(script)
        _validate_quality_evidence(quality)
        _validate_originality_evidence(
            originality, str(script["release_version"]["text"])
        )
        _validate_blind_review_evidence(blind_review)
        source_gate = {
            "level": source_manifest.get("source_level"),
            "is_full_text": source_manifest.get("is_full_text"),
            "research_status": source_manifest.get("research_status"),
        }
        qualification_scope = str(
            project_workflow(root).get("qualification_scope", "production")
        )
        machine_gate = evaluate_script_lock(
            source=source_gate, metrics=metrics, quality=quality,
            originality=originality, blind_review=blind_review,
            blocking_issues=list(quality.get("blocking_issues", [])),
            human_approved=False,
            qualification_scope=qualification_scope,
        )
        if not machine_gate["script_locked"]:
            raise ContentPackageError(
                f"script lock gate failed: {machine_gate['failed_checks']}"
            )
    except ContentPackageError:
        raise
    except Exception as error:
        raise ContentPackageError(str(error)) from error

    release_text = script["release_version"]["text"]
    lock_payload = {
        "schema_version": "script-lock.v1", "release_id": release_id,
        "machine_locked": True, "human_approved": False,
        "next_stage_status": "blocked_by_script_approval",
        "release_text_sha256": _sha_bytes(release_text.encode("utf-8")),
        "input_hashes": input_hashes, "package_digest": package_digest,
        "machine_gate": machine_gate,
        "qualification_scope": qualification_scope,
    }
    script_package = {
        "schema_version": "phase1-content-package.v1", "release_id": release_id,
        "package_digest": package_digest, "script": script,
        "graph_validation": graph_report,
    }

    relative_payloads: dict[str, bytes] = {
        "01_research_资料搜集/SOURCE_MANIFEST.json": _pretty_json_bytes(source_manifest),
        "01_research_资料搜集/SOURCE_SANITIZATION_REPORT.json": _pretty_json_bytes(source_sanitization_report),
        "01_research_资料搜集/BOOK_RESEARCH.json": _pretty_json_bytes(research),
        "01_research_资料搜集/FACT_LEDGER.json": _pretty_json_bytes(research["fact_ledger"]),
        "01_research_资料搜集/CREATIVE_DECISION.json": _pretty_json_bytes(creative_decision),
        "01_research_资料搜集/FATE_ANCHORS.json": _pretty_json_bytes(fate_anchors),
        "01_research_资料搜集/EVENT_CARDS.json": _pretty_json_bytes(event_cards),
        "02_story_script_故事脚本/SCRIPT_PACKAGE.json": _pretty_json_bytes(script_package),
        "02_story_script_故事脚本/SCRIPT_PERFORMANCE.md": render_script_markdown(
            "单主播口播表演稿", script["performance_version"], mode="performance"
        ).encode("utf-8"),
        "02_story_script_故事脚本/SCRIPT_RELEASE.md": render_script_markdown(
            "单主播口播发布稿", script["release_version"], mode="release"
        ).encode("utf-8"),
        "02_story_script_故事脚本/SCRIPT_AUDIT.md": render_script_markdown(
            "单主播口播审计稿", script["audit_version"], mode="audit"
        ).encode("utf-8"),
        "02_story_script_故事脚本/SCRIPT_METRICS.json": _pretty_json_bytes(metrics),
        "02_story_script_故事脚本/CONTENT_QUALITY_REPORT.json": _pretty_json_bytes(quality),
        "02_story_script_故事脚本/ORIGINALITY_REPORT.json": _pretty_json_bytes(originality),
        "02_story_script_故事脚本/BLIND_REVIEW.json": _pretty_json_bytes(blind_review),
        "02_story_script_故事脚本/SCRIPT_LOCK.json": _pretty_json_bytes(lock_payload),
    }

    for relative, content in relative_payloads.items():
        target = root / relative
        if target.exists() and target.read_bytes() != content:
            raise ContentPackageConflict(f"refusing to overwrite different artifact: {relative}")

    written: list[Path] = []
    stage_manifest_path: Path | None = None
    temp_root = Path(tempfile.mkdtemp(prefix="phase1-content-", dir=root))
    try:
        for relative, content in relative_payloads.items():
            staged = temp_root / relative
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(content)
        for relative in relative_payloads:
            staged = temp_root / relative
            target = root / relative
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, target)
            written.append(target)

        output_pairs = [
            (Path(relative).stem.lower(), root / relative)
            for relative in relative_payloads
        ]
        stage_manifest_path = write_stage_manifest(
            root, stage="phase1_content_brain", release_id=release_id,
            release_profile_id=str(project_workflow(root)["release_profile_id"]),
            inputs=[("project_contract", root / "project.json")],
            outputs=output_pairs,
            checks=[
                {"check": "formal_research", "severity": "error", "result": "pass"},
                {"check": "editorial_graph", "severity": "error", "result": "pass"},
                {"check": "script_metrics", "severity": "error", "result": "pass"},
                {"check": "machine_script_lock", "severity": "error", "result": "pass"},
            ],
            producer="book-video-factory.phase1-content-brain",
        )
        output_hashes = {
            relative: sha256_file(root / relative) for relative in relative_payloads
        }
        manifest_payload = {
            "schema_version": "content-package-manifest.v1",
            "release_id": release_id, "package_digest": package_digest,
            "input_hashes": input_hashes, "output_hashes": output_hashes,
            "stage_manifest_path": stage_manifest_path.relative_to(root).as_posix(),
            "stage_manifest_sha256": sha256_file(stage_manifest_path),
            "human_approval_required": True,
        }
        manifest_path = root / "02_story_script_故事脚本/CONTENT_PACKAGE_MANIFEST.json"
        manifest_path.write_bytes(_pretty_json_bytes(manifest_payload))
        written.append(manifest_path)
    except Exception:
        for path in reversed(written):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        if stage_manifest_path is not None:
            try:
                stage_manifest_path.unlink()
            except FileNotFoundError:
                pass
        raise
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    return ContentPackageResult(
        status="created", package_digest=package_digest,
        manifest_path=root / "02_story_script_故事脚本/CONTENT_PACKAGE_MANIFEST.json",
        stage_manifest_path=stage_manifest_path,
    )
