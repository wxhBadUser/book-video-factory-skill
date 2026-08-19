from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from book_video_factory.audio_stage.captions import CaptionAlignmentError, restore_display_captions
from book_video_factory.audio_stage.compiler import (
    AudioStageConflict,
    AudioStageError,
    finalize_audio_stage,
    generate_audio_stage,
)
from book_video_factory.audio_stage.contracts import (
    AudioStageContractError,
    validate_audio_stage_input,
    validate_pronunciation_lexicon,
    verify_phase4_prerequisites,
)
from book_video_factory.audio_stage.media_probe import (
    MediaValidationError,
    VttCue,
    parse_vtt,
    validate_vtt,
)
from book_video_factory.audio_stage.pronunciation import (
    PronunciationError,
    compile_spoken_script,
    map_spoken_interval_to_display,
)
from book_video_factory.audio_stage.status import audio_stage_status
from book_video_factory.audio_stage.storyboard_plan import (
    StoryboardPlanError,
    validate_storyboard_audio_plan,
)
from book_video_factory.hbg_bridge.provenance import _verify_vendor
from book_video_factory.manifests import sha256_file
from phase2_fixture_factory import write_json
from phase4_fixture_factory import (
    _ffmpeg_silence,
    build_approved_phase3_project,
    build_storyboard_audio_plan,
    fake_hbg_audio_runner,
    symlink_or_skip,
    write_phase4_inputs,
)
from test_phase4_storyboard_plan import _extend_meta, _project, _valid

def _repository_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "scripts/verify_vfinal_architecture.py").is_file():
            return parent
    raise AssertionError("repository root was not found")


REPO = _repository_root()


