"""原著来源导入与等级判定。

来源等级：
- Level A（全文）：用户上传全文 / 合法公版原文 / 本地完整文本 → research_status=ready；
- Level B（部分原文+权威资料）：部分原文或详细权威资料 → research_status=limited，标记覆盖；
- Level C（简介或模型记忆）：只有简介/摘要 → research_status=insufficient_source，fail closed。
不凭模型记忆声称深读完成。
"""
from __future__ import annotations

import enum
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FULL_TEXT_MIN_CHARS = 4000  # 全文判定阈值（按字符数；中文密度高，4000 字符≈实质文本）
SUMMARY_MAX_CHARS = 800  # 简介阈值：少于 800 字符且无章节结构 → C
RIGHTS_INDICATORS = (
    ("txt80.cc", re.compile(r"txt80\.cc", re.IGNORECASE)),
    ("txt80", re.compile(r"txt80", re.IGNORECASE)),
    ("source_disclaimer", re.compile(r"来源声明|转载|免责声明|免责条款|免责：|免责:|仅供个人学习|网络收集", re.IGNORECASE)),
)


class SourceLevel(str, enum.Enum):
    A = "A"
    B = "B"
    C = "C"


@dataclass
class SourceFile:
    path: str
    size: int
    sha256: str
    kind: str  # full_text / partial_text / summary / metadata / authoritative

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "size": self.size, "sha256": self.sha256, "kind": self.kind}


@dataclass
class SourceManifest:
    book_title: str
    author: str
    level: SourceLevel
    research_status: str  # ready / limited / insufficient_source
    is_full_text: bool
    files: list[SourceFile] = field(default_factory=list)
    coverage: str = ""
    source_dir: str = ""
    rights_status: str = "pending_rights_clearance"
    public_release_allowed: bool = False
    rights_evidence: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "book-research.v1",
            "book_id": "",
            "book_title": self.book_title,
            "author": self.author,
            "source_level": self.level.value,
            "evidence_level": self.level.value,
            "research_status": self.research_status,
            "is_full_text": self.is_full_text,
            "coverage": self.coverage,
            "source_dir": self.source_dir,
            "rights_status": self.rights_status,
            "public_release_allowed": self.public_release_allowed,
            "rights_evidence": list(self.rights_evidence),
            "files": [f.to_dict() for f in self.files],
        }


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _classify_text(p: Path) -> str:
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "metadata"
    n = len(text)
    if n >= FULL_TEXT_MIN_CHARS:
        return "full_text"
    if n >= SUMMARY_MAX_CHARS:
        return "partial_text"
    return "summary"


def _rights_evidence(source: Path, files: list[SourceFile]) -> list[dict[str, str]]:
    evidence: list[dict[str, str]] = []
    for source_file in files:
        if source_file.kind not in {"full_text", "partial_text", "summary"}:
            continue
        path = source / source_file.path if source.is_dir() else source
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for label, pattern in RIGHTS_INDICATORS:
            match = pattern.search(text)
            if match:
                line = text[: match.start()].splitlines()[-1] if match.start() else ""
                evidence.append(
                    {
                        "indicator": label,
                        "file": source_file.path,
                        "excerpt": (line + match.group(0))[:240],
                    }
                )
    return evidence


def build_source_sanitization_report(
    source_manifest: dict[str, Any], source_root: Path
) -> dict[str, Any]:
    """Return deterministic source/rights evidence without altering source text."""
    source_dir_value = source_manifest.get("source_dir")
    source_dir = (source_root / str(source_dir_value)).resolve()
    files = [
        SourceFile(
            path=str(item.get("path", "")),
            size=int(item.get("size", 0)),
            sha256=str(item.get("sha256", "")),
            kind=str(item.get("kind", "metadata")),
        )
        for item in source_manifest.get("files", [])
        if isinstance(item, dict)
    ]
    detected = _rights_evidence(source_dir, files)
    declared_status = str(source_manifest.get("rights_status") or "pending_rights_clearance")
    rights_status = "uncleared_source_declaration" if detected else declared_status
    public_allowed = rights_status == "cleared" and source_manifest.get("public_release_allowed") is True and not detected
    return {
        "schema_version": "source-sanitization-report.v1",
        "evidence_level": source_manifest.get("evidence_level", source_manifest.get("source_level")),
        "rights_status": rights_status,
        "public_release_allowed": public_allowed,
        "detected_source_declarations": detected,
        "source_text_modified": False,
        "sanitization_action": "none_source_preserved_byte_for_byte",
    }


