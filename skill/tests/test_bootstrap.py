from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bootstrap = load_module("bootstrap_workspace", ROOT / "scripts/bootstrap_workspace.py")
run_cost = load_module("run_cost", ROOT / "scripts/run_cost.py")


EXPECTED_PROJECT_DIRS = {
    "00_topic_选题",
    "01_research_资料搜集/raw",
    "01_research_资料搜集/normalized",
    "01_research_资料搜集/sources",
    "02_story_script_故事脚本",
    "03_images_生成图片/prompts",
    "03_images_生成图片/generated",
    "03_images_生成图片/approved",
    "04_audio",
    "05_director",
    "06_visual_production",
    "07_render/workspaces",
    "05_voice_人声",
    "06_music_音乐",
    "07_timeline_时间线",
    "08_render_合成/preview",
    "08_render_合成/final",
    "09_qc_质检",
    "10_delivery_交付",
    "assets/generated/anchors",
    "assets/generated/sheets",
    "assets/generated/scenes",
    "assets/audio/opening",
    "assets/audio/bgm",
    "qa",
    "renders",
    "manifests/stages",
    "logs/approval_events",
}


class BootstrapTests(unittest.TestCase):
    def test_workspace_bootstrap_copies_clean_runtime_and_hbg_vendor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            created = bootstrap.bootstrap_workspace(workspace)

            self.assertTrue(created)
            self.assertTrue(
                (workspace / "book_video_factory/config/style_profiles/classic-narrator-hbg-v1.json").is_file()
            )
            self.assertTrue(
                (workspace / "vendor/hbg-life-simulation/scripts/build_narration.mjs").is_file()
            )
            self.assertTrue(
                (workspace / "vendor/hbg-life-simulation/scripts/render_streaming_ffmpeg.mjs").is_file()
            )
            self.assertTrue(
                (workspace / "vendor/hbg-life-simulation/UPSTREAM_LOCK.json").is_file()
            )
            self.assertTrue((workspace / "scripts/verify_vfinal_architecture.py").is_file())
            self.assertTrue(
                (workspace / "skill/runtime/book_video_factory/scripts/run_full_pipeline.py").is_file()
            )
            for reference in ("content-brain.md", "visual-stage.md", "audio-stage.md"):
                with self.subTest(reference=reference):
                    self.assertTrue((workspace / "skill/references" / reference).is_file())
            for contract in (
                "AGENTS.md",
                "CODEX_AGENT.md",
                "FULL_PIPELINE.md",
                "ACCEPTANCE.md",
                "INSTALL.md",
                "TROUBLESHOOTING.md",
            ):
                with self.subTest(contract=contract):
                    self.assertTrue((workspace / contract).is_file())
            self.assertFalse(
                (workspace / "book_video_factory/src/book_video_factory/audio_drama_contracts.py").exists()
            )
            self.assertFalse(
                (workspace / "book_video_factory/src/book_video_factory/voice.py").exists()
            )
            self.assertFalse(
                (workspace / "book_video_factory/src/book_video_factory/gemini_video.py").exists()
            )

    def test_workspace_bootstrap_prunes_rejected_runtime_files_and_restores_managed_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            bootstrap.bootstrap_workspace(workspace)
            rejected = workspace / "book_video_factory/src/book_video_factory/audio_drama_contracts.py"
            rejected.parent.mkdir(parents=True, exist_ok=True)
            rejected.write_text("obsolete", encoding="utf-8")
            managed = workspace / "book_video_factory/README.md"
            managed.write_text("tampered", encoding="utf-8")

            changed = bootstrap.bootstrap_workspace(workspace)

            self.assertFalse(rejected.exists())
            self.assertEqual(
                managed.read_text(encoding="utf-8"),
                (ROOT / "runtime/book_video_factory/README.md").read_text(encoding="utf-8"),
            )
            self.assertIn(managed, changed)

    def test_project_creation_uses_single_hbg_contract_and_root_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            bootstrap.bootstrap_workspace(workspace)
            project, _ = bootstrap.create_project(
                workspace,
                "old-man-and-the-sea",
                "老人与海",
                "欧内斯特·海明威",
            )

            contract = json.loads((project / "project.json").read_text(encoding="utf-8"))
            self.assertEqual(contract["workflow"]["mode"], "single-book")
            self.assertEqual(
                contract["workflow"]["style_profile_id"], "classic-narrator-hbg-v1"
            )
            self.assertEqual(
                contract["workflow"]["release_profile_id"],
                "book-classic-narrator-hbg-16x9-v1",
            )
            self.assertEqual(contract["workflow"]["generation_lane"], "host-imagegen")
            self.assertEqual(contract["workflow"]["state_source"], "derived_gate_evaluator")

            for relative in EXPECTED_PROJECT_DIRS:
                with self.subTest(relative=relative):
                    self.assertTrue((project / relative).is_dir())

            for relative in (
                "PROJECT_SPEC.json",
                "SCRIPT_SOURCE.md",
                "SCRIPT.md",
                "CHARACTERS.md",
                "STORYBOARD_BASE.json",
                "PROMPTS.md",
            ):
                with self.subTest(relative=relative):
                    self.assertTrue((project / relative).is_file())

            project_spec = json.loads((project / "PROJECT_SPEC.json").read_text(encoding="utf-8"))
            self.assertEqual(project_spec["projectType"], "classic-book-narration")
            self.assertEqual(project_spec["book"]["title"], "老人与海")
            self.assertEqual(project_spec["narration"]["provider"], "edge-tts")
            self.assertEqual(project_spec["workflow"]["scriptContract"], "script.narrator-essay.v1")
            self.assertEqual(
                project_spec["book"]["sourceManifest"],
                "01_research_资料搜集/SOURCE_MANIFEST.json",
            )
            self.assertEqual(
                project_spec["visual"]["profile"],
                "03_images_生成图片/BOOK_VISUAL_PROFILE.json",
            )

    def test_bootstrap_and_project_creation_are_idempotent_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            bootstrap.bootstrap_workspace(workspace)
            project, _ = bootstrap.create_project(
                workspace, "example-book", "Example", "Author"
            )
            source = project / "SCRIPT_SOURCE.md"
            source.write_text("USER LOCKED CONTENT\n", encoding="utf-8")

            bootstrap.bootstrap_workspace(workspace)
            same_project, created = bootstrap.create_project(
                workspace, "example-book", "Example", "Author"
            )

            self.assertEqual(same_project, project)
            self.assertEqual(source.read_text(encoding="utf-8"), "USER LOCKED CONTENT\n")
            self.assertEqual(created, [])


    def test_workspace_bootstrap_copies_phase_two_bridge_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            bootstrap.bootstrap_workspace(workspace)
            self.assertTrue((workspace / "book_video_factory/scripts/build_hbg_bridge.py").is_file())
            self.assertTrue((workspace / "book_video_factory/src/book_video_factory/hbg_bridge/compiler.py").is_file())
            self.assertTrue((workspace / "book_video_factory/schemas/hbg_bridge_input.v1.schema.json").is_file())


    def test_workspace_bootstrap_copies_phase_four_audio_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            bootstrap.bootstrap_workspace(workspace)
            for relative in (
                "book_video_factory/scripts/run_audio_stage.py",
                "book_video_factory/src/book_video_factory/audio_stage/compiler.py",
                "book_video_factory/src/book_video_factory/audio_stage/status.py",
                "book_video_factory/schemas/audio_stage_manifest.v1.schema.json",
            ):
                with self.subTest(relative=relative):
                    self.assertTrue((workspace / relative).is_file(), relative)


    def test_workspace_bootstrap_copies_phase_five_to_seven_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            bootstrap.bootstrap_workspace(workspace)
            for relative in (
                "book_video_factory/scripts/build_director_stage.py",
                "book_video_factory/scripts/register_scene_asset.py",
                "book_video_factory/scripts/split_scene_sheet.py",
                "book_video_factory/scripts/review_scene_assets.py",
                "book_video_factory/scripts/approve_scene_assets.py",
                "book_video_factory/scripts/run_render_stage.py",
                "book_video_factory/scripts/approve_final_master.py",
                "book_video_factory/scripts/run_full_pipeline.py",
                "book_video_factory/src/book_video_factory/director_stage/compiler.py",
                "book_video_factory/src/book_video_factory/production_visuals/registry.py",
                "book_video_factory/src/book_video_factory/render_stage/compiler.py",
                "book_video_factory/src/book_video_factory/delivery_stage/approval.py",
            ):
                with self.subTest(relative=relative):
                    self.assertTrue((workspace / relative).is_file(), relative)

    def test_project_creation_rejects_removed_workflow_modes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            bootstrap.bootstrap_workspace(workspace)
            with self.assertRaisesRegex(ValueError, "single-book"):
                bootstrap.create_project(
                    workspace,
                    "bad-mode",
                    "Example",
                    "Author",
                    mode="content-system-backed",
                )

    def test_slug_rejects_unsafe_values(self) -> None:
        with self.assertRaises(Exception):
            bootstrap.valid_slug("../unsafe")


class LedgerTests(unittest.TestCase):
    def test_unknown_tokens_are_rendered_as_dash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            warehouse = Path(temporary) / "book_video_warehouse"
            run_cost.append_event(
                warehouse,
                {
                    "project_slug": "example-book",
                    "images_generated": 12,
                    "music_jobs": 1,
                    "voice_seconds": 42,
                    "render_seconds": 50,
                    "retries": 0,
                },
            )
            events = run_cost.read_events(warehouse)
            self.assertEqual(run_cost.token_value(events, "codex_input_tokens"), "—")
            self.assertEqual(run_cost.aggregate(events)["images_generated"], 12)


if __name__ == "__main__":
    unittest.main()