class PhaseFourAdversarialMutations(unittest.TestCase):
    """Numbered attacks must fail closed or preserve a stated invariant."""

    def _blocked(self, number: int, label: str, error: type[BaseException], operation) -> None:
        with self.subTest(attack=f"{number:03d}-{label}"):
            with self.assertRaises(error):
                operation()
            print(f"ATTACK {number:03d} BLOCKED {label}")

    def _minimal_input_fixture(self, root: Path) -> tuple[dict, dict]:
        files = {
            "SCRIPT.md": b"script",
            "PROJECT_SPEC.json": b"{}\n",
            "HBG_STYLE.json": b"{}\n",
            "STORYBOARD_BASE.json": b"[]\n",
            "03_images_生成图片/ANCHOR_APPROVAL.json": b"{}\n",
            "04_audio/PRONUNCIATION_LEXICON.json": b"{}\n",
        }
        for relative, content in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        payload = {
            "schema_version": "audio-stage-input.v1",
            "release_id": "r1",
            "provider": "edge-tts",
            "voice": "zh-CN-YunjianNeural",
            "body_rate": "+0%",
            "lead_rate": "+0%",
            "reveal_rate": "+0%",
            "pitch": "+0Hz",
            "lead_text": "名著值得读，但很多人读不进去。",
            "reveal_text": "《老人与海》，海明威。",
            "caption_min_chars": 6,
            "caption_max_chars": 18,
            "caption_min_duration": 0.55,
            "lead_start": 0.05,
            "flash_gap_after_lead": 0.02,
            "flash_duration": 1.667,
            "reveal_hold": 0.12,
            "body_gap": 0.22,
            "body_mode": "continuous",
            "bindings": {
                "script_md_sha256": sha256_file(root / "SCRIPT.md"),
                "project_spec_sha256": sha256_file(root / "PROJECT_SPEC.json"),
                "hbg_style_sha256": sha256_file(root / "HBG_STYLE.json"),
                "storyboard_base_sha256": sha256_file(root / "STORYBOARD_BASE.json"),
                "visual_approval_sha256": sha256_file(root / "03_images_生成图片/ANCHOR_APPROVAL.json"),
                "pronunciation_lexicon_sha256": sha256_file(root / "04_audio/PRONUNCIATION_LEXICON.json"),
            },
        }
        lexicon = {
            "schema_version": "pronunciation-lexicon.v1",
            "release_id": "r1",
            "entries": [{
                "entry_id": "hemingway",
                "display": "海明威",
                "spoken": "海明维",
                "scope": "body",
                "occurrence_policy": {"mode": "all"},
                "note": "作者名读音",
            }],
        }
        return payload, lexicon

    def test_001_to_076_contract_timeline_and_architecture_attacks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline_input, baseline_lexicon = self._minimal_input_fixture(root)

            input_mutations = [
                (1, "unknown-input-field", lambda p: p.__setitem__("extra", True)),
                (2, "wrong-provider", lambda p: p.__setitem__("provider", "local-tts")),
                (3, "invalid-edge-voice", lambda p: p.__setitem__("voice", "voice")),
                (4, "invalid-body-rate", lambda p: p.__setitem__("body_rate", "fast")),
                (5, "invalid-lead-rate", lambda p: p.__setitem__("lead_rate", "0%")),
                (6, "invalid-pitch", lambda p: p.__setitem__("pitch", "0Hz")),
                (7, "noncontinuous-body", lambda p: p.__setitem__("body_mode", "chunks")),
                (8, "caption-min-greater-max", lambda p: p.update(caption_min_chars=19, caption_max_chars=18)),
                (9, "negative-opening-time", lambda p: p.__setitem__("body_gap", -1)),
                (10, "local-path-in-lead", lambda p: p.__setitem__("lead_text", "/Users/alice/private/audio")),
                (11, "secret-in-reveal", lambda p: p.__setitem__("reveal_text", "sk-abcdefghijklmnopqrstuvwxyz123456")),
                (12, "stale-script-binding", lambda p: p["bindings"].__setitem__("script_md_sha256", "0" * 64)),
                (13, "unknown-binding", lambda p: p["bindings"].__setitem__("unknown_sha256", "0" * 64)),
                (14, "invalid-release-id-whitespace", lambda p: p.__setitem__("release_id", " r1")),
                (15, "ssml-opening-copy", lambda p: p.__setitem__("lead_text", "<speak>名著</speak>")),
            ]
            for number, label, mutate in input_mutations:
                candidate = deepcopy(baseline_input)
                mutate(candidate)
                self._blocked(number, label, AudioStageContractError, lambda c=candidate: validate_audio_stage_input(root, c))

            lexicon_mutations = [
                (16, "unknown-lexicon-field", lambda l: l.__setitem__("extra", 1)),
                (17, "duplicate-entry-id", lambda l: l["entries"].append(deepcopy(l["entries"][0]))),
                (18, "duplicate-display-scope", lambda l: l["entries"].append({**deepcopy(l["entries"][0]), "entry_id": "second"})),
                (19, "ssml-spoken", lambda l: l["entries"][0].__setitem__("spoken", "<say-as>海明维</say-as>")),
                (20, "shell-command-spoken", lambda l: l["entries"][0].__setitem__("spoken", "海明维; rm -rf /")),
                (21, "local-path-note", lambda l: l["entries"][0].__setitem__("note", "C:\\Users\\alice\\voice")),
                (22, "secret-note", lambda l: l["entries"][0].__setitem__("note", "api_key=secretvalue")),
                (23, "invalid-scope", lambda l: l["entries"][0].__setitem__("scope", "all")),
                (24, "zero-exact-count", lambda l: l["entries"][0].__setitem__("occurrence_policy", {"mode": "exact", "count": 0})),
                (25, "unknown-occurrence-mode", lambda l: l["entries"][0].__setitem__("occurrence_policy", {"mode": "sometimes"})),
                (26, "newline-in-spoken", lambda l: l["entries"][0].__setitem__("spoken", "海明\n维")),
                (27, "empty-display", lambda l: l["entries"][0].__setitem__("display", "")),
                (28, "whitespace-entry-id", lambda l: l["entries"][0].__setitem__("entry_id", " bad")),
                (29, "boolean-count", lambda l: l["entries"][0].__setitem__("occurrence_policy", {"mode": "exact", "count": True})),
                (30, "unknown-entry-field", lambda l: l["entries"][0].__setitem__("provider", "edge")),
            ]
            for number, label, mutate in lexicon_mutations:
                candidate = deepcopy(baseline_lexicon)
                mutate(candidate)
                self._blocked(number, label, AudioStageContractError, lambda c=candidate: validate_pronunciation_lexicon(root, c))

            no_op = deepcopy(baseline_lexicon); no_op["entries"][0]["spoken"] = "海明威"
            punctuation = deepcopy(baseline_lexicon); punctuation["entries"][0]["spoken"] = "海明维。"
            exact_missing = deepcopy(baseline_lexicon); exact_missing["entries"][0]["occurrence_policy"] = {"mode": "exact", "count": 2}
            shadowed = deepcopy(baseline_lexicon); shadowed["entries"].append({
                "entry_id": "short", "display": "海明", "spoken": "海鸣", "scope": "body",
                "occurrence_policy": {"mode": "all"}, "note": "short",
            })
            pronunciation_attacks = [
                (31, "no-op-pronunciation", lambda: compile_spoken_script("海明威", no_op)),
                (32, "punctuation-changing-pronunciation", lambda: compile_spoken_script("海明威", punctuation)),
                (33, "exact-occurrence-mismatch", lambda: compile_spoken_script("海明威", exact_missing)),
                (34, "shadowed-short-entry", lambda: compile_spoken_script("海明威", shadowed)),
                (35, "empty-display-script", lambda: compile_spoken_script("", baseline_lexicon)),
            ]
            for number, label, operation in pronunciation_attacks:
                self._blocked(number, label, PronunciationError, operation)
            compilation = compile_spoken_script("海明威来到海边。", baseline_lexicon)
            interval_attacks = [
                (36, "negative-spoken-offset", lambda: map_spoken_interval_to_display(compilation, -1, 1)),
                (37, "reversed-spoken-offset", lambda: map_spoken_interval_to_display(compilation, 3, 2)),
                (38, "out-of-range-spoken-offset", lambda: map_spoken_interval_to_display(compilation, 0, 999)),
                (39, "boolean-spoken-offset", lambda: map_spoken_interval_to_display(compilation, True, 2)),
            ]
            for number, label, operation in interval_attacks:
                self._blocked(number, label, PronunciationError, operation)

            vtt_cases = {
                40: ("missing-vtt-header", "00:00:00.000 --> 00:00:00.500\n海明维\n"),
                41: ("vtt-without-cues", "WEBVTT\n"),
                42: ("malformed-vtt-time", "WEBVTT\n\nBAD --> 00:00:00.500\n海明维\n"),
                43: ("vtt-minute-overflow", "WEBVTT\n\n00:60:00.000 --> 00:60:00.500\n海明维\n"),
                44: ("vtt-block-without-timing", "WEBVTT\n\n海明维\n"),
                45: ("empty-vtt-text", "WEBVTT\n\n00:00:00.000 --> 00:00:00.500\n\n"),
            }
            for number, (label, text) in vtt_cases.items():
                path = root / f"attack-{number}.vtt"; path.write_text(text, encoding="utf-8")
                self._blocked(number, label, MediaValidationError, lambda p=path: parse_vtt(p))

            vtt_validation_attacks = [
                (46, "overlapping-vtt", [VttCue(0, 1, "海明"), VttCue(0.5, 1.5, "维")], 2.0, "海明维"),
                (47, "backward-vtt", [VttCue(1, 2, "海明"), VttCue(0, 1, "维")], 2.0, "海明维"),
                (48, "out-of-range-vtt", [VttCue(0, 3, "海明维")], 2.0, "海明维"),
                (49, "punctuation-only-vtt", [VttCue(0, 1, "。")], 1.0, "海明维"),
                (50, "omitted-vtt-text", [VttCue(0, 1, "海明")], 1.0, "海明维"),
                (51, "duplicated-vtt-text", [VttCue(0, 1, "海明维海明维")], 1.0, "海明维"),
                (52, "reordered-vtt-text", [VttCue(0, 1, "维海明")], 1.0, "海明维"),
                (53, "zero-duration-master", [VttCue(0, 1, "海明维")], 0.0, "海明维"),
            ]
            for number, label, cues, duration, expected in vtt_validation_attacks:
                self._blocked(number, label, MediaValidationError, lambda c=cues,d=duration,e=expected: validate_vtt(c, duration=d, expected_text=e))

            raw_good = [VttCue(0.0, 0.7, "海明维"), VttCue(0.7, 1.4, "来到海边。")]
            caption_attacks = [
                (54, "empty-caption-cues", [], 1, 18, 0.2),
                (55, "unknown-caption-text", [VttCue(0, 1, "陌生文本")], 1, 18, 0.2),
                (56, "reordered-caption-text", list(reversed(raw_good)), 1, 18, 0.2),
                (57, "omitted-caption-tail", [raw_good[0]], 1, 18, 0.2),
                (58, "duplicated-caption-text", [raw_good[0], raw_good[0], raw_good[1]], 1, 18, 0.2),
                (59, "caption-below-min-length", raw_good, 20, 30, 0.2),
                (60, "caption-above-max-length", raw_good, 1, 2, 0.2),
                (61, "caption-below-min-duration", raw_good, 1, 18, 1.0),
            ]
            for number, label, cues, min_chars, max_chars, min_duration in caption_attacks:
                self._blocked(
                    number, label, CaptionAlignmentError,
                    lambda c=cues,mi=min_chars,ma=max_chars,md=min_duration: restore_display_captions(
                        c, compilation, min_chars=mi, max_chars=ma, min_duration=md
                    ),
                )

            storyboard_root = root / "storyboard"
            storyboard_root.mkdir()
            project, preliminary, meta, beats = _project(storyboard_root)
            plan = _valid(project)
            storyboard_mutations = []
            case=deepcopy(plan); case["beat_dispositions"].pop(); storyboard_mutations.append((62,"missing-beat-disposition",case,meta))
            case=deepcopy(plan); case["beat_dispositions"].append(deepcopy(case["beat_dispositions"][0])); storyboard_mutations.append((63,"duplicate-beat-disposition",case,meta))
            case=deepcopy(plan); case["beat_dispositions"][0]["beat_id"]="B999"; storyboard_mutations.append((64,"unknown-beat-disposition",case,meta))
            case=deepcopy(plan); case["shots"][0]["source_beat_ids"]=["B002"]; storyboard_mutations.append((65,"reverse-beat-mapping",case,meta))
            case=deepcopy(plan); case["shots"][2]["caption_ids"].pop(); storyboard_mutations.append((66,"unbound-caption",case,meta))
            case=deepcopy(plan); case["shots"][1]["caption_ids"].append("caption-0001"); storyboard_mutations.append((67,"duplicated-caption-binding",case,meta))
            case=deepcopy(plan); case["shots"][0]["risk_flags"]=["handz"]; storyboard_mutations.append((68,"unknown-risk-flag",case,meta))
            case=deepcopy(plan); case["shots"][1]["generation_mode"]="2x2"; storyboard_mutations.append((69,"high-risk-grid-route",case,meta))
            case=deepcopy(plan); case["shots"][0]["anchor_refs"]=[]; storyboard_mutations.append((70,"participant-without-anchor",case,meta))
            case=deepcopy(plan); case["shots"][0]["forbidden_entities"]=["老人"]; storyboard_mutations.append((71,"required-forbidden-overlap",case,meta))
            meta_long=_extend_meta(meta,21); case=deepcopy(plan); case["shots"][0]["caption_ids"]=[f"caption-{i:04d}" for i in range(1,12)]; case["shots"][1]["caption_ids"]=[f"caption-{i:04d}" for i in range(12,17)]; case["shots"][1]["cue"]="字幕12"; case["shots"][2]["caption_ids"]=[f"caption-{i:04d}" for i in range(17,22)]; case["shots"][2]["cue"]="字幕17"; storyboard_mutations.append((72,"long-shot-without-strong-load",case,meta_long))
            for number,label,candidate,candidate_meta in storyboard_mutations:
                self._blocked(number,label,StoryboardPlanError,lambda c=candidate,m=candidate_meta: validate_storyboard_audio_plan(project,c,preliminary,audio_meta=m,phase2_beats=beats))

            scanner_path = REPO / "scripts/verify_phase4_audio_stage.py"
            import importlib.util
            spec=importlib.util.spec_from_file_location("phase4_mutation_scanner",scanner_path); assert spec and spec.loader
            scanner=importlib.util.module_from_spec(spec); spec.loader.exec_module(scanner)
            architecture_attacks = [
                (73,"private-edge-client","tts_client.py","import edge_tts\nedge_tts.Communicate('x','y')\n",scanner.scan_local_tts_implementations,"local_tts_implementation"),
                (74,"fake-audio-bytes","fake_audio.py","Path('narration.m4a').write_bytes(b'fake audio')\n",scanner.scan_fake_media_writers,"fake_audio_writer"),
                (75,"arbitrary-hbg-script","bad.py","run_hbg_node(user_script, project, capability='phase4')\n",scanner.scan_hbg_capability_boundary,"arbitrary_hbg_invocation"),
                (76,"estimated-timing","timing.py","timing_source = 'estimated'\nduration = characters / 232\n",scanner.scan_estimated_timing,"estimated_audio_timing"),
            ]
            for number,label,name,text,scan,expected in architecture_attacks:
                attack_root=root/f"arch-{number}"; path=attack_root/"book_video_factory/src/book_video_factory/audio_stage"/name; path.parent.mkdir(parents=True); path.write_text(text,encoding="utf-8")
                with self.subTest(attack=f"{number:03d}-{label}"):
                    ids={item["check_id"] for item in scan(attack_root)}
                    self.assertIn(expected,ids)
                    print(f"ATTACK {number:03d} BLOCKED {label}")

    def test_077_to_084_phase0_to_phase4_transaction_attacks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary)
            project=build_approved_phase3_project(base)
            input_path,lexicon_path=write_phase4_inputs(project)

            approval=project/"03_images_生成图片/ANCHOR_APPROVAL.json"
            approval_bytes=approval.read_bytes()
            approval.unlink()
            self._blocked(77,"missing-phase3-approval",AudioStageContractError,lambda: verify_phase4_prerequisites(project,"r1"))
            approval.write_bytes(approval_bytes)

            approval_payload=json.loads(approval.read_text(encoding="utf-8")); approval_payload["release_id"]="r0"; write_json(approval,approval_payload)
            self._blocked(78,"stale-phase3-approval",AudioStageContractError,lambda: verify_phase4_prerequisites(project,"r1"))
            approval.write_bytes(approval_bytes)

            repo_copy=base/"repo-copy"; (repo_copy/"vendor").mkdir(parents=True)
            shutil.copytree(REPO/"vendor/hbg-life-simulation",repo_copy/"vendor/hbg-life-simulation")
            target=repo_copy/"vendor/hbg-life-simulation/scripts/build_narration.mjs"; target.write_text(target.read_text(encoding="utf-8")+"\n// drift\n",encoding="utf-8")
            self._blocked(79,"hbg-vendor-drift",RuntimeError,lambda: _verify_vendor(repo_copy))

            with mock.patch("book_video_factory.audio_stage.compiler._edge_tts_available",return_value=False):
                self._blocked(80,"missing-real-edge-tts",AudioStageError,lambda: generate_audio_stage(project,input_path,lexicon_path))
            self.assertFalse((project/"audio_meta.json").exists())

            def broken_runner(staging: Path) -> None:
                (staging/"audio_meta.json").write_text("{}",encoding="utf-8")
            self._blocked(81,"partial-hbg-output-rollback",AudioStageError,lambda: generate_audio_stage(project,input_path,lexicon_path,runner=broken_runner))
            self.assertFalse((project/"04_audio/AUDIO_PRELIMINARY_MANIFEST.json").exists())

            generate_audio_stage(project,input_path,lexicon_path,runner=fake_hbg_audio_runner)
            meta=project/"audio_meta.json"; meta_bytes=meta.read_bytes(); meta.write_bytes(meta_bytes+b"tamper")
            self._blocked(82,"preliminary-output-tamper",AudioStageConflict,lambda: generate_audio_stage(project,input_path,lexicon_path,runner=fake_hbg_audio_runner))
            meta.write_bytes(meta_bytes)

            plan_path=project/"04_audio/STORYBOARD_AUDIO_PLAN.json"; write_json(plan_path,build_storyboard_audio_plan(project))
            plan=json.loads(plan_path.read_text(encoding="utf-8")); good_plan=deepcopy(plan); plan["preliminary_manifest_sha256"]="0"*64; write_json(plan_path,plan)
            self._blocked(83,"stale-preliminary-plan",AudioStageError,lambda: finalize_audio_stage(project,plan_path,runner=fake_hbg_audio_runner))
            write_json(plan_path,good_plan)

            def drifting_runner(staging: Path) -> None:
                fake_hbg_audio_runner(staging)
                with (staging/"assets/audio/narration.m4a").open("ab") as stream: stream.write(b"drift")
            self._blocked(84,"finalization-audio-drift",AudioStageError,lambda: finalize_audio_stage(project,plan_path,runner=drifting_runner))
            self.assertFalse((project/"04_audio/AUDIO_STAGE_MANIFEST.json").exists())

    def test_085_to_086_final_manifest_attacks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary)
            project=build_approved_phase3_project(base)
            input_path,lexicon_path=write_phase4_inputs(project)
            generate_audio_stage(project,input_path,lexicon_path,runner=fake_hbg_audio_runner)
            plan_path=project/"04_audio/STORYBOARD_AUDIO_PLAN.json"
            write_json(plan_path,build_storyboard_audio_plan(project))
            finalize_audio_stage(project,plan_path,runner=fake_hbg_audio_runner)

            bindings=project/"04_audio/CAPTION_BINDINGS.json"; bindings_bytes=bindings.read_bytes(); bindings.write_bytes(bindings_bytes+b"tamper")
            self._blocked(85,"post-finalization-output-tamper",AudioStageConflict,lambda: finalize_audio_stage(project,plan_path,runner=fake_hbg_audio_runner))
            self.assertEqual(audio_stage_status(project,"r1"),"blocked_by_audio_manifest_integrity")
            bindings.write_bytes(bindings_bytes)

            final_manifest=project/"04_audio/AUDIO_STAGE_MANIFEST.json"; payload=json.loads(final_manifest.read_text(encoding="utf-8")); payload["next_stage_status"]="ready_for_final_video"; write_json(final_manifest,payload)
            with self.subTest(attack="086-fake-success-status"):
                self.assertEqual(audio_stage_status(project,"r1"),"blocked_by_audio_manifest_integrity")
                print("ATTACK 086 BLOCKED fake-success-status")

    def test_087_to_089_project_input_path_attacks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)

            project = build_approved_phase3_project(base / "external")
            input_path, lexicon_path = write_phase4_inputs(project)
            outside_input = base / "outside-input.json"
            outside_input.write_bytes(input_path.read_bytes())
            self._blocked(87,"external-audio-input",AudioStageError,lambda: generate_audio_stage(project,outside_input,lexicon_path,runner=fake_hbg_audio_runner))

            project = build_approved_phase3_project(base / "input-link")
            input_path, lexicon_path = write_phase4_inputs(project)
            outside_input = base / "linked-input.json"
            outside_input.write_bytes(input_path.read_bytes())
            input_path.unlink(); symlink_or_skip(self,input_path,outside_input)
            self._blocked(88,"symlinked-audio-input",AudioStageError,lambda: generate_audio_stage(project,input_path,lexicon_path,runner=fake_hbg_audio_runner))

            project = build_approved_phase3_project(base / "lexicon-link")
            input_path, lexicon_path = write_phase4_inputs(project)
            outside_lexicon = base / "linked-lexicon.json"
            outside_lexicon.write_bytes(lexicon_path.read_bytes())
            lexicon_path.unlink(); symlink_or_skip(self,lexicon_path,outside_lexicon)
            self._blocked(89,"symlinked-pronunciation-lexicon",AudioStageError,lambda: generate_audio_stage(project,input_path,lexicon_path,runner=fake_hbg_audio_runner))

    def test_090_metadata_tamper_is_checked_before_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = build_approved_phase3_project(base)
            input_path, lexicon_path = write_phase4_inputs(project)
            generate_audio_stage(project,input_path,lexicon_path,runner=fake_hbg_audio_runner)
            manifest_path = project / "04_audio/AUDIO_PRELIMINARY_MANIFEST.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["external_edge_service_exercised"] = True
            write_json(manifest_path,manifest)
            calls: list[int] = []
            def forbidden_runner(_staging: Path) -> None:
                calls.append(1)
                raise AssertionError("provider reran before evidence validation")
            self._blocked(90,"metadata-tamper-before-provider",AudioStageConflict,lambda: generate_audio_stage(project,input_path,lexicon_path,runner=forbidden_runner))
            self.assertEqual(calls,[])

    def test_091_first_generation_does_not_reuse_unproven_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = build_approved_phase3_project(base)
            input_path, lexicon_path = write_phase4_inputs(project)
            poison = project / "assets/audio/narration-full.txt"
            poison.parent.mkdir(parents=True,exist_ok=True); poison.write_text("poisoned",encoding="utf-8")
            def no_cache_runner(staging: Path) -> None:
                if (staging / "assets/audio/narration-full.txt").exists():
                    raise AssertionError("unproven cache reached staging")
                fake_hbg_audio_runner(staging)
            result = generate_audio_stage(project,input_path,lexicon_path,runner=no_cache_runner)
            with self.subTest(attack="091-unproven-cache-reuse"):
                self.assertEqual(result.status,"created")
                print("ATTACK 091 BLOCKED unproven-cache-reuse")

    def test_092_body_mp3_duration_attack(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = build_approved_phase3_project(base)
            input_path, lexicon_path = write_phase4_inputs(project)
            def wrong_duration(staging: Path) -> None:
                fake_hbg_audio_runner(staging)
                _ffmpeg_silence(staging / "assets/audio/narration-full.mp3",1.0,"libmp3lame")
            self._blocked(92,"body-mp3-duration-mismatch",AudioStageError,lambda: generate_audio_stage(project,input_path,lexicon_path,runner=wrong_duration))

    def test_093_missing_opening_audio_attack(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = build_approved_phase3_project(base)
            input_path, lexicon_path = write_phase4_inputs(project)
            def missing_opening(staging: Path) -> None:
                fake_hbg_audio_runner(staging)
                (staging / "assets/audio/opening/lead-natural.mp3").unlink()
            self._blocked(93,"missing-opening-lead",AudioStageError,lambda: generate_audio_stage(project,input_path,lexicon_path,runner=missing_opening))

    def test_094_symlinked_bound_evidence_attack(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = build_approved_phase3_project(base)
            input_path, _lexicon_path = write_phase4_inputs(project)
            payload = json.loads(input_path.read_text(encoding="utf-8"))
            script = project / "SCRIPT.md"; outside_script = base / "outside-script.md"
            outside_script.write_bytes(script.read_bytes()); script.unlink(); symlink_or_skip(self,script,outside_script)
            payload["bindings"]["script_md_sha256"] = sha256_file(outside_script)
            self._blocked(94,"symlinked-bound-evidence",AudioStageContractError,lambda: validate_audio_stage_input(project,payload))

    def test_095_to_096_final_status_path_attacks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = build_approved_phase3_project(base)
            input_path, lexicon_path = write_phase4_inputs(project)
            generate_audio_stage(project,input_path,lexicon_path,runner=fake_hbg_audio_runner)
            plan_path = project / "04_audio/STORYBOARD_AUDIO_PLAN.json"
            write_json(plan_path,build_storyboard_audio_plan(project))
            finalize_audio_stage(project,plan_path,runner=fake_hbg_audio_runner)
            bindings = project / "04_audio/CAPTION_BINDINGS.json"; binding_bytes = bindings.read_bytes()
            outside_bindings = base / "outside-bindings.json"; outside_bindings.write_bytes(binding_bytes)
            bindings.unlink(); symlink_or_skip(self,bindings,outside_bindings)
            with self.subTest(attack="095-symlinked-final-output"):
                self.assertEqual(audio_stage_status(project,"r1"),"blocked_by_audio_manifest_integrity")
                print("ATTACK 095 BLOCKED symlinked-final-output")
            bindings.unlink(); bindings.write_bytes(binding_bytes)

            final_manifest_path = project / "04_audio/AUDIO_STAGE_MANIFEST.json"
            final_manifest = json.loads(final_manifest_path.read_text(encoding="utf-8"))
            original_stage = project / final_manifest["stage_manifest_path"]
            outside_stage = base / "outside-stage.json"; outside_stage.write_bytes(original_stage.read_bytes())
            final_manifest["stage_manifest_path"] = "../outside-stage.json"
            final_manifest["stage_manifest_sha256"] = sha256_file(outside_stage)
            write_json(final_manifest_path,final_manifest)
            with self.subTest(attack="096-final-stage-path-escape"):
                self.assertEqual(audio_stage_status(project,"r1"),"blocked_by_audio_manifest_integrity")
                print("ATTACK 096 BLOCKED final-stage-path-escape")

    def test_097_to_098_compiled_text_binding_attacks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = build_approved_phase3_project(base / "opening-text")
            input_path, lexicon_path = write_phase4_inputs(project)
            def coordinated_wrong_opening(staging: Path) -> None:
                fake_hbg_audio_runner(staging)
                opening = staging / "assets/audio/opening"
                wrong = "与项目输入无关的开头"
                (opening / "lead-natural.txt").write_text(wrong + "\n",encoding="utf-8")
                (opening / "lead-natural.vtt").write_text(
                    "WEBVTT\n\n00:00:00.000 --> 00:00:00.500\n" + wrong + "\n",
                    encoding="utf-8",
                )
            self._blocked(97,"coordinated-opening-text-vtt-tamper",AudioStageError,lambda: generate_audio_stage(project,input_path,lexicon_path,runner=coordinated_wrong_opening))

            project = build_approved_phase3_project(base / "body-pronunciation")
            input_path, lexicon_path = write_phase4_inputs(project)
            lexicon = json.loads(lexicon_path.read_text(encoding="utf-8"))
            lexicon["entries"].append({
                "entry_id":"old-man-body","display":"老人","spoken":"老仁","scope":"body",
                "occurrence_policy":{"mode":"all"},"note":"正文发音攻击夹具",
            })
            write_json(lexicon_path,lexicon)
            payload = json.loads(input_path.read_text(encoding="utf-8"))
            payload["bindings"]["pronunciation_lexicon_sha256"] = sha256_file(lexicon_path)
            write_json(input_path,payload)
            generate_audio_stage(project,input_path,lexicon_path,runner=fake_hbg_audio_runner)
            result = generate_audio_stage(project,input_path,lexicon_path,runner=fake_hbg_audio_runner)
            with self.subTest(attack="098-display-meta-cannot-replace-raw-spoken-evidence"):
                self.assertEqual(result.status,"unchanged")
                print("ATTACK 098 BLOCKED display-meta-cannot-replace-raw-spoken-evidence")

    def test_099_to_103_final_status_structural_attacks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = build_approved_phase3_project(base)
            input_path, lexicon_path = write_phase4_inputs(project)
            generate_audio_stage(project,input_path,lexicon_path,runner=fake_hbg_audio_runner)
            plan_path = project / "04_audio/STORYBOARD_AUDIO_PLAN.json"
            write_json(plan_path,build_storyboard_audio_plan(project))
            finalize_audio_stage(project,plan_path,runner=fake_hbg_audio_runner)
            manifest_path = project / "04_audio/AUDIO_STAGE_MANIFEST.json"
            original_manifest = manifest_path.read_bytes()
            original = json.loads(original_manifest)
            stage_path = project / original["stage_manifest_path"]
            original_stage = stage_path.read_bytes()

            def expect_blocked(number: int, label: str) -> None:
                with self.subTest(attack=f"{number:03d}-{label}"):
                    self.assertEqual(audio_stage_status(project,"r1"),"blocked_by_audio_manifest_integrity")
                    print(f"ATTACK {number:03d} BLOCKED {label}")

            payload = deepcopy(original)
            payload["external_edge_service_exercised"] = not payload["external_edge_service_exercised"]
            write_json(manifest_path,payload)
            expect_blocked(99,"final-provenance-flag-mismatch")
            manifest_path.write_bytes(original_manifest)

            stage = json.loads(original_stage)
            stage["producer"] = {"tool":"attacker"}
            write_json(stage_path,stage)
            payload = deepcopy(original)
            stage_sha = sha256_file(stage_path)
            payload["final_output_hashes"][original["stage_manifest_path"]] = stage_sha
            payload["stage_manifest_sha256"] = stage_sha
            write_json(manifest_path,payload)
            expect_blocked(100,"coordinated-final-stage-producer-tamper")
            stage_path.write_bytes(original_stage); manifest_path.write_bytes(original_manifest)

            payload = deepcopy(original); payload["caption_timeline_sha256"] = "0"*64
            write_json(manifest_path,payload)
            expect_blocked(101,"fake-caption-timeline-fingerprint")
            manifest_path.write_bytes(original_manifest)

            stage = json.loads(original_stage); stage["outputs"] = stage["outputs"][:-1]
            write_json(stage_path,stage)
            payload = deepcopy(original)
            stage_sha = sha256_file(stage_path)
            payload["final_output_hashes"][original["stage_manifest_path"]] = stage_sha
            payload["stage_manifest_sha256"] = stage_sha
            write_json(manifest_path,payload)
            expect_blocked(102,"final-stage-output-omission")
            stage_path.write_bytes(original_stage); manifest_path.write_bytes(original_manifest)

            payload = deepcopy(original)
            removed = next(iter(payload["preliminary_audio_hashes"]))
            payload["preliminary_audio_hashes"].pop(removed)
            write_json(manifest_path,payload)
            expect_blocked(103,"incomplete-preliminary-audio-binding")

    def test_104_preliminary_unstructured_stage_check_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = build_approved_phase3_project(Path(temporary))
            input_path, lexicon_path = write_phase4_inputs(project)
            generate_audio_stage(project,input_path,lexicon_path,runner=fake_hbg_audio_runner)
            manifest_path = project / "04_audio/AUDIO_PRELIMINARY_MANIFEST.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            stage_path = project / manifest["stage_manifest_path"]
            stage = json.loads(stage_path.read_text(encoding="utf-8"))
            stage["checks"].append("unverified-extra-check")
            write_json(stage_path, stage)
            stage_sha = sha256_file(stage_path)
            manifest["output_hashes"][manifest["stage_manifest_path"]] = stage_sha
            manifest["stage_manifest_sha256"] = stage_sha
            write_json(manifest_path, manifest)
            with self.subTest(attack="104-preliminary-unstructured-extra-check"):
                self.assertEqual(audio_stage_status(project,"r1"),"blocked_by_audio_manifest_integrity")
                print("ATTACK 104 BLOCKED preliminary-unstructured-extra-check")

    def test_105_final_unstructured_stage_check_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = build_approved_phase3_project(Path(temporary))
            input_path, lexicon_path = write_phase4_inputs(project)
            generate_audio_stage(project,input_path,lexicon_path,runner=fake_hbg_audio_runner)
            plan_path = project / "04_audio/STORYBOARD_AUDIO_PLAN.json"
            write_json(plan_path,build_storyboard_audio_plan(project))
            finalize_audio_stage(project,plan_path,runner=fake_hbg_audio_runner)
            manifest_path = project / "04_audio/AUDIO_STAGE_MANIFEST.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            stage_path = project / manifest["stage_manifest_path"]
            stage = json.loads(stage_path.read_text(encoding="utf-8"))
            stage["checks"].append("unverified-extra-check")
            write_json(stage_path, stage)
            stage_sha = sha256_file(stage_path)
            manifest["final_output_hashes"][manifest["stage_manifest_path"]] = stage_sha
            manifest["stage_manifest_sha256"] = stage_sha
            write_json(manifest_path, manifest)
            with self.subTest(attack="105-final-unstructured-extra-check"):
                self.assertEqual(audio_stage_status(project,"r1"),"blocked_by_audio_manifest_integrity")
                print("ATTACK 105 BLOCKED final-unstructured-extra-check")


if __name__ == "__main__":
    unittest.main()
