# images-temp

[**Browse media →**](https://kisenamiyuki.github.io/images-temp/)

Add media to `originals/` and push to `main`. GitHub Actions updates `optimized/`
and deploys the media page with previews, file sizes, and copyable direct links.
You can also run **Optimize media** manually from the Actions tab.

## Formats

| Source | Output |
| --- | --- |
| JPG / JPEG / PNG | WebP (quality 82) |
| GIF / animated PNG | Animated WebP, preserving timing and looping |
| MP4 | VP9 WebM with Opus audio; original MP4 if the 80% size target cannot be met |

Subdirectories are preserved: `originals/photos/a.jpg` → `optimized/photos/a.webp`.
Output-name collisions are rejected. Originals are never modified; unsupported
formats are ignored, and conversion does not always reduce file size.

Keep `optimized/manifest.json` tracked: it records output paths, sizes, hashes,
and settings. Unchanged files are skipped, deleted sources lose their tracked
outputs, and failed conversions leave existing outputs intact. Keep
`originals/.gitkeep` so the source folder survives removal of its last file.

## Deployment

Set **Settings → Pages → Build and deployment → Source** to **GitHub Actions**
and allow the workflow bot to push to `main`. If a bot push is rejected by a
concurrent update, rerun the workflow.

Only the page, manifest, and tracked outputs are published—not originals.
Packaging rejects missing files or a site reaching 1 GB. See
[GitHub Pages limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits);
use object storage/CDN for larger collections or heavy traffic.

## Local use

Requires Python 3.12+ and FFmpeg/FFprobe on `PATH` with libvpx-vp9 and libopus support.

```sh
python -m pip install -r scripts/requirements-media.txt
python scripts/optimize_media.py
python -B -m unittest discover -s tests -v
python -m http.server 8000
```

Open <http://localhost:8000/>. Adjust encoding settings in
`scripts/optimize_media.py`; inspect video quality after changes.
