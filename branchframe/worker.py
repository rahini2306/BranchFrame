"""Leased render workers: run locally or on independent ECS tasks with S3."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request

from .store import Store


def find_ffmpeg():
    executable = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if executable:
        return executable
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return None


def probe_media(source):
    ffprobe = os.environ.get("FFPROBE") or shutil.which("ffprobe")
    if ffprobe:
        return json.loads(
            run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-format_whitelist",
                    "mov,matroska,webm,mp3,wav,ogg,aac,flac",
                    "-show_streams",
                    "-show_format",
                    "-of",
                    "json",
                    str(source),
                ]
            )
        )
    # The optional imageio wheel bundles FFmpeg alone. Inspect its metadata
    # output without decoding the movie when ffprobe is not installed.
    import re

    result = subprocess.run(
        [
            find_ffmpeg(),
            "-nostdin",
            "-hide_banner",
            "-protocol_whitelist",
            "file,pipe",
            "-format_whitelist",
            "mov,matroska,webm,mp3,wav,ogg,aac,flac",
            "-i",
            str(source),
        ],
        capture_output=True,
        timeout=30,
    )
    metadata = result.stderr.decode(errors="replace")
    match = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", metadata)
    if not match:
        raise ValueError("Could not read video duration; upload a valid video")
    duration = int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])
    return {
        "format": {"duration": duration},
        "streams": [{"codec_type": "audio"}]
        if re.search(r"Stream .*Audio:", metadata)
        else [],
    }


def run(args):
    process = subprocess.run(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1800
    )
    if process.returncode:
        raise RuntimeError(process.stderr.decode(errors="replace")[-1500:])
    return process.stdout


def execute(store, job):
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("Install FFmpeg or the imageio-ffmpeg Python package")
    with tempfile.TemporaryDirectory(prefix="bf-render-") as directory:
        directory = Path(directory)
        output = directory / "output.mp4"
        if job["kind"] == "segment":
            payload = job["payload"]
            clip = payload["clip"]
            source = store.get("assets/" + payload["project"] + "/" + clip["asset"])
            probe = probe_media(source)
            duration = float(probe.get("format", {}).get("duration", 0))
            if clip["end"] > duration + 0.1:
                raise ValueError("Trim end exceeds source duration")
            has_audio = (
                any(s.get("codec_type") == "audio" for s in probe["streams"])
                and not clip["muted"]
            )
            caption = directory / "caption.txt"
            caption.write_text(clip["caption"], encoding="utf-8")
            filters = "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30"
            if clip["caption"]:
                filters += f",drawtext=textfile='{caption}':expansion=none:fontcolor=white:fontsize=32:box=1:boxcolor=black@0.6:boxborderw=12:x=(w-text_w)/2:y=h-text_h-40"
            args = [
                ffmpeg,
                "-v",
                "error",
                "-nostdin",
                "-y",
                "-protocol_whitelist",
                "file,pipe",
                "-format_whitelist",
                "mov,matroska,webm,mp3,wav,ogg,aac,flac",
                "-ss",
                str(clip["start"]),
                "-i",
                str(source),
            ]
            if not has_audio:
                args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
            args += [
                "-t",
                str(clip["end"] - clip["start"]),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0" if has_audio else "1:a:0",
                "-vf",
                filters,
                "-af",
                f"aresample=48000,asetpts=PTS-STARTPTS,volume={clip.get('volume', 1)}",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-ac",
                "2",
                "-ar",
                "48000",
                "-video_track_timescale",
                "90000",
                "-movflags",
                "+faststart",
                str(output),
            ]
            run(args)
            key = "segments/" + payload["cache_key"] + ".mp4"
        elif "timeline" in job["payload"]:
            from .rendering import assemble

            assemble(store, job["payload"], output, ffmpeg, run)
            key = "exports/" + job["render"] + ".mp4"
        else:
            paths = [store.get(key) for key in job["payload"]["keys"]]
            # All filenames are generated identifiers under the controlled store.
            listing = directory / "concat.txt"
            listing.write_text(
                "".join("file '" + str(p).replace("'", "'\\''") + "'\n" for p in paths)
            )
            run(
                [
                    ffmpeg,
                    "-v",
                    "error",
                    "-nostdin",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(listing),
                    "-c",
                    "copy",
                    "-movflags",
                    "+faststart",
                    str(output),
                ]
            )
            key = "exports/" + job["render"] + ".mp4"
        store.put(key, output)
        return key


def process_job(store, job, heartbeat, finish):
    stopped = threading.Event()
    lost = threading.Event()

    def pulse():
        while not stopped.wait(30):
            try:
                heartbeat(job["id"], job["lease"])
            except Exception:
                lost.set()
                return

    thread = threading.Thread(target=pulse, daemon=True)
    thread.start()
    try:
        result = execute(store, job)
        if not lost.is_set():
            finish(job["id"], job["lease"], result=result)
    except Exception as error:
        print("Render task failed:", str(error), flush=True)
        if not lost.is_set():
            try:
                finish(job["id"], job["lease"], error=str(error))
            except Exception as callback_error:
                print("Result callback failed:", callback_error, flush=True)
    finally:
        stopped.set()
        thread.join(timeout=1)


def local_loop(app):
    while True:
        try:
            job = app.claim()
            if job:
                process_job(app.store, job, app.heartbeat, app.finish)
            else:
                time.sleep(1)
        except Exception as error:
            print("Worker:", error, flush=True)
            time.sleep(3)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", required=True)
    args = parser.parse_args()
    token = os.environ["BRANCHFRAME_WORKER_TOKEN"]
    if not os.environ.get("BRANCHFRAME_BUCKET"):
        raise SystemExit("Remote workers require BRANCHFRAME_BUCKET")
    store = Store(os.environ.get("BRANCHFRAME_DATA", "worker-data"))

    def request(path, data):
        body = json.dumps(data).encode()
        req = urllib.request.Request(
            args.api.rstrip("/") + "/internal/" + path,
            body,
            {"Content-Type": "application/json", "Authorization": "Bearer " + token},
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)

    def heartbeat(jid, lease):
        return request("heartbeat", {"id": jid, "lease": lease})

    def finish(jid, lease, result=None, error=None):
        return request(
            "finish", {"id": jid, "lease": lease, "result": result, "error": error}
        )

    while True:
        try:
            job = request("claim", {})
            if job:
                process_job(store, job, heartbeat, finish)
            else:
                time.sleep(2)
        except Exception as error:
            print("Remote worker:", error, flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
