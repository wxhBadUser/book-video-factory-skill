from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from book_video_factory.manifests import safe_project_output, sha256_file


class StagingResolutionError(RuntimeError):
    """A provider delivery cannot be resolved to one safe staged image."""


class AssetNormalizationError(RuntimeError):
    """A staged scene image cannot be safely normalized and published."""


@dataclass(frozen=True)
class StagedAssetResolution:
    path: Path
    sha256: str
    width: int
    height: int
    mode: str


@dataclass(frozen=True)
class AssetNormalizationResult:
    source_path: Path
    output_path: Path
    source_sha256: str
    final_sha256: str
    delivered_size: tuple[int, int]
    final_size: tuple[int, int]
    orientation: str
    strategy: str
    asset_tier: str


def _probe(path: Path, error_type: type[RuntimeError]) -> tuple[int, int, str]:
    try:
        with Image.open(path) as probe:
            probe.verify()
        with Image.open(path) as opened:
            return opened.width, opened.height, opened.mode
    except (OSError, UnidentifiedImageError) as error:
        raise error_type(f"staged asset is not a decodable image: {error}") from error


def resolve_staged_asset(project: Path, expected_output_target: str) -> StagedAssetResolution:
    root = project.expanduser().resolve()
    expected = Path(expected_output_target)
    if expected.is_absolute() or ".." in expected.parts or not expected.name:
        raise StagingResolutionError("expected output target must be a confined relative path")
    staging = root / "06_visual_production/staging"
    if staging.is_symlink() or not staging.is_dir():
        raise StagingResolutionError("project staging directory is missing or symlinked")
    staging_root = staging.resolve()
    matches: list[Path] = []
    for candidate in staging.rglob("*"):
        if candidate.name.casefold() != expected.name.casefold() or candidate.is_symlink() or not candidate.is_file():
            continue
        resolved = candidate.resolve()
        try:
            resolved.relative_to(staging_root)
        except ValueError as error:
            raise StagingResolutionError("staged asset escapes the project staging directory") from error
        matches.append(resolved)
    if not matches:
        raise StagingResolutionError(f"staged provider output is missing or not found: {expected.name}")
    if len(matches) != 1:
        raise StagingResolutionError(f"staged provider output basename collision or multiple duplicates: {expected.name}")
    path = matches[0]
    width, height, mode = _probe(path, StagingResolutionError)
    return StagedAssetResolution(path, sha256_file(path), width, height, mode)


def normalize_scene_asset(
    project: Path,
    source: Path,
    output_target: str,
    *,
    orientation: str,
    strategy: str,
    asset_tier: str,
) -> AssetNormalizationResult:
    root = project.expanduser().resolve()
    raw_source = source.expanduser()
    if raw_source.is_symlink() or not raw_source.is_file():
        raise AssetNormalizationError("normalization source is missing or symlinked")
    source_path = raw_source.resolve()
    staging = (root / "06_visual_production/staging").resolve()
    try:
        source_path.relative_to(staging)
    except ValueError as error:
        raise AssetNormalizationError("normalization source must remain inside project staging") from error
    if orientation not in {"landscape", "portrait"}:
        raise AssetNormalizationError("orientation must be landscape or portrait")
    if strategy not in {"letterbox", "crop"}:
        raise AssetNormalizationError("strategy must be letterbox or crop")
    if asset_tier not in {"standard", "hero", "identity"}:
        raise AssetNormalizationError("asset tier must be standard, hero, or identity")
    try:
        output = safe_project_output(root, Path(output_target))
    except (OSError, ValueError) as error:
        raise AssetNormalizationError(f"normalization output escapes the project: {error}") from error
    if output.suffix.casefold() != ".png":
        raise AssetNormalizationError("normalized scene output must be PNG")
    if output.exists() or output.is_symlink():
        raise AssetNormalizationError("refusing to overwrite an existing normalized asset")
    delivered_width, delivered_height, _mode = _probe(source_path, AssetNormalizationError)
    target_size = (1920, 1080) if orientation == "landscape" else (1080, 1920)
    source_aspect = delivered_width / delivered_height
    target_aspect = target_size[0] / target_size[1]
    crop_loss = 1.0 - min(source_aspect / target_aspect, target_aspect / source_aspect)
    if strategy == "crop" and crop_loss > 1e-6:
        if asset_tier in {"hero", "identity"}:
            raise AssetNormalizationError(f"{asset_tier} tier forbids automatic hard crop")
        if crop_loss > 0.12:
            raise AssetNormalizationError(f"automatic crop loss {crop_loss:.3f} exceeds the safe 0.120 limit")
    try:
        with Image.open(source_path) as opened:
            image = opened.convert("RGB")
        if strategy == "letterbox":
            fitted = ImageOps.contain(image, target_size, Image.Resampling.LANCZOS)
            normalized = Image.new("RGB", target_size, (0, 0, 0))
            normalized.paste(fitted, ((target_size[0] - fitted.width) // 2, (target_size[1] - fitted.height) // 2))
        else:
            normalized = ImageOps.fit(image, target_size, Image.Resampling.LANCZOS, centering=(0.5, 0.5))
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=f".{output.stem}.", suffix=".png.tmp", dir=output.parent, delete=False) as handle:
            staged_output = Path(handle.name)
        normalized.save(staged_output, format="PNG")
        final_hash = sha256_file(staged_output)
        os.replace(staged_output, output)
        if sha256_file(output) != final_hash:
            output.unlink(missing_ok=True)
            raise AssetNormalizationError("published asset hash differs from the pre-publication hash")
        final_width, final_height, _final_mode = _probe(output, AssetNormalizationError)
        if (final_width, final_height) != target_size:
            output.unlink(missing_ok=True)
            raise AssetNormalizationError("published asset canvas differs from the exact target")
    except AssetNormalizationError:
        raise
    except Exception as error:
        if "staged_output" in locals():
            staged_output.unlink(missing_ok=True)
        output.unlink(missing_ok=True)
        raise AssetNormalizationError(f"asset normalization transaction failed: {error}") from error
    return AssetNormalizationResult(
        source_path,
        output,
        sha256_file(source_path),
        final_hash,
        (delivered_width, delivered_height),
        target_size,
        orientation,
        strategy,
        asset_tier,
    )

