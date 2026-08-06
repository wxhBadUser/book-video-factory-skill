from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from book_video_factory.imagegen_api_batch import (
    ImageGenBatchError,
    run_imagegen_api_batch,
)


class ImageGenApiBatchTests(unittest.TestCase):
    def fixture(self, base: Path) -> tuple[Path, Path, Path]:
        codex_home = base / "codex-home"
        cli = codex_home / "skills/.system/imagegen/scripts/image_gen.py"
        cli.parent.mkdir(parents=True)
        cli.write_text("# installed system imagegen cli\n", encoding="utf-8")
        input_path = base / "prompts.jsonl"
        input_path.write_text('{"prompt":"one"}\n', encoding="utf-8")
        return codex_home, cli, input_path

    def test_delegates_exact_generate_batch_command_without_http_client(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            codex_home, cli, input_path = self.fixture(base)
            out_dir = base / "outputs"
            env = {
                "CODEX_HOME": str(codex_home),
                "OPENAI_API_KEY": "secret-key-for-test",
                "OPENAI_BASE_URL": "https://image.example.invalid/v1",
            }
            completed = subprocess.CompletedProcess([], 0, stdout="generated\n", stderr="")
            with mock.patch.dict("os.environ", env, clear=True), mock.patch(
                "book_video_factory.imagegen_api_batch.subprocess.run", return_value=completed
            ) as runner:
                result = run_imagegen_api_batch(input_path, out_dir, concurrency=5)
            self.assertEqual(result.returncode, 0)
            command = runner.call_args.args[0]
            self.assertEqual(command[1:3], [str(cli), "generate-batch"])
            self.assertEqual(command[command.index("--concurrency") + 1], "5")
            self.assertIn("--no-augment", command)
            self.assertNotIn(env["OPENAI_API_KEY"], command)
            self.assertNotIn(env["OPENAI_BASE_URL"], command)

    def test_missing_cli_key_or_base_url_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            codex_home, cli, input_path = self.fixture(base)
            out_dir = base / "outputs"
            cases = [
                ({"CODEX_HOME": str(codex_home), "OPENAI_BASE_URL": "https://example.invalid"}, "OPENAI_API_KEY"),
                ({"CODEX_HOME": str(codex_home), "OPENAI_API_KEY": "secret"}, "OPENAI_BASE_URL"),
            ]
            for env, expected in cases:
                with self.subTest(expected=expected), mock.patch.dict("os.environ", env, clear=True):
                    with self.assertRaisesRegex(ImageGenBatchError, expected):
                        run_imagegen_api_batch(input_path, out_dir)
            cli.unlink()
            with mock.patch.dict("os.environ", {
                "CODEX_HOME": str(codex_home), "OPENAI_API_KEY": "secret", "OPENAI_BASE_URL": "https://example.invalid"
            }, clear=True):
                with self.assertRaisesRegex(ImageGenBatchError, "CLI|image_gen"):
                    run_imagegen_api_batch(input_path, out_dir)

    def test_concurrency_above_ten_is_rejected_before_subprocess(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            codex_home, _cli, input_path = self.fixture(base)
            with mock.patch.dict("os.environ", {
                "CODEX_HOME": str(codex_home), "OPENAI_API_KEY": "secret", "OPENAI_BASE_URL": "https://example.invalid"
            }, clear=True), mock.patch("book_video_factory.imagegen_api_batch.subprocess.run") as runner:
                with self.assertRaisesRegex(ImageGenBatchError, "concurrency"):
                    run_imagegen_api_batch(input_path, base / "outputs", concurrency=11)
                runner.assert_not_called()

    def test_child_output_is_redacted_before_reporting(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            codex_home, _cli, input_path = self.fixture(base)
            key = "top-secret-key"
            url = "https://private.example.invalid/v1"
            completed = subprocess.CompletedProcess([], 2, stdout=f"bad {key}", stderr=f"endpoint {url}")
            with mock.patch.dict("os.environ", {
                "CODEX_HOME": str(codex_home), "OPENAI_API_KEY": key, "OPENAI_BASE_URL": url
            }, clear=True), mock.patch("book_video_factory.imagegen_api_batch.subprocess.run", return_value=completed):
                with self.assertRaises(ImageGenBatchError) as raised:
                    run_imagegen_api_batch(input_path, base / "outputs")
            message = str(raised.exception)
            self.assertNotIn(key, message)
            self.assertNotIn(url, message)
            self.assertIn("[REDACTED]", message)


if __name__ == "__main__":
    unittest.main()

