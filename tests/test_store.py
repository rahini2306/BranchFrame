from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
import tempfile
from branchframe.store import Store


class AtomicStoreTests(TestCase):
    def test_interrupted_copy_preserves_existing_object(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            source = Path(root) / "source"
            source.write_bytes(b"new data")
            target = store.path("segments/example.mp4")
            target.parent.mkdir()
            target.write_bytes(b"original")

            def interrupted(src, dest):
                Path(dest).write_bytes(b"partial")
                raise OSError("Disk interruption")

            with patch("branchframe.store.shutil.copyfile", interrupted):
                with self.assertRaises(OSError):
                    store.put("segments/example.mp4", source)
            self.assertEqual(target.read_bytes(), b"original")
            self.assertEqual(len(list(target.parent.iterdir())), 1)

    def test_interrupted_download_is_not_cached_as_complete(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)

            class InterruptedS3:
                def download_file(self, bucket, key, destination):
                    Path(destination).write_bytes(b"partial")
                    raise OSError("Network interruption")

            store.s3 = InterruptedS3()
            store.bucket = "test"
            with self.assertRaises(OSError):
                store.get("assets/missing")
            self.assertFalse(store.path("assets/missing").exists())
            self.assertEqual(list(store.path("assets").iterdir()), [])
