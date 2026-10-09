#!/usr/bin/env python3
"""Synchronize originals/ into optimized/; run from any working directory."""

import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile

from PIL import Image, ImageOps, ImageSequence, __version__ as pillow_version

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "originals"
DESTINATION = ROOT / "optimized"
MANIFEST = DESTINATION / "manifest.json"
FORMATS = {".jpg": ".webp", ".jpeg": ".webp", ".png": ".webp",
           ".gif": ".webp", ".mp4": ".webm"}
SETTINGS = {
    "version": 1,
    "image_quality": 82,
    "image_method": 6,
    "video_crf": 32,
    "video_cpu_used": 2,
    "video_target_ratio": 0.80,
    "video_overhead_ratio": 0.03,
    "video_attempts": 2,
    "audio_bitrate": 96000,
    "audio_mono_bitrate": 64000,
    "pillow_version": pillow_version,
}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def output_name(key):
    """Replace the source extension with the web format extension."""
    part = PurePosixPath(key)
    if (part.is_absolute() or str(part) != key or ".." in part.parts
            or "\\" in key or ":" in key or part.suffix.lower() not in FORMATS):
        raise ValueError(f"Invalid source key: {key!r}")
    return part.with_suffix(FORMATS[part.suffix.lower()]).as_posix()


def destination_path(name):
    path = DESTINATION / name
    if not path.resolve().is_relative_to(DESTINATION.resolve()):
        raise ValueError(f"Output escapes optimized/: {name!r}")
    for candidate in [path, *path.parents]:
        if candidate == DESTINATION.parent:
            break
        if candidate.is_symlink():
            raise ValueError(f"Symlink in output path: {candidate}")
    return path


def encode_image(source, target):
    with Image.open(source) as image:
        options = {"format": "WEBP", "quality": SETTINGS["image_quality"],
                   "method": SETTINGS["image_method"]}
        if getattr(image, "is_animated", False):
            frames, durations = [], []
            # Pillow returns composited frames, honoring disposal and blending.
            for index, frame in enumerate(ImageSequence.Iterator(image)):
                if index == 0 and image.info.get("default_image"):
                    continue  # APNG's optional poster is not an animation frame.
                frames.append(frame.convert("RGBA"))
                durations.append(frame.info.get("duration", 100))
            loop = image.info.get("loop", 1)
            if image.format == "GIF":
                # GIF counts repeats after the first play; WebP counts plays.
                loop = image.info.get("loop")
                loop = 1 if loop is None else (0 if loop == 0 else loop + 1)
            frames[0].save(target, save_all=True, append_images=frames[1:],
                           duration=durations, loop=loop, **options)
        else:
            ImageOps.exif_transpose(image).convert("RGBA").save(target, **options)


def encode_video(source, target):
    probe = json.loads(subprocess.run([
        "ffprobe", "-v", "error", "-show_format", "-show_streams",
        "-of", "json", str(source),
    ], capture_output=True, text=True, check=True).stdout)
    duration = float(probe["format"]["duration"])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError(f"Invalid video duration: {source}")
    audio = next((stream for stream in probe["streams"]
                  if stream["codec_type"] == "audio"), None)
    audio_bitrate = 0
    if audio:
        audio_bitrate = (SETTINGS["audio_mono_bitrate"] if audio.get("channels") == 1
                         else SETTINGS["audio_bitrate"])
        if audio.get("bit_rate") not in (None, "N/A") and int(audio["bit_rate"]) > 0:
            audio_bitrate = min(audio_bitrate, int(audio["bit_rate"]))
    target_size = int(source.stat().st_size * SETTINGS["video_target_ratio"])
    total_bitrate = target_size * 8 / duration * (1 - SETTINGS["video_overhead_ratio"])
    video_bitrate = int(total_bitrate - audio_bitrate)
    with tempfile.TemporaryDirectory(prefix="vp9-pass-") as temporary:
        for _ in range(SETTINGS["video_attempts"]):
            if video_bitrate <= 0:
                break
            command = [
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(source), "-map", "0:v:0",
                "-c:v", "libvpx-vp9", "-crf", str(SETTINGS["video_crf"]),
                "-b:v", str(video_bitrate), "-cpu-used", str(SETTINGS["video_cpu_used"]),
                "-deadline", "good", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
                "-pix_fmt", "yuv420p", "-passlogfile", str(Path(temporary) / "pass"),
            ]
            subprocess.run(command + ["-pass", "1", "-an", "-f", "null", os.devnull], check=True)
            subprocess.run(command + [
                "-pass", "2", "-map", "0:a:0?", "-c:a", "libopus",
                "-b:a", str(audio_bitrate or SETTINGS["audio_bitrate"]), str(target),
            ], check=True)
            size = target.stat().st_size
            if size == 0:
                raise ValueError(f"Conversion produced no data: {source}")
            if size <= target_size:
                return target
            # Leave extra headroom on the retry while retaining the CRF target.
            video_bitrate = int(video_bitrate * target_size / size * 0.90)
    target.unlink(missing_ok=True)
    fallback = target.with_suffix(source.suffix)
    shutil.copyfile(source, fallback)
    return fallback


