import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from app.ui.app_shell import local_drop_paths


class LocalDropPathsTests(unittest.TestCase):
    def _url(self, path, local=True):
        url = Mock()
        url.isLocalFile.return_value = local
        url.toLocalFile.return_value = str(path)
        return url

    def test_returns_existing_local_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / 'sample.mp4'
            media.write_bytes(b'media')
            self.assertEqual(local_drop_paths([self._url(media)]), [str(media)])

    def test_filters_non_local_and_missing_paths_preserving_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / 'first.mp4'
            second = Path(tmp) / 'second.mp4'
            first.write_bytes(b'1')
            second.write_bytes(b'2')
            urls = [
                self._url(first),
                self._url(Path(tmp) / 'missing.mp4'),
                self._url('https://example.invalid/file.mp4', local=False),
                self._url(second),
            ]
            self.assertEqual(local_drop_paths(urls), [str(first), str(second)])

    def test_preserves_duplicate_paths_for_existing_import_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / 'sample.mp4'
            media.write_bytes(b'media')
            self.assertEqual(
                local_drop_paths([self._url(media), self._url(media)]),
                [str(media), str(media)],
            )

    def test_empty_drop_returns_empty(self):
        self.assertEqual(local_drop_paths([]), [])


if __name__ == '__main__':
    unittest.main()
