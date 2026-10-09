"""Pages packaging tests; no converters or network required."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    'build_pages', Path(__file__).resolve().parents[1] / 'scripts/build_pages.py'
)
pages = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pages)


class PagesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.destination = self.root / 'site'
        (self.root / 'index.html').write_text('<!doctype html>')
        (self.root / 'optimized/nested').mkdir(parents=True)
        (self.root / 'optimized/nested/a #.webp').write_bytes(b'media')
        self.manifest = self.root / 'optimized/manifest.json'
        self.manifest.write_text(json.dumps({'version': 1, 'assets': {
            'nested/a.png': {'output': 'nested/a #.webp'}
        }}))

    def test_packages_only_managed_outputs(self):
        (self.root / 'optimized/unmanaged.webp').write_bytes(b'ignored')
        (self.root / 'originals').mkdir()
        (self.root / 'originals/a.png').write_bytes(b'original')
        pages.build(self.root, self.destination)
        self.assertEqual({p.relative_to(self.destination).as_posix()
                          for p in self.destination.rglob('*') if p.is_file()},
                         {'index.html', 'optimized/manifest.json', 'optimized/nested/a #.webp'})

    def test_size_limit_includes_page_and_manifest(self):
        size = sum(p.stat().st_size for p in self.root.rglob('*') if p.is_file())
        with self.assertRaisesRegex(ValueError, 'must be below'):
            pages.build(self.root, self.destination, limit=size)
        self.assertFalse(self.destination.exists())
        pages.build(self.root, self.destination, limit=size + 1)

    def test_missing_media_fails_before_staging(self):
        (self.root / 'optimized/nested/a #.webp').unlink()
        with self.assertRaisesRegex(ValueError, 'Missing published file'):
            pages.build(self.root, self.destination)
        self.assertFalse(self.destination.exists())

    def test_invalid_paths_fail_before_staging(self):
        for name in ['../secret.webp', '/a.webp', 'nested/../a.webp', 'a\\b.webp', 'https://a.webp']:
            with self.subTest(name=name):
                self.manifest.write_text(json.dumps({'version': 1, 'assets': {
                    'a.png': {'output': name}
                }}))
                with self.assertRaisesRegex(ValueError, 'Invalid output path'):
                    pages.build(self.root, self.destination)
                self.assertFalse(self.destination.exists())
