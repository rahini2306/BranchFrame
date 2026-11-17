import subprocess
import tempfile
import unittest
from pathlib import Path

from branchframe.domain import digest, validate_timeline
from branchframe.editing import schedule
from branchframe.store import Store
from branchframe.worker import execute, find_ffmpeg, probe_media
from tests.test_domain import clip


@unittest.skipUnless(find_ffmpeg(), "FFmpeg is required")
class CompositionTests(unittest.TestCase):
    def test_crossfade_overlay_and_music_export(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            store = Store(root)
            video = root / "source.mp4"
            music = root / "music.wav"
            subprocess.run(
                [
                    find_ffmpeg(),
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=red:s=320x240:r=30:d=2",
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
                    str(video),
                ],
                check=True,
            )
            subprocess.run(
                [
                    find_ffmpeg(),
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=220:duration=3",
                    str(music),
                ],
                check=True,
            )
            project = "b" * 32
            aid = "a" * 32
            mid = "c" * 32
            store.put("assets/" + project + "/" + aid, video)
            store.put("assets/" + project + "/" + mid, music)
            timeline = {
                "clips": [
                    clip("first", asset=aid, end=1),
                    clip(
                        "second",
                        asset=aid,
                        end=1,
                        transition="fade",
                        transition_duration=0.25,
                    ),
                    clip(
                        "overlay",
                        asset=aid,
                        end=0.5,
                        track=1,
                        at=0.25,
                        x=0.5,
                        y=0.1,
                        width=0.3,
                        height=0.3,
                        muted=True,
                    ),
                ],
                "audio": [
                    {
                        "id": "music",
                        "asset": mid,
                        "start": 0,
                        "end": 2,
                        "at": 0,
                        "volume": 0.2,
                        "fade_in": 0.2,
                        "fade_out": 0.2,
                    }
                ],
            }
            validate_timeline(timeline)
            self.assertAlmostEqual(schedule(timeline)["duration"], 1.75)
            keys = []
            for c in timeline["clips"]:
                keys.append(
                    execute(
                        store,
                        {
                            "kind": "segment",
                            "render": "d" * 32,
                            "payload": {
                                "clip": c,
                                "project": project,
                                "cache_key": digest(c),
                            },
                        },
                    )
                )
            result = execute(
                store,
                {
                    "kind": "assemble",
                    "render": "d" * 32,
                    "payload": {"keys": keys, "timeline": timeline, "project": project},
                },
            )
            self.assertGreater(store.get(result).stat().st_size, 1000)
            self.assertAlmostEqual(
                float(probe_media(store.get(result))["format"]["duration"]),
                1.75,
                delta=0.15,
            )
            # A concat before xfade has a different timebase; exercise the chain,
            # not just a single two-input fade.
            middle = clip("middle", asset=aid, end=0.5)
            timeline["clips"].insert(1, middle)
            keys.insert(
                1,
                execute(
                    store,
                    {
                        "kind": "segment",
                        "render": "e" * 32,
                        "payload": {
                            "clip": middle,
                            "project": project,
                            "cache_key": digest(middle),
                        },
                    },
                ),
            )
            result = execute(
                store,
                {
                    "kind": "assemble",
                    "render": "e" * 32,
                    "payload": {"keys": keys, "timeline": timeline, "project": project},
                },
            )
            self.assertAlmostEqual(
                float(probe_media(store.get(result))["format"]["duration"]),
                2.25,
                delta=0.15,
            )

    def test_invalid_track_and_outside_overlay_are_rejected(self):
        for values in [
            {"track": 4},
            {"track": 1, "x": 0.9, "width": 0.4},
            {"transition": "invented"},
        ]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_timeline({"clips": [clip(**values)]})
