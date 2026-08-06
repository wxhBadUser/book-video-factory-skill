from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from phase4_fixture_factory import _ffmpeg_silence, build_approved_phase3_project, fake_hbg_audio_runner, symlink_or_skip, write_phase4_inputs
from phase2_fixture_factory import write_json
from book_video_factory.manifests import sha256_file

try:
    from book_video_factory.audio_stage.compiler import AudioStageError, AudioStageConflict, generate_audio_stage
except ModuleNotFoundError:
    AudioStageError=RuntimeError  # type: ignore
    AudioStageConflict=RuntimeError  # type: ignore


class Phase4GenerateTests(unittest.TestCase):
    def build(self, base: Path):
        project=build_approved_phase3_project(base)
        input_path,lexicon_path=write_phase4_inputs(project)
        return project,input_path,lexicon_path

    def test_generates_real_fixture_audio_vtt_display_meta_and_gap_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project,input_path,lexicon_path=self.build(Path(temp))
            result=generate_audio_stage(project,input_path,lexicon_path,runner=fake_hbg_audio_runner)
            self.assertEqual(result.status,"created")
            self.assertEqual(result.next_stage_status,"awaiting_audio_storyboard_plan")
            for relative in (
                "assets/audio/narration-full.mp3","assets/audio/narration-full.wav",
                "assets/audio/narration-full.vtt","assets/audio/narration.m4a",
                "04_audio/SCRIPT_SPOKEN.md","04_audio/SPOKEN_DISPLAY_MAP.json",
                "04_audio/AUDIO_PRELIMINARY_MANIFEST.json","04_audio/AUDIO_STORYBOARD_GAPS.json",
                "04_audio/raw/audio_meta.hbg.json","audio_meta.json","STORYBOARD.json",
            ):
                self.assertTrue((project/relative).is_file(),relative)
            meta=json.loads((project/"audio_meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["syncMode"],"full-body-vtt-master")
            self.assertEqual(meta["body"]["captionTimingSource"],"edge_vtt")
            self.assertNotIn("spokenText",meta["captions"][0])
            manifest=json.loads(result.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["next_stage_status"],"awaiting_audio_storyboard_plan")
            self.assertEqual(manifest["external_edge_service_exercised"],False)

    def test_identical_rerun_is_unchanged_and_tamper_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project,input_path,lexicon_path=self.build(Path(temp))
            first=generate_audio_stage(project,input_path,lexicon_path,runner=fake_hbg_audio_runner)
            second=generate_audio_stage(project,input_path,lexicon_path,runner=fake_hbg_audio_runner)
            self.assertEqual(second.status,"unchanged")
            target=project/"audio_meta.json"; target.write_bytes(target.read_bytes()+b"tamper")
            with self.assertRaises(AudioStageConflict):
                generate_audio_stage(project,input_path,lexicon_path,runner=fake_hbg_audio_runner)

    def test_failure_leaves_no_partial_official_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project,input_path,lexicon_path=self.build(Path(temp))
            def broken(staging: Path):
                (staging/"audio_meta.json").write_text("{}")
            with self.assertRaises(AudioStageError):
                generate_audio_stage(project,input_path,lexicon_path,runner=broken)
            self.assertFalse((project/"04_audio/AUDIO_PRELIMINARY_MANIFEST.json").exists())
            self.assertFalse((project/"audio_meta.json").exists())

    def test_missing_edge_tts_fails_without_fake_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project,input_path,lexicon_path=self.build(Path(temp))
            with mock.patch("book_video_factory.audio_stage.compiler._edge_tts_available",return_value=False):
                with self.assertRaisesRegex(AudioStageError,"edge-tts|Edge"):
                    generate_audio_stage(project,input_path,lexicon_path)
            self.assertFalse((project/"audio_meta.json").exists())

    def test_generate_requires_project_local_nonsymlink_input_and_lexicon(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project,input_path,lexicon_path=self.build(base)
            outside = base / "outside-input.json"
            outside.write_bytes(input_path.read_bytes())
            with self.assertRaisesRegex(AudioStageError, "project-local|official|symlink"):
                generate_audio_stage(project,outside,lexicon_path,runner=fake_hbg_audio_runner)

        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project,input_path,lexicon_path=self.build(base)
            outside = base / "outside-lexicon.json"
            outside.write_bytes(lexicon_path.read_bytes())
            lexicon_path.unlink()
            symlink_or_skip(self,lexicon_path,outside)
            with self.assertRaisesRegex(AudioStageError, "project-local|official|symlink"):
                generate_audio_stage(project,input_path,lexicon_path,runner=fake_hbg_audio_runner)

    def test_existing_manifest_metadata_tamper_is_rejected_without_provider_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project,input_path,lexicon_path=self.build(Path(temp))
            generate_audio_stage(project,input_path,lexicon_path,runner=fake_hbg_audio_runner)
            manifest_path=project/"04_audio/AUDIO_PRELIMINARY_MANIFEST.json"
            manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["external_edge_service_exercised"]=True
            manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
            calls=[]
            def forbidden(_staging: Path):
                calls.append(1)
                raise AssertionError("provider must not run during unchanged verification")
            with self.assertRaises(AudioStageConflict):
                generate_audio_stage(project,input_path,lexicon_path,runner=forbidden)
            self.assertEqual(calls,[])

    def test_first_generation_does_not_reuse_unproven_project_audio_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project,input_path,lexicon_path=self.build(Path(temp))
            poisoned=project/"assets/audio/narration-full.txt"
            poisoned.parent.mkdir(parents=True,exist_ok=True)
            poisoned.write_text("poisoned cache\n",encoding="utf-8")
            def runner(staging: Path):
                self.assertFalse((staging/"assets/audio/narration-full.txt").exists())
                fake_hbg_audio_runner(staging)
            result=generate_audio_stage(project,input_path,lexicon_path,runner=runner)
            self.assertEqual(result.status,"created")

    def test_valid_but_wrong_duration_mp3_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project,input_path,lexicon_path=self.build(Path(temp))
            def wrong_duration(staging: Path):
                fake_hbg_audio_runner(staging)
                _ffmpeg_silence(staging/"assets/audio/narration-full.mp3",1.0,"libmp3lame")
            with self.assertRaisesRegex(AudioStageError,"duration|MP3|mp3"):
                generate_audio_stage(project,input_path,lexicon_path,runner=wrong_duration)

    def test_missing_opening_audio_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project,input_path,lexicon_path=self.build(Path(temp))
            def missing_opening(staging: Path):
                fake_hbg_audio_runner(staging)
                (staging/"assets/audio/opening/lead-natural.mp3").unlink()
            with self.assertRaisesRegex(AudioStageError,"opening|lead"):
                generate_audio_stage(project,input_path,lexicon_path,runner=missing_opening)

    def test_opening_text_cannot_be_replaced_with_a_self_consistent_wrong_vtt(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project,input_path,lexicon_path=self.build(Path(temp))
            def wrong_opening(staging: Path):
                fake_hbg_audio_runner(staging)
                opening=staging/"assets/audio/opening"
                wrong="与项目输入无关的开头"
                (opening/"lead-natural.txt").write_text(wrong+"\n",encoding="utf-8")
                (opening/"lead-natural.vtt").write_text(
                    "WEBVTT\n\n00:00:00.000 --> 00:00:00.500\n"+wrong+"\n",
                    encoding="utf-8",
                )
            with self.assertRaisesRegex(AudioStageError,"opening|lead|text"):
                generate_audio_stage(project,input_path,lexicon_path,runner=wrong_opening)

    def test_body_pronunciation_rerun_validates_against_raw_hbg_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project,input_path,lexicon_path=self.build(Path(temp))
            lexicon=json.loads(lexicon_path.read_text(encoding="utf-8"))
            lexicon["entries"].append({
                "entry_id":"old-man-body",
                "display":"老人",
                "spoken":"老仁",
                "scope":"body",
                "occurrence_policy":{"mode":"all"},
                "note":"正文发音测试",
            })
            write_json(lexicon_path,lexicon)
            payload=json.loads(input_path.read_text(encoding="utf-8"))
            payload["bindings"]["pronunciation_lexicon_sha256"]=sha256_file(lexicon_path)
            write_json(input_path,payload)
            first=generate_audio_stage(project,input_path,lexicon_path,runner=fake_hbg_audio_runner)
            second=generate_audio_stage(project,input_path,lexicon_path,runner=fake_hbg_audio_runner)
            self.assertEqual(first.status,"created")
            self.assertEqual(second.status,"unchanged")



    def test_existing_media_report_tamper_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project,input_path,lexicon_path=self.build(Path(temp))
            generate_audio_stage(project,input_path,lexicon_path,runner=fake_hbg_audio_runner)
            manifest_path=project/"04_audio/AUDIO_PRELIMINARY_MANIFEST.json"
            manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["media_report"]["tampered"]=True
            manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
            with self.assertRaises(AudioStageConflict):
                generate_audio_stage(project,input_path,lexicon_path,runner=fake_hbg_audio_runner)

    def test_existing_tool_provenance_tamper_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project,input_path,lexicon_path=self.build(Path(temp))
            generate_audio_stage(project,input_path,lexicon_path,runner=fake_hbg_audio_runner)
            manifest_path=project/"04_audio/AUDIO_PRELIMINARY_MANIFEST.json"
            manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["tool_provenance"]["node"]="attacker"
            manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
            with self.assertRaises(AudioStageConflict):
                generate_audio_stage(project,input_path,lexicon_path,runner=fake_hbg_audio_runner)

    def test_publish_rejects_symlinked_output_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project,input_path,lexicon_path=self.build(base)
            outside = base / "outside-audio"
            outside.mkdir()
            audio = project / "assets/audio"
            if audio.exists():
                import shutil
                shutil.rmtree(audio)
            audio.parent.mkdir(parents=True,exist_ok=True)
            symlink_or_skip(self,audio,outside,target_is_directory=True)
            with self.assertRaisesRegex((AudioStageError, AudioStageConflict), "symlink|outside|unsafe"):
                generate_audio_stage(project,input_path,lexicon_path,runner=fake_hbg_audio_runner)
            self.assertEqual(list(outside.iterdir()), [])
            self.assertFalse((project/"04_audio/AUDIO_PRELIMINARY_MANIFEST.json").exists())

    def test_runner_runtime_error_is_wrapped_as_audio_stage_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project,input_path,lexicon_path=self.build(Path(temp))
            def exploding(_staging: Path) -> None:
                raise RuntimeError("provider exploded")
            with self.assertRaises(AudioStageError) as raised:
                generate_audio_stage(project,input_path,lexicon_path,runner=exploding)
            self.assertIn("provider exploded",str(raised.exception))
            self.assertIsInstance(raised.exception.__cause__,RuntimeError)
            self.assertFalse((project/"04_audio/AUDIO_PRELIMINARY_MANIFEST.json").exists())


if __name__=="__main__": unittest.main()
