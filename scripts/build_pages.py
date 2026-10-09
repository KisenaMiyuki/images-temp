#!/usr/bin/env python3
"""Stage only the media index and manifest-managed outputs for GitHub Pages."""

import json
from pathlib import Path, PurePosixPath
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
# Use decimal GB conservatively, rather than assuming the limit means GiB.
MAX_BYTES = 1_000_000_000


def build(root, destination, limit=MAX_BYTES):
    manifest = json.loads((root / 'optimized/manifest.json').read_text(encoding='utf-8'))
    if manifest.get('version') != 1 or not isinstance(manifest.get('assets'), dict):
        raise ValueError('Unsupported media manifest')
    files = {Path('index.html'), Path('optimized/manifest.json')}
    for entry in manifest['assets'].values():
        name = entry['output']
        path = PurePosixPath(name)
        if (path.is_absolute() or str(path) != name or '..' in path.parts
                or '\\' in name or ':' in name or not path.suffix):
            raise ValueError(f'Invalid output path: {name!r}')
        files.add(Path('optimized') / path)
    total = 0
    for relative in files:
        source = root / relative
        if not source.resolve().is_relative_to(root.resolve()) or any(
                part.is_symlink() for part in [source, *source.parents]):
            raise ValueError(f'Symlink or escaped path: {relative}')
        if not source.is_file():
            raise ValueError(f'Missing published file: {relative}; run media synchronization first')
        total += source.stat().st_size
    if total >= limit:
        raise ValueError(f'Pages site is {total:,} bytes; must be below {limit:,} bytes')
    if destination.exists():
        raise ValueError(f'Staging directory already exists: {destination}')
    destination.mkdir(parents=True)
    for relative in sorted(files):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / relative, target)
    print(f'Staged {len(files)} files, {total:,} bytes ({total / limit:.1%} of Pages size budget).')


if __name__ == '__main__':
    build(ROOT, Path(sys.argv[1]))
