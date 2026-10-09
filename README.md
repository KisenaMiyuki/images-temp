# images-temp

```
https://kisenamiyuki.github.io/images-temp/optimized/
```

Add source media to `originals/`. GitHub Actions maintains `optimized/` on pushes
to `main`; you can also run **Optimize media** manually in the Actions tab.

| Original | Optimized |
| --- | --- |
| JPG / JPEG / PNG | WebP (quality 82) |
| GIF / animated PNG | Animated WebP, retaining frame timing and looping |
| MP4 | Two-pass VP9 WebM with Opus audio, or original MP4 if the size target cannot be met |

Subdirectories are retained and extensions are replaced: `originals/photos/a.jpg`
becomes `optimized/photos/a.webp`, and `originals/demo.mp4` becomes
`optimized/demo.webm` when conversion meets the size target, otherwise
`optimized/demo.mp4`. Use the manifest's `output` field to locate the selected file.
Manifest keys remain relative source filenames, including
the original extensions. Sources such as `a.jpg` and `a.png` would both produce
`a.webp`; the script rejects these collisions before changing any outputs.
Existing files using the previous `a.jpg.webp` naming migrate automatically,
reusing verified bytes without reencoding.

`optimized/manifest.json` records source and output SHA-256 hashes and encoding
settings. Unchanged files are skipped; changed sources, missing or modified
outputs, and changed settings cause regeneration. Removing or renaming a source
removes its previous output. Only files recorded in the manifest are pruned;
other files in `optimized/` are left alone. Keep the manifest under version control.

All conversions are staged before replacing outputs or removing stale files.
A conversion failure leaves existing outputs and the manifest unchanged.
Keep `originals/.gitkeep` so deleting the last original retains the source folder.
Unsupported formats are ignored. Originals are never modified. Output size
depends on source content and quality; conversion does not guarantee a smaller file.

Videos target 80% of the original file size. FFprobe supplies duration and audio
metadata; two-pass VP9 distributes the calculated bitrate across scenes, reserving
3% for overhead. Opus targets 64 kbps for mono or 96 kbps otherwise, capped at the
source audio bitrate when known. Silent videos reserve no audio budget. Resolution
and frame rate are preserved (odd dimensions are padded to even dimensions).
CRF 32 supplies a constrained-quality target alongside the bitrate budget, but
does not guarantee perceptual quality.
An oversized result gets one retry with a lower bitrate. If the target still cannot
be met, or audio leaves no video budget, the original MP4 is copied unchanged instead.
Only WebMs at or below the size target are published. Fallbacks are cached and tracked
in the manifest just like converted files. Encoding/probing failures still abort the
run without replacing existing outputs. There is no automated perceptual quality
assessment; inspect representative videos before adjusting the quality/size settings.

For a local run, install Python 3.12 or newer and FFmpeg/FFprobe (with libvpx-vp9 and
libopus support) on your PATH, then run:

```sh
python -m pip install -r scripts/requirements-media.txt
python scripts/optimize_media.py
python -B -m unittest discover -s tests -v
```

Adjust conversion settings in `scripts/optimize_media.py`. The action commits
generated files to `main` using `GITHUB_TOKEN`; repository rules must permit
the bot to push. [Bot pushes do not trigger other push workflows](https://docs.github.com/en/actions/concepts/security/github_token),
so deployment that needs these assets should run after synchronization in the
same workflow or be triggered explicitly. Concurrent pushes are never force-pushed
over: if a push is rejected, rerun the workflow against the latest `main`.
