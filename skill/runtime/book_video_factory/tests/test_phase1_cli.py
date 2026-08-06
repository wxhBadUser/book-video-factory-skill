from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
SCRIPT = ROOT / "scripts" / "build_content_package.py"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "phase1_valid_package"

sys.path.insert(0, str(ROOT / "src"))
from book_video_factory.project import initialize_project
sys.path.insert(0, str(Path(__file__).resolve().parent))
from phase1_fixture_factory import materialize_phase1_source


def _project(tmp: str) -> Path:
    project = initialize_project(Path(tmp) / "warehouse", "old-man-and-the-sea", "老人与海", "海明威")
    materialize_phase1_source(project)
    return project


def _run(project: Path, source: Path = FIXTURE, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--project", str(project), "--input-dir", str(source), "--release-id", "r1", *extra],
        cwd=REPO, capture_output=True, text=True, check=False,
    )


def test_validate_only_writes_nothing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = _project(tmp)
        result = _run(project, FIXTURE, "--validate-only")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["status"] == "valid"
        assert not (project / "02_story_script_故事脚本/SCRIPT_LOCK.json").exists()


def test_valid_fixture_builds_package() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = _project(tmp)
        result = _run(project)
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["status"] == "created"
        assert len(payload["package_digest"]) == 64
        assert (project / "02_story_script_故事脚本/SCRIPT_LOCK.json").is_file()


def test_placeholder_fixture_returns_exit_two() -> None:
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as input_tmp:
        project = _project(tmp)
        target = Path(input_tmp)
        for file in FIXTURE.iterdir():
            destination = target / file.name
            if file.is_dir():
                shutil.copytree(file, destination)
            else:
                destination.write_bytes(file.read_bytes())
        cards = json.loads((target / "EVENT_CARDS.json").read_text(encoding="utf-8"))
        cards["cards"][0]["hammer_line"] = "待定"
        (target / "EVENT_CARDS.json").write_text(json.dumps(cards, ensure_ascii=False), encoding="utf-8")
        result = _run(project, target)
        assert result.returncode == 2
        assert json.loads(result.stderr)["error_type"] == "validation"


def test_conflicting_existing_package_returns_exit_three() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = _project(tmp)
        target = project / "02_story_script_故事脚本/SCRIPT_RELEASE.md"
        target.write_text("用户内容", encoding="utf-8")
        result = _run(project)
        assert result.returncode == 3
        assert json.loads(result.stderr)["error_type"] == "conflict"
        assert target.read_text(encoding="utf-8") == "用户内容"


def test_cli_json_summary_contains_manifest_paths() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = _project(tmp)
        result = _run(project)
        payload = json.loads(result.stdout)
        assert payload["manifest_path"].endswith("CONTENT_PACKAGE_MANIFEST.json")
        assert payload["stage_manifest_path"].endswith(".json")
