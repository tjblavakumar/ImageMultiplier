import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

import generate_variance as app


def image_bytes(size=(800, 600)):
    output = io.BytesIO()
    Image.new("RGB", size, "red").save(output, "PNG")
    return output.getvalue()


class GenerateVarianceTests(unittest.TestCase):
    def test_size_conversion_and_invalid_sizes(self):
        self.assertEqual(app.parse_size("2x2"), (600, 600))
        self.assertEqual(app.parse_size("1.5x2"), (450, 600))
        for value in ("0x2", "-1x2", "nanx2", "2", "2x2x2"):
            with self.subTest(value=value), self.assertRaises(app.argparse.ArgumentTypeError):
                app.parse_size(value)

    def test_saves_crop_dpi_and_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = app.save_result(
                image_bytes(), Path(directory), Path("sample.jpg"), (600, 600), "test prompt", 42,
                app.background_color("white"),
            )
            with Image.open(path) as output:
                self.assertEqual(output.size, (600, 600))
                self.assertAlmostEqual(output.info["dpi"][0], 300, delta=0.1)
            metadata = json.loads(path.with_suffix(".json").read_text())
            self.assertEqual(metadata["seed"], 42)
            self.assertEqual(metadata["prompt"], "test prompt")

    def test_transparency_is_filled_with_selected_background(self):
        data = io.BytesIO()
        image = Image.new("RGBA", (2, 1), (255, 0, 0, 255))
        image.putpixel((1, 0), (0, 0, 0, 0))
        image.save(data, format="PNG")
        with tempfile.TemporaryDirectory() as directory:
            path = app.save_result(
                data.getvalue(), Path(directory), Path("sample.png"), (2, 1), "prompt", 42,
                app.background_color("#0080ff"),
            )
            with Image.open(path) as output:
                self.assertEqual(output.getpixel((0, 0)), (255, 0, 0))
                self.assertEqual(output.getpixel((1, 0)), (0, 128, 255))
            self.assertEqual(json.loads(path.with_suffix(".json").read_text())["background_color"], "#0080ff")

    @patch.object(app.requests, "post")
    def test_invalid_background_is_rejected_before_upload(self, post):
        with self.assertRaises(SystemExit):
            app.main(["sample.jpg", "2x2", "not-a-color", "1"])
        post.assert_not_called()

    @patch.object(app.requests, "get")
    @patch.object(app.requests, "post")
    def test_explicit_background_is_in_prompt(self, post, get):
        post.side_effect = [
            Mock(ok=True, json=lambda: {"asset": {"key": "images/source.png"}}),
            Mock(ok=True, json=lambda: {"aiRecord": {"status": "SUCCESS", "temporaryUrl": "https://example.com/1"}}),
        ]
        get.return_value = Mock(ok=True, content=image_bytes())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            source.write_bytes(image_bytes())
            old_cwd = os.getcwd()
            try:
                os.chdir(root)
                with patch.dict(os.environ, {"ONEMIN_API_KEY": "test-key"}):
                    self.assertEqual(app.main([str(source), "2x2", "blue", "1"]), 0)
            finally:
                os.chdir(old_cwd)
        prompt = post.call_args_list[1].kwargs["json"]["promptObject"]["prompt"]
        self.assertIn("solid blue background", prompt)
        self.assertIn("rich, vibrant, bright colors with strong contrast", prompt)

    @patch.object(app.requests, "get")
    @patch.object(app.requests, "post")
    def test_generates_each_image_from_uploaded_reference(self, post, get):
        post.side_effect = [
            Mock(ok=True, json=lambda: {"asset": {"key": "images/source.png"}}),
            Mock(ok=True, json=lambda: {"aiRecord": {"status": "SUCCESS", "temporaryUrl": "https://example.com/1"}}),
            Mock(ok=True, json=lambda: {"aiRecord": {"status": "SUCCESS", "temporaryUrl": "https://example.com/2"}}),
        ]
        get.return_value = Mock(ok=True, content=image_bytes())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            source.write_bytes(image_bytes())
            old_cwd = os.getcwd()
            try:
                os.chdir(root)
                with patch.dict(os.environ, {"ONEMIN_API_KEY": "test-key"}):
                    self.assertEqual(app.main([str(source), "2x2", "2"]), 0)
                outputs = list((root / "output").glob("*/*.png"))
                self.assertEqual(len(outputs), 2)
                self.assertEqual(len(list((root / "output").glob("*/*.json"))), 2)
                self.assertEqual(source.read_bytes(), image_bytes())
            finally:
                os.chdir(old_cwd)
        self.assertEqual(post.call_count, 3)
        for call in post.call_args_list[1:]:
            self.assertEqual(call.kwargs["json"]["promptObject"]["imageUrl"], "images/source.png")
            self.assertIn("solid white background", call.kwargs["json"]["promptObject"]["prompt"])
            self.assertIn("rich, vibrant, bright colors with strong contrast", call.kwargs["json"]["promptObject"]["prompt"])
        self.assertNotEqual(
            post.call_args_list[1].kwargs["json"]["promptObject"]["prompt"],
            post.call_args_list[2].kwargs["json"]["promptObject"]["prompt"],
        )
        self.assertNotIn("headers", get.call_args.kwargs)

    @patch.object(app.requests, "post")
    def test_failed_generation_does_not_download(self, post):
        post.return_value = Mock(ok=True, json=lambda: {"aiRecord": {"status": "FAILED"}})
        with self.assertRaisesRegex(app.GenerationError, "FAILED"):
            app.generate_image("source", "prompt", 1, "test-key")

    @patch.object(app.requests, "get")
    @patch.object(app.requests, "post")
    def test_keeps_first_result_if_later_generation_fails(self, post, get):
        post.side_effect = [
            Mock(ok=True, json=lambda: {"asset": {"key": "images/source.png"}}),
            Mock(ok=True, json=lambda: {"aiRecord": {"status": "SUCCESS", "temporaryUrl": "https://example.com/1"}}),
            Mock(ok=True, json=lambda: {"aiRecord": {"status": "FAILED"}}),
        ]
        get.return_value = Mock(ok=True, content=image_bytes())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            source.write_bytes(image_bytes())
            old_cwd = os.getcwd()
            try:
                os.chdir(root)
                with patch.dict(os.environ, {"ONEMIN_API_KEY": "test-key"}):
                    self.assertEqual(app.main([str(source), "2x2", "2"]), 1)
                self.assertEqual(len(list((root / "output").glob("*/*.png"))), 1)
            finally:
                os.chdir(old_cwd)
        self.assertEqual(get.call_count, 1)

    @patch.object(app.requests, "post")
    def test_timeout_warns_request_may_have_been_processed(self, post):
        post.side_effect = app.requests.Timeout()
        with self.assertRaisesRegex(app.GenerationError, "may have been processed"):
            app.generate_image("source", "prompt", 1, "test-key")


if __name__ == "__main__":
    unittest.main()
