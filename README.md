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
| MP4 | WebM (VP9 video, Opus audio when present) |

Subdirectories are retained and extensions are replaced: `originals/photos/a.jpg`
becomes `optimized/photos/a.webp`, and `originals/demo.mp4` becomes
`optimized/demo.webm`. Manifest keys remain relative source filenames, including
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

For a local run, install Python 3.12 or newer and FFmpeg (with libvpx-vp9 and
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
