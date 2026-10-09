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

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is not installed")
    def test_mp4_with_and_without_audio(self):
        for name, audio in [("silent.mp4", False), ("sound.mp4", True)]:
            command = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi",
                       "-i", "color=c=red:s=16x16:r=5:d=0.4"]
            if audio:
                command += ["-f", "lavfi", "-i", "sine=frequency=440:duration=0.4",
                            "-c:a", "aac"]
            command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(self.source / name)]
            subprocess.run(command, check=True)
        media.synchronize()
        for name, audio in [("silent.mp4", False), ("sound.mp4", True)]:
            output = self.output / Path(name).with_suffix(".webm")
            result = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(output)],
                                    capture_output=True, text=True)
            self.assertIn("Video: vp9", result.stderr)
            self.assertEqual("Audio: opus" in result.stderr, audio)
            subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i",
                            str(output), "-f", "null", "-"], check=True)


if __name__ == "__main__":
    unittest.main()