def ingest_source(source_path: Path, *, book_title: str = "", author: str = "") -> SourceManifest:
    """扫描来源目录，判定等级。"""
    src = Path(source_path)
    if not src.exists():
        raise FileNotFoundError(src)
    files: list[SourceFile] = []
    has_full = False
    has_partial = False
    has_summary = False
    if src.is_file():
        # PDF/EPUB are opaque containers until a separate extraction step creates
        # auditable UTF-8 text. File presence alone is not proof of full-text access.
        if src.suffix.lower() in {".pdf", ".epub"}:
            kind = "opaque_document"
        else:
            kind = _classify_text(src)
        files.append(SourceFile(src.name, src.stat().st_size, _sha256_file(src), kind))
        if kind == "full_text":
            has_full = True
        elif kind == "partial_text":
            has_partial = True
        else:
            has_summary = True
    else:
        for p in sorted(src.rglob("*")):
            if not p.is_file():
                continue
            if p.suffix.lower() in {".txt", ".md", ".text"}:
                kind = _classify_text(p)
            elif p.suffix.lower() in {".epub", ".pdf"}:
                kind = "opaque_document"
            elif p.suffix.lower() == ".json":
                kind = "metadata"
            else:
                continue
            files.append(SourceFile(str(p.relative_to(src)), p.stat().st_size, _sha256_file(p), kind))
            if kind == "full_text":
                has_full = True
            elif kind == "partial_text":
                has_partial = True
            elif kind == "summary":
                has_summary = True

    # 从 meta.json 提取书名作者
    if not book_title or not author:
        for f in files:
            if f.kind == "metadata" and f.path.endswith("meta.json"):
                try:
                    meta_path = src / f.path if src.is_dir() else src
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    book_title = book_title or meta.get("title", "")
                    author = author or meta.get("author", "")
                except (OSError, json.JSONDecodeError):
                    pass

    if has_full:
        level = SourceLevel.A
        status = "ready"
        coverage = "full_text_available"
    elif has_partial:
        level = SourceLevel.B
        status = "limited"
        coverage = "partial_text_or_authoritative_only"
    else:
        level = SourceLevel.C
        status = "insufficient_source"
        coverage = "summary_or_memory_only"

    rights_evidence = _rights_evidence(src, files)
    return SourceManifest(
        book_title=book_title,
        author=author,
        level=level,
        research_status=status,
        is_full_text=has_full,
        files=files,
        coverage=coverage,
        source_dir=str(src),
        rights_status=("uncleared_source_declaration" if rights_evidence else "pending_rights_clearance"),
        public_release_allowed=False,
        rights_evidence=rights_evidence,
    )


def source_rights_state(project: Path) -> str:
    root = project.expanduser().resolve()
    manifest_path = root / "01_research_资料搜集/SOURCE_MANIFEST.json"
    report_path = root / "01_research_资料搜集/SOURCE_SANITIZATION_REPORT.json"
    if not manifest_path.is_file():
        return "pending_rights_clearance"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        else:
            report = build_source_sanitization_report(manifest, root)
    except (OSError, json.JSONDecodeError):
        return "pending_rights_clearance"
    if (
        manifest.get("rights_status") != "cleared"
        or manifest.get("public_release_allowed") is not True
        or report.get("rights_status") != "cleared"
        or report.get("public_release_allowed") is not True
        or report.get("detected_source_declarations")
    ):
        return str(report.get("rights_status") or manifest.get("rights_status") or "pending_rights_clearance")
    return "cleared"