def atomic_write(target, data=None, source=None):
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
        pending = Path(stream.name)
        try:
            if source is None:
                stream.write(data)
            else:
                with source.open("rb") as encoded:
                    shutil.copyfileobj(encoded, stream)
        except BaseException:
            stream.close()
            pending.unlink(missing_ok=True)
            raise
    try:
        pending.replace(target)
    finally:
        pending.unlink(missing_ok=True)


def synchronize():
    if not SOURCE.is_dir() or SOURCE.is_symlink():
        raise ValueError("originals/ must exist and must not be a symlink")
    if DESTINATION.is_symlink():
        raise ValueError("optimized/ must not be a symlink")
    DESTINATION.mkdir(exist_ok=True)
    destination_path("manifest.json")
    previous = {"version": 1, "assets": {}}
    if MANIFEST.exists():
        previous = json.loads(MANIFEST.read_text(encoding="utf-8"))
        if previous.get("version") != 1 or not isinstance(previous.get("assets"), dict):
            raise ValueError("Unsupported or invalid optimized/manifest.json")
    old = previous["assets"]
    # Validate all managed paths before converting or pruning anything.
    for key, entry in old.items():
        expected = output_name(key)
        legacy = key + FORMATS[PurePosixPath(key).suffix.lower()]
        allowed = {expected, legacy}
        if PurePosixPath(key).suffix.lower() == ".mp4":
            allowed.add(key)
        if entry["output"] not in allowed:
            raise ValueError(f"Unexpected output for {key!r}")
        destination_path(entry["output"])

    sources = {}
    for path in sorted(SOURCE.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symlink in originals/: {path}")
        if path.suffix.lower() not in FORMATS:
            continue
        if not path.resolve().is_relative_to(SOURCE.resolve()):
            raise ValueError(f"Source escapes originals/: {path}")
        if path.is_file():
            key = path.relative_to(SOURCE).as_posix()
            output_name(key)
            sources[key] = path

    owners = {}
    for key in sources:
        name = output_name(key)
        # Case-fold to keep the output directory portable across filesystems.
        if name.casefold() in owners:
            raise ValueError(
                f"Output collision: {owners[name.casefold()]!r} and {key!r} both map to {name!r}"
            )
        owners[name.casefold()] = key

    video_settings = dict(SETTINGS)
    if any(path.suffix.lower() == ".mp4" for path in sources.values()):
        video_settings["ffmpeg_version"] = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, text=True, check=True
        ).stdout.splitlines()[0]

    assets, changed = {}, []
    with tempfile.TemporaryDirectory(prefix="optimize-media-") as temporary:
        stage = Path(temporary)
        for key, source in sources.items():
            name = output_name(key)
            target = destination_path(name)
            settings = video_settings if source.suffix.lower() == ".mp4" else SETTINGS
            entry = {"output": name, "source_sha256": digest(source), "settings": settings}
            before = old.get(key, {})
            unchanged = all(before.get(field) == entry[field]
                            for field in ("source_sha256", "settings"))
            reusable = destination_path(before["output"]) if before else target
            valid = (unchanged and reusable.is_file()
                      and before.get("output_sha256") == digest(reusable))
            if valid and source.suffix.lower() == ".mp4" and before["output"] == key:
                assets[key] = before
                continue
            if valid and reusable == target:
                assets[key] = before
                continue
            staged = stage / name
            staged.parent.mkdir(parents=True, exist_ok=True)
            if valid:
                # Naming-only migrations reuse verified bytes without encoding.
                shutil.copyfile(reusable, staged)
            elif source.suffix.lower() == ".mp4":
                staged = encode_video(source, staged)
                name = staged.relative_to(stage).as_posix()
                target = destination_path(name)
                entry["output"] = name
            else:
                encode_image(source, staged)
            if not staged.is_file() or staged.stat().st_size == 0:
                raise ValueError(f"Conversion produced no data: {key}")
            entry["output_sha256"] = digest(staged)
            assets[key] = entry
            changed.append((staged, target))
            action = "Migrated" if valid else "Converted"
            print(f"{action} {key} -> {name}")

        # Every conversion succeeded before any existing assets are changed.
        for staged, target in changed:
            atomic_write(target, source=staged)
        live_outputs = {entry["output"] for entry in assets.values()}
        live_paths = {destination_path(name) for name in live_outputs}
        obsolete = {entry["output"] for entry in old.values()} - live_outputs
        for name in sorted(obsolete):
            target = destination_path(name)
            if target in live_paths:
                continue  # Case-only rename on a case-insensitive filesystem.
            target.unlink(missing_ok=True)
            print(f"Removed {name}")
            parent = target.parent
            while parent != DESTINATION:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent

        content = json.dumps({"version": 1, "assets": assets}, indent=2, sort_keys=True) + "\n"
        if not MANIFEST.exists() or MANIFEST.read_text(encoding="utf-8") != content:
            atomic_write(MANIFEST, content.encode("utf-8"))
    print(f"Synchronized {len(assets)} assets; updated {len(changed)}.")


if __name__ == "__main__":
    synchronize()
