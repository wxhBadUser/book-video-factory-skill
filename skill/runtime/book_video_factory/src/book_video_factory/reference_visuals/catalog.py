from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError


class ReferenceCatalogError(ValueError):
    pass


_SHA256 = re.compile(r"[0-9a-f]{64}")
_REFERENCE_ID = re.compile(r"REF_[A-Z0-9_]{2,64}")
_TOP_FIELDS = {
    "schema_version", "catalog_id", "kernel_id", "kernel_path",
    "kernel_sha256", "entries",
}
_ENTRY_FIELDS = {
    "reference_id", "work_title", "path", "profile", "profile_sha256",
    "sha256", "width", "height", "shot_count", "metrics",
    "visual_language", "roles",
}
_PROFILE_FIELDS = {
    "schema_version", "reference_id", "work_title", "visual_language",
    "metrics", "roles",
}
_KERNEL_FIELDS = {
    "schema_version", "kernel_id", "shared_requirements",
    "shared_prohibitions", "machine_diagnostics_are_aesthetic_approval",
}


def repository_root() -> Path:
    current = Path(__file__).resolve()
    catalog = Path("book_video_factory/config/visuals/gold-reference-catalog-v1.json")
    references = Path("docs/visual-upgrade-input/contact_sheets")
    for parent in current.parents:
        if (parent / catalog).is_file() and (parent / references).is_dir():
            return parent
    raise ReferenceCatalogError("cannot locate repository visual reference evidence")


def default_catalog_path() -> Path:
    return repository_root() / "book_video_factory/config/visuals/gold-reference-catalog-v1.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _confined(root: Path, value: str, label: str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as error:
        raise ReferenceCatalogError(f"{label} escapes repository root: {value}") from error
    return resolved


@dataclass(frozen=True)
class ReferenceCatalogEntry:
    reference_id: str
    work_title: str
    path: str
    profile_path: str
    profile_sha256: str
    sha256: str
    width: int
    height: int
    shot_count: int
    metrics: dict[str, float | int]
    visual_language: str
    style_only: bool
    identity_reference_allowed: bool
    production_asset_allowed: bool
    exact_composition_copy_allowed: bool
    absolute_path: Path


@dataclass(frozen=True)
class ReferenceCatalog:
    path: Path
    catalog_id: str
    kernel_id: str
    kernel_path: Path
    kernel_sha256: str
    entries: tuple[ReferenceCatalogEntry, ...]

    def by_id(self) -> dict[str, ReferenceCatalogEntry]:
        return {entry.reference_id: entry for entry in self.entries}


