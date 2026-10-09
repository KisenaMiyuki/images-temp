"""End-to-end synchronization tests using isolated temporary repositories."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

SPEC = importlib.util.spec_from_file_location(
    "optimize_media", Path(__file__).resolve().parents[1] / "scripts/optimize_media.py"
)
media = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(media)


class SynchronizationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "originals"
        self.output = self.root / "optimized"
        self.source.mkdir()
        self.manifest = self.output / "manifest.json"
        for field, value in [("SOURCE", self.source), ("DESTINATION", self.output),
                             ("MANIFEST", self.manifest)]:
            handle = patch.object(media, field, value)
            handle.start()
            self.addCleanup(handle.stop)

    def image(self, key="photo.png", color="red"):
        path = self.source / key
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (16, 12), color).save(path)
        return path

    def snapshot(self):
        return {p.relative_to(self.output).as_posix(): p.read_bytes()
                for p in self.output.rglob("*") if p.is_file()}

    def test_nested_names_replace_extensions(self):
        for key in ["a.jpg", "b.png", "nested/a.JPG", "space & quote's/猫.png"]:
            self.image(key)
        (self.source / "ignored.txt").write_text("ignored")
        media.synchronize()
        assets = json.loads(self.manifest.read_text())["assets"]
        self.assertEqual(len(assets), 4)
        for key, entry in assets.items():
            self.assertEqual(entry["output"], Path(key).with_suffix(".webp").as_posix())
            with Image.open(self.output / entry["output"]) as image:
                self.assertEqual(image.size, (16, 12))

    def test_colliding_sources_fail_without_changing_outputs(self):
        self.image("a.jpg")
        media.synchronize()
        before = self.snapshot()
        self.image("a.png")
        with self.assertRaisesRegex(ValueError, "Output collision"):
            media.synchronize()
        self.assertEqual(self.snapshot(), before)

    def test_legacy_outputs_migrate_without_reencoding(self):
        self.image()
        media.synchronize()
        expected = (self.output / "photo.webp").read_bytes()
        (self.output / "photo.webp").rename(self.output / "photo.png.webp")
        manifest = json.loads(self.manifest.read_text())
        manifest["assets"]["photo.png"]["output"] = "photo.png.webp"
        self.manifest.write_text(json.dumps(manifest))
        with patch.object(media, "encode_image", side_effect=AssertionError("reencoded")):
            media.synchronize()
        self.assertEqual((self.output / "photo.webp").read_bytes(), expected)
        self.assertFalse((self.output / "photo.png.webp").exists())
        self.assertEqual(json.loads(self.manifest.read_text())["assets"]["photo.png"]["output"],
                         "photo.webp")
        with patch.object(media, "encode_image", side_effect=AssertionError("reencoded")):
            media.synchronize()

    def test_source_extension_rename_keeps_shared_output(self):
        source = self.image("photo.jpg")
        media.synchronize()
        source.rename(self.source / "photo.jpeg")
        media.synchronize()
        self.assertTrue((self.output / "photo.webp").exists())
        self.assertEqual(set(json.loads(self.manifest.read_text())["assets"]), {"photo.jpeg"})

    def test_case_only_source_rename_keeps_output(self):
        source = self.image("photo.jpg")
        media.synchronize()
        source.rename(self.source / "PHOTO.jpg")
        media.synchronize()
        self.assertTrue((self.output / "PHOTO.webp").exists())
        self.assertEqual(set(json.loads(self.manifest.read_text())["assets"]), {"PHOTO.jpg"})

    def test_second_run_changes_nothing(self):
        self.image()
        media.synchronize()
        before = self.snapshot()
        stamp = self.manifest.stat().st_mtime_ns
        with patch.object(media, "encode_image", side_effect=AssertionError("reencoded")):
            media.synchronize()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.manifest.stat().st_mtime_ns, stamp)

    def test_same_filename_replacement_and_settings_change(self):
        self.image()
        media.synchronize()
        before = self.snapshot()
        self.image(color="blue")
        media.synchronize()
        self.assertNotEqual(self.snapshot()["photo.webp"], before["photo.webp"])
        with patch.dict(media.SETTINGS, {"image_quality": 50}):
            with patch.object(media, "encode_image", wraps=media.encode_image) as encoder:
                media.synchronize()
                self.assertEqual(encoder.call_count, 1)

    def test_missing_and_corrupted_outputs_are_repaired(self):
        self.image()
        media.synchronize()
        target = self.output / "photo.webp"
        expected = target.read_bytes()
        target.unlink()
        media.synchronize()
        self.assertEqual(target.read_bytes(), expected)
        target.write_bytes(b"corrupted")
        media.synchronize()
        self.assertEqual(target.read_bytes(), expected)

    def test_rename_and_delete_prune_only_managed_outputs(self):
        source = self.image("nested/old.png")
        media.synchronize()
        unrelated = self.output / "keep.txt"
        unrelated.write_text("keep")
        source.rename(self.source / "new.png")
        media.synchronize()
        self.assertFalse((self.output / "nested").exists())
        self.assertTrue((self.output / "new.webp").exists())
        (self.source / "new.png").unlink()
        (self.source / ".gitkeep").touch()
        media.synchronize()
        self.assertEqual(json.loads(self.manifest.read_text())["assets"], {})
        self.assertEqual(unrelated.read_text(), "keep")
        self.assertFalse((self.output / "new.webp").exists())

    def test_conversion_failure_preserves_previous_outputs_and_manifest(self):
        source = self.image("old.png")
        media.synchronize()
        before = self.snapshot()
        source.unlink()
        self.image("a-valid.png")
        (self.source / "z-broken.png").write_bytes(b"not an image")
        with self.assertRaises(OSError):
            media.synchronize()
        self.assertEqual(self.snapshot(), before)

    def test_missing_source_directory_does_not_prune(self):
        self.image()
        media.synchronize()
        before = self.snapshot()
        shutil.rmtree(self.source)
        with self.assertRaises(ValueError):
            media.synchronize()
        self.assertEqual(self.snapshot(), before)

    def test_manifest_cannot_delete_outside_output_directory(self):
        self.output.mkdir()
        victim = self.root / "victim.png.webp"
        victim.write_bytes(b"keep")
        self.manifest.write_text(json.dumps({"version": 1, "assets": {
            "../victim.png": {"output": "../victim.png.webp"}
        }}))
        with self.assertRaises(ValueError):
            media.synchronize()
        self.assertEqual(victim.read_bytes(), b"keep")

    def test_transparency_and_exif_orientation(self):
        Image.new("RGBA", (16, 12), (255, 0, 0, 0)).save(self.source / "alpha.png")
        image = Image.new("RGB", (16, 12), "green")
        exif = Image.Exif()
        exif[274] = 6
        image.save(self.source / "rotated.jpg", exif=exif)
        media.synchronize()
        with Image.open(self.output / "alpha.webp") as image:
            self.assertEqual(image.convert("RGBA").getpixel((0, 0))[3], 0)
        with Image.open(self.output / "rotated.webp") as image:
            self.assertEqual(image.size, (12, 16))

    def test_animated_gif_preserves_timing_and_loop(self):
        frames = [Image.new("RGBA", (16, 12), color) for color in ["red", "blue"]]
        for key, options, expected_loop in [("forever.gif", {"loop": 0}, 0),
                                            ("repeat.gif", {"loop": 2}, 3),
                                            ("once.gif", {}, 1)]:
            frames[0].save(self.source / key, save_all=True, append_images=frames[1:],
                           duration=[80, 160], **options)
        media.synchronize()
        for key, expected_loop in [("forever.gif", 0), ("repeat.gif", 3), ("once.gif", 1)]:
            with Image.open(self.output / (Path(key).with_suffix(".webp").as_posix())) as image:
                self.assertEqual(image.n_frames, 2)
                self.assertEqual(image.info["loop"], expected_loop)
                durations = []
                for index in range(image.n_frames):
                    image.seek(index)
                    image.load()
                    durations.append(image.info["duration"])
                self.assertEqual(durations, [80, 160])

    def test_animated_png_excludes_poster_and_preserves_loop(self):
        poster = Image.new("RGBA", (16, 12), "green")
        frames = [Image.new("RGBA", (16, 12), color) for color in ["red", "blue"]]
        poster.save(self.source / "animation.png", save_all=True, append_images=frames,
                    default_image=True, duration=[80, 160], loop=2)
        media.synchronize()
        with Image.open(self.output / "animation.webp") as image:
            self.assertEqual(image.n_frames, 2)
            self.assertEqual(image.info["loop"], 2)
            image.load()
            self.assertEqual(image.info["duration"], 80)
            self.assertGreater(image.convert("RGB").getpixel((0, 0))[0], 200)

    def test_video_budget_two_pass_audio_and_retry(self):
        source = self.source / "clip.mp4"
        source.write_bytes(b"x" * 200000)
        target = self.root / "clip.webm"
        for audio in [None, {"codec_type": "audio", "channels": 1, "bit_rate": "32000"},
                      {"codec_type": "audio", "channels": 2}]:
            with self.subTest(audio=audio):
                calls = []
                sizes = iter([180000, 150000])

                def run(command, **kwargs):
                    calls.append(command)
                    if command[0] == "ffprobe":
                        return subprocess.CompletedProcess(command, 0, stdout=json.dumps({
                            "format": {"duration": "10"},
                            "streams": [audio] if audio else [],
                        }))
                    if command[command.index("-pass") + 1] == "2":
                        target.write_bytes(b"v" * next(sizes))
                    return subprocess.CompletedProcess(command, 0)

                with patch.object(media.subprocess, "run", side_effect=run):
                    self.assertEqual(media.encode_video(source, target), target)
                self.assertEqual(len(calls), 5)
                audio_rate = 0 if audio is None else (32000 if audio["channels"] == 1 else 96000)
                budget = int(160000 * 8 / 10 * 0.97 - audio_rate)
                self.assertEqual(calls[1][calls[1].index("-b:v") + 1], str(budget))
                self.assertIn("-an", calls[1])
                self.assertEqual(calls[2][calls[2].index("-b:a") + 1], str(audio_rate or 96000))
                self.assertLess(int(calls[3][calls[3].index("-b:v") + 1]), budget)
                self.assertEqual(target.stat().st_size, 150000)
                self.assertFalse(Path(calls[1][calls[1].index("-passlogfile") + 1]).parent.exists())

    def test_oversized_video_falls_back_after_bounded_retries(self):
        source = self.source / "clip.mp4"
        source.write_bytes(b"original" * 1000)
        target = self.root / "clip.webm"

        def run(command, **kwargs):
            if command[0] == "ffprobe":
                return subprocess.CompletedProcess(command, 0, stdout=json.dumps({
                    "format": {"duration": "1"}, "streams": [],
                }))
            if command[command.index("-pass") + 1] == "2":
                target.write_bytes(b"v" * 10000)
            return subprocess.CompletedProcess(command, 0)

        with patch.object(media.subprocess, "run", side_effect=run) as runner:
            fallback = media.encode_video(source, target)
        self.assertEqual(runner.call_count, 5)
        self.assertFalse(target.exists())
        self.assertEqual(fallback.suffix, ".mp4")
        self.assertEqual(fallback.read_bytes(), source.read_bytes())

    def test_video_fallback_is_cached_and_pruned_when_webm_succeeds(self):
        source = self.source / "clip.mp4"
        source.write_bytes(b"original")

        def fallback(source, target):
            target = target.with_suffix(".mp4")
            shutil.copyfile(source, target)
            return target

        version = subprocess.CompletedProcess([], 0, stdout="ffmpeg test\n")
        with patch.object(media.subprocess, "run", return_value=version):
            with patch.object(media, "encode_video", side_effect=fallback):
                media.synchronize()
            before = self.snapshot()
            self.assertEqual(json.loads(self.manifest.read_text())["assets"]["clip.mp4"]["output"],
                             "clip.mp4")
            with patch.object(media, "encode_video", side_effect=AssertionError("reencoded")):
                media.synchronize()
            self.assertEqual(self.snapshot(), before)

            def webm(source, target):
                target.write_bytes(b"vp9")
                return target

            source.write_bytes(b"changed original")
            with patch.object(media, "encode_video", side_effect=webm):
                media.synchronize()
            self.assertFalse((self.output / "clip.mp4").exists())
            self.assertTrue((self.output / "clip.webm").exists())

    def test_video_with_no_available_video_budget_skips_encoding(self):
        source = self.source / "clip.mp4"
        source.write_bytes(b"small")
        target = self.root / "clip.webm"
        probe = subprocess.CompletedProcess([], 0, stdout=json.dumps({
            "format": {"duration": "10"},
            "streams": [{"codec_type": "audio", "channels": 2}],
        }))
        with patch.object(media.subprocess, "run", return_value=probe) as runner:
            fallback = media.encode_video(source, target)
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(fallback.read_bytes(), source.read_bytes())

    def test_invalid_video_duration_fails_before_encoding(self):
        source = self.source / "clip.mp4"
        source.write_bytes(b"source")
        for duration in ["0", "-1", "nan", "inf"]:
            with self.subTest(duration=duration):
                probe = subprocess.CompletedProcess([], 0, stdout=json.dumps({
                    "format": {"duration": duration}, "streams": [],
                }))
                with patch.object(media.subprocess, "run", return_value=probe) as runner:
                    with self.assertRaisesRegex(ValueError, "Invalid video duration"):
                        media.encode_video(source, self.root / "clip.webm")
                self.assertEqual(runner.call_count, 1)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is not installed")
    def test_mp4_with_and_without_audio(self):
        for name, audio in [("silent.mp4", False), ("sound.mp4", True)]:
            command = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi",
                       "-i", "testsrc2=s=128x72:r=10:d=2"]
            if audio:
                command += ["-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                            "-c:a", "aac"]
            command += ["-c:v", "libx264", "-crf", "10", "-pix_fmt", "yuv420p",
                        str(self.source / name)]
            subprocess.run(command, check=True)
        media.synchronize()
        for name, audio in [("silent.mp4", False), ("sound.mp4", True)]:
            entry = json.loads(self.manifest.read_text())["assets"][name]
            output = self.output / entry["output"]
            self.assertEqual(output.suffix, ".webm")
            self.assertLessEqual(output.stat().st_size,
                                 int((self.source / name).stat().st_size * 0.80))
            result = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(output)],
                                    capture_output=True, text=True)
            self.assertIn("Video: vp9", result.stderr)
            self.assertEqual("Audio: opus" in result.stderr, audio)
            subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i",
                            str(output), "-f", "null", "-"], check=True)


if __name__ == "__main__":
    unittest.main()
