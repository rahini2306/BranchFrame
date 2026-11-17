#!/usr/bin/env python3
"""Exercise a deployed site with an existing verified account; retain test evidence.

Creates a small smoke-test project and export. It does not delete user data or
claim worker failover/backup recovery have been verified. Never pass passwords
in command-line arguments; getpass keeps them out of terminal history.
"""

import argparse
import getpass
import http.cookiejar
import json
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--output", default="cloud-smoke-results.json")
    args = parser.parse_args()
    base = args.url.rstrip("/")
    if urlsplit(base).scheme != "https":
        parser.error("Use the deployed HTTPS website URL")
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def request(path, data=None, raw=None, headers=None, binary=False):
        body = (
            raw
            if raw is not None
            else json.dumps(data).encode()
            if data is not None
            else None
        )
        h = {"Content-Type": "application/json", **(headers or {})}
        with opener.open(
            urllib.request.Request(base + path, data=body, headers=h), timeout=60
        ) as response:
            value = response.read()
            return value if binary else json.loads(value)

    report = {
        "url": base,
        "started": time.time(),
        "checks": [],
        "deployment_status": "smoke-test-only",
    }
    report["health"] = request("/api/health")
    request(
        "/api/login",
        {
            "email": args.email,
            "password": getpass.getpass("Existing account password: "),
        },
    )
    if not request("/api/me")["email_verified"]:
        raise RuntimeError("Verify this account email before testing the deployment")
    session = next(c for c in jar if c.name == "bf_session")
    assert session.secure and session.has_nonstandard_attr("HttpOnly"), (
        "Missing secure session flags"
    )
    report["checks"].append("HTTPS login and secure HttpOnly cookie")
    project = request(
        "/api/projects",
        {
            "name": "Deployment smoke " + time.strftime("%Y-%m-%d %H:%M"),
            "require_approval": False,
        },
    )["id"]
    root = "/api/projects/" + project
    report["project"] = project
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "fixture.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=s=320x240:r=30:d=2",
                "-f",
                "lavfi",
                "-i",
                "sine=duration=2",
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
        asset = request(
            root + "/assets",
            raw=source.read_bytes(),
            headers={"X-Filename": "smoke.mp4"},
        )["id"]
    snapshot = request(root)
    head = next(b["head"] for b in snapshot["branches"] if b["name"] == "main")
    timeline = {
        "clips": [
            {
                "id": "smoke",
                "asset": asset,
                "start": 0,
                "end": 1.5,
                "caption": "Cloud smoke test",
                "muted": False,
            }
        ]
    }
    commit = request(
        root + "/commits",
        {
            "branch": "main",
            "expected_head": head,
            "timeline": timeline,
            "message": "Deployment smoke",
        },
    )["id"]
    report["checks"].append("Upload, authorized project access, and immutable commit")
    for attempt in range(2):
        started = time.monotonic()
        rid = request(root + "/renders", {"commit": commit})["id"]
        deadline = started + 600
        while time.monotonic() < deadline:
            render = next(r for r in request(root)["renders"] if r["id"] == rid)
            if render["status"] == "failed":
                raise RuntimeError("Export failed: " + str(render.get("error")))
            if render["status"] == "done":
                break
            time.sleep(3)
        else:
            raise TimeoutError("Export did not complete within ten minutes")
        assert len(request(root + "/renders/" + rid + "/media", binary=True)) > 1000
        report["warm" if attempt else "cold"] = {
            "seconds": round(time.monotonic() - started, 3),
            "cache_hits": render["cache_hits"],
            "render": rid,
        }
    assert report["warm"]["cache_hits"] >= 1
    report["checks"].append("Real MP4 export and repeated-export segment cache hit")
    try:
        urllib.request.urlopen(base + root, timeout=30)
    except urllib.error.HTTPError as error:
        assert error.code == 401
    else:
        raise AssertionError("Anonymous project access was not rejected")
    report["checks"].append("Anonymous project access rejected")
    report["finished"] = time.time()
    Path(args.output).write_text(json.dumps(report, indent=2) + "\n")
    print(
        "Passed. Evidence saved to "
        + args.output
        + ". Test project retained: "
        + project
    )


if __name__ == "__main__":
    main()