def _positive_number(value: Any, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        raise ReferenceCatalogError(f"{label} must be a nonnegative number")
    return float(value)


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReferenceCatalogError(f"{label} is unreadable: {error}") from error
    if not isinstance(value, dict):
        raise ReferenceCatalogError(f"{label} must be a JSON object")
    return value


def load_reference_catalog(
    path: Path | None = None,
    *,
    repository_root: Path | None = None,
) -> ReferenceCatalog:
    root = (repository_root or globals()["repository_root"]()).resolve()
    source = _confined(root, str(path or default_catalog_path()), "catalog path")
    payload = _load_json(source, "reference catalog")
    if payload.get("schema_version") != "gold-reference-catalog.v1":
        raise ReferenceCatalogError("unsupported reference catalog schema")
    if set(payload) != _TOP_FIELDS:
        raise ReferenceCatalogError("reference catalog fields are invalid")
    catalog_id = payload.get("catalog_id")
    kernel_id = payload.get("kernel_id")
    kernel_digest = payload.get("kernel_sha256")
    if not isinstance(catalog_id, str) or not catalog_id.strip() or catalog_id != catalog_id.strip():
        raise ReferenceCatalogError("reference catalog_id is invalid")
    if not isinstance(kernel_id, str) or not kernel_id.strip() or kernel_id != kernel_id.strip():
        raise ReferenceCatalogError("reference kernel_id is invalid")
    if not isinstance(kernel_digest, str) or _SHA256.fullmatch(kernel_digest) is None:
        raise ReferenceCatalogError("reference kernel sha256 is invalid")

    kernel_path = _confined(root, str(payload.get("kernel_path", "")), "kernel path")
    if not kernel_path.is_file() or _sha256(kernel_path) != kernel_digest:
        raise ReferenceCatalogError("reference kernel hash mismatch or file missing")
    kernel = _load_json(kernel_path, "reference kernel")
    if (
        set(kernel) != _KERNEL_FIELDS
        or kernel.get("schema_version") != "visual-quality-kernel.v1"
        or kernel.get("kernel_id") != kernel_id
        or kernel.get("machine_diagnostics_are_aesthetic_approval") is not False
        or not isinstance(kernel.get("shared_requirements"), list)
        or len(kernel["shared_requirements"]) < 5
        or not all(isinstance(item, str) and item.strip() and item == item.strip() for item in kernel["shared_requirements"])
        or not isinstance(kernel.get("shared_prohibitions"), list)
        or len(kernel["shared_prohibitions"]) < 5
        or not all(isinstance(item, str) and item.strip() and item == item.strip() for item in kernel["shared_prohibitions"])
    ):
        raise ReferenceCatalogError("reference kernel contract is invalid")

    entries = payload.get("entries")
    if not isinstance(entries, list) or len(entries) != 6:
        raise ReferenceCatalogError("reference catalog must contain exactly six entries")
    seen_ids: set[str] = set()
    seen_titles: set[str] = set()
    seen_hashes: set[str] = set()
    parsed: list[ReferenceCatalogEntry] = []
    for index, item in enumerate(entries):
        if not isinstance(item, dict) or set(item) != _ENTRY_FIELDS:
            raise ReferenceCatalogError(f"entries[{index}] fields are invalid")
        reference_id = item.get("reference_id")
        title = item.get("work_title")
        digest = item.get("sha256")
        profile_digest = item.get("profile_sha256")
        if not isinstance(reference_id, str) or _REFERENCE_ID.fullmatch(reference_id) is None:
            raise ReferenceCatalogError(f"entries[{index}].reference_id is invalid")
        if reference_id in seen_ids:
            raise ReferenceCatalogError(f"duplicate reference_id: {reference_id}")
        if not isinstance(title, str) or not title.strip() or title != title.strip() or title in seen_titles:
            raise ReferenceCatalogError(f"entries[{index}].work_title is invalid or duplicate")
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise ReferenceCatalogError(f"entries[{index}].sha256 is invalid")
        if not isinstance(profile_digest, str) or _SHA256.fullmatch(profile_digest) is None:
            raise ReferenceCatalogError(f"entries[{index}].profile_sha256 is invalid")
        if digest in seen_hashes:
            raise ReferenceCatalogError(f"duplicate reference sha256: {digest}")
        roles = item.get("roles")
        if roles != {
            "style_only": True,
            "identity_reference_allowed": False,
            "production_asset_allowed": False,
            "exact_composition_copy_allowed": False,
        }:
            raise ReferenceCatalogError("gold references must remain style-only evidence")
        image_path = _confined(root, str(item.get("path", "")), "reference path")
        profile_path = _confined(root, str(item.get("profile", "")), "profile path")
        if not image_path.is_file() or not profile_path.is_file():
            raise ReferenceCatalogError(f"reference files are missing for {reference_id}")
        if _sha256(image_path) != digest:
            raise ReferenceCatalogError(f"sha256 mismatch for {reference_id}")
        if _sha256(profile_path) != profile_digest:
            raise ReferenceCatalogError(f"reference profile sha256 mismatch for {reference_id}")
        try:
            with Image.open(image_path) as image:
                image.verify()
            with Image.open(image_path) as image:
                actual_width, actual_height = image.size
        except (OSError, UnidentifiedImageError) as error:
            raise ReferenceCatalogError(f"reference image is not decodable: {reference_id}") from error
        width = item.get("width")
        height = item.get("height")
        shot_count = item.get("shot_count")
        if not isinstance(width, int) or isinstance(width, bool) or not isinstance(height, int) or isinstance(height, bool) or (width, height) != (actual_width, actual_height):
            raise ReferenceCatalogError(f"dimensions mismatch for {reference_id}")
        if not isinstance(shot_count, int) or isinstance(shot_count, bool) or shot_count <= 0:
            raise ReferenceCatalogError(f"shot_count is invalid for {reference_id}")
        metrics = item.get("metrics")
        required_metrics = {"shot_count", "mean_luma", "mean_contrast", "dark_share", "highlight_share", "warm_tendency"}
        if not isinstance(metrics, dict) or set(metrics) != required_metrics or metrics.get("shot_count") != shot_count:
            raise ReferenceCatalogError(f"metrics keys mismatch for {reference_id}")
        for key in required_metrics - {"shot_count"}:
            _positive_number(metrics[key], f"{reference_id}.{key}")
        visual_language = item.get("visual_language")
        if not isinstance(visual_language, str) or not visual_language.strip() or visual_language != visual_language.strip():
            raise ReferenceCatalogError(f"visual_language missing for {reference_id}")
        profile = _load_json(profile_path, f"reference profile {reference_id}")
        expected_profile = {
            "schema_version": "reference-visual-profile.v1",
            "reference_id": reference_id,
            "work_title": title,
            "visual_language": visual_language,
            "metrics": metrics,
            "roles": roles,
        }
        if set(profile) != _PROFILE_FIELDS or profile != expected_profile:
            raise ReferenceCatalogError(f"reference profile mismatch for {reference_id}")
        parsed.append(ReferenceCatalogEntry(
            reference_id=reference_id,
            work_title=title,
            path=str(item["path"]),
            profile_path=str(item["profile"]),
            profile_sha256=profile_digest,
            sha256=digest,
            width=width,
            height=height,
            shot_count=shot_count,
            metrics={key: metrics[key] for key in sorted(metrics)},
            visual_language=visual_language,
            style_only=True,
            identity_reference_allowed=False,
            production_asset_allowed=False,
            exact_composition_copy_allowed=False,
            absolute_path=image_path,
        ))
        seen_ids.add(reference_id)
        seen_titles.add(title)
        seen_hashes.add(digest)
    expected_titles = {"漂亮朋友", "窄门", "简爱", "小王子", "包法利夫人", "月亮与六便士"}
    if seen_titles != expected_titles:
        raise ReferenceCatalogError("catalog does not match the approved six visual cases")
    return ReferenceCatalog(
        path=source,
        catalog_id=catalog_id,
        kernel_id=kernel_id,
        kernel_path=kernel_path,
        kernel_sha256=kernel_digest,
        entries=tuple(parsed),
    )


def verify_reference_catalog(path: Path | None = None) -> dict[str, Any]:
    catalog = load_reference_catalog(path)
    hashes = [entry.sha256 for entry in catalog.entries]
    return {
        "schema_version": "reference-catalog-verification.v1",
        "status": "pass",
        "catalog_id": catalog.catalog_id,
        "kernel_id": catalog.kernel_id,
        "kernel_sha256": catalog.kernel_sha256,
        "verified_profiles": len(catalog.entries),
        "verified_entries": len(catalog.entries),
        "duplicate_hashes": sorted({digest for digest in hashes if hashes.count(digest) > 1}),
    }
