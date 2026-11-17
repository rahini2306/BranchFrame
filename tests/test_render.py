"""Actual decode/encode/assemble smoke test. CI installs FFmpeg explicitly."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import tempfile
import unittest
from branchframe.worker import find_ffmpeg, execute, probe_media
from branchframe.domain import digest
from branchframe.store import Store


@unittest.skipUnless(find_ffmpeg(), "FFmpeg is not installed")
class RenderTests(unittest.TestCase):
    def test_caption_mute_parallel_segments_and_assembly(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            source = Path(directory) / "fixture.mp4"
            subprocess.run(
                [
                    find_ffmpeg(),
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=blue:s=320x240:r=30:d=2",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=2",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-shortest",
                    str(source),
                ],
                check=True,
            )
            aid = "a" * 32
            project = "b" * 32
            store.put("assets/" + project + "/" + aid, source)
            jobs = []
            for muted, caption in [(False, ""), (True, "A caption: 100% original")]:
                clip = {
                    "id": "one",
                    "asset": aid,
                    "start": 0,
                    "end": 1,
                    "caption": caption,
                    "muted": muted,
                }
                key = digest(clip)
                job = {
                    "kind": "segment",
                    "render": "c" * 32,
                    "payload": {"project": project, "clip": clip, "cache_key": key},
                }
                jobs.append(job)
            with ThreadPoolExecutor(max_workers=2) as pool:
                keys = list(pool.map(lambda job: execute(store, job), jobs))
            for object_key in keys:
                self.assertGreater(store.get(object_key).stat().st_size, 1000)
            final = execute(
                store,
                {"kind": "assemble", "render": "c" * 32, "payload": {"keys": keys}},
            )
            probe = probe_media(store.get(final))
            self.assertAlmostEqual(float(probe["format"]["duration"]), 2, delta=0.2)
            self.assertTrue(any(s["codec_type"] == "audio" for s in probe["streams"]))

    def test_corrupt_media_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "bad.mp4"
            source.write_bytes(b"not a video")
            with self.assertRaises((ValueError, RuntimeError)):
                probe_media(source)
