"""Exercise the actual HTTP handler using byte streams (no listening socket)."""

from concurrent.futures import ThreadPoolExecutor
from email.message import Message
from io import BytesIO
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from branchframe.server import App, Handler, APIError
from tests.test_domain import clip, tl


class Client:
    def __init__(self, app):
        self.app = app
        self.cookie = ""

    def request(self, path, data=None, method=None, headers=None, raw=None):
        handler = Handler.__new__(Handler)
        handler.server = SimpleNamespace(app=self.app)
        handler.command = method or (
            "POST" if data is not None or raw is not None else "GET"
        )
        handler.path = path
        handler.request_version = "HTTP/1.1"
        handler.requestline = handler.command + " " + path + " HTTP/1.1"
        handler.client_address = ("127.0.0.1", 1234)
        handler.log_message = lambda *args: None
        body = (
            raw
            if raw is not None
            else json.dumps(data).encode()
            if data is not None
            else b""
        )
        handler.headers = Message()
        handler.headers["Host"] = "localhost:8000"
        handler.headers["Content-Length"] = str(len(body))
        if self.cookie:
            handler.headers["Cookie"] = self.cookie
        for key, value in (headers or {}).items():
            handler.headers[key] = value
        handler.rfile = BytesIO(body)
        handler.wfile = BytesIO()
        handler.dispatch()
        response = handler.wfile.getvalue()
        head, body = response.split(b"\r\n\r\n", 1)
        lines = head.decode().split("\r\n")
        status = int(lines[0].split()[1])
        response_headers = {
            k.lower(): v.strip()
            for k, v in (line.split(":", 1) for line in lines[1:] if ":" in line)
        }
        if "set-cookie" in response_headers:
            self.cookie = response_headers["set-cookie"].split(";")[0]
        value = (
            json.loads(body)
            if response_headers.get("content-type") == "application/json"
            else body
        )
        return status, value, response_headers

    def ok(self, path, data=None, **kwargs):
        status, value, _ = self.request(path, data, **kwargs)
        if status >= 400:
            raise AssertionError((status, value))
        return value


class APIFixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = App(self.directory.name)
        self.client = Client(self.app)
        self.client.ok(
            "/api/register",
            {
                "name": "Owner",
                "email": "owner@example.test",
                "password": "test-password-123",
            },
        )
        self.user = self.client.ok("/api/me")["id"]
        self.project = self.client.ok("/api/projects", {"name": "Film", "require_approval":False})["id"]
        self.url = "/api/projects/" + self.project

    def tearDown(self):
        self.directory.cleanup()

    def snapshot(self):
        return self.client.ok(self.url)

    def upload(self):
        return self.client.ok(
            self.url + "/assets",
            raw=b"not-a-real-video",
            headers={"X-Filename": "sample.mp4"},
        )["id"]

    def commit(self, timeline, branch="main", expected=None):
        snapshot = self.snapshot()
        head = next(b["head"] for b in snapshot["branches"] if b["name"] == branch)
        return self.client.ok(
            self.url + "/commits",
            {
                "branch": branch,
                "expected_head": expected or head,
                "message": "An edit",
                "timeline": timeline,
            },
        )["id"]

    def other(self, email="other@example.test"):
        client = Client(self.app)
        client.ok(
            "/api/register",
            {"name": "Other", "email": email, "password": "test-password-123"},
        )
        return client


class APITests(APIFixture):
    def test_auth_and_logout(self):
        self.assertEqual(Client(self.app).request("/api/projects")[0], 401)
        self.client.ok("/api/logout", {})
        self.assertEqual(self.client.request("/api/me")[0], 401)

    def test_password_not_stored_in_plaintext(self):
        with self.app.store.connect() as db:
            value = db.execute("SELECT password FROM users").fetchone()[0]
            self.assertNotIn("test-password", value)
            self.assertIn(":", value)

    def test_invalid_login(self):
        self.assertEqual(
            Client(self.app).request(
                "/api/login",
                {"email": "owner@example.test", "password": "incorrect-pass"},
            )[0],
            401,
        )

    def test_project_isolation(self):
        other = self.other()
        self.assertEqual(other.request(self.url)[0], 404)
        self.assertEqual(
            other.request(
                self.url + "/assets", raw=b"x", headers={"X-Filename": "x.mp4"}
            )[0],
            404,
        )

    def test_reviewer_permissions(self):
        other = self.other()
        self.client.ok(
            self.url + "/members", {"email": "other@example.test", "role": "reviewer"}
        )
        other.ok(self.url)
        head = self.snapshot()["branches"][0]["head"]
        self.assertEqual(
            other.request(self.url + "/branches", {"name": "bad", "head": head})[0], 403
        )
        other.ok(
            self.url + "/reviews",
            {"commit": head, "body": "Looks good", "kind": "approve", "at": 2},
        )
        self.assertEqual(len(self.snapshot()["reviews"]), 1)

    def test_optimistic_lock(self):
        before = self.snapshot()["branches"][0]["head"]
        self.commit(tl())
        status, _, _ = self.client.request(
            self.url + "/commits",
            {
                "branch": "main",
                "expected_head": before,
                "message": "Stale",
                "timeline": tl(),
            },
        )
        self.assertEqual(status, 409)

    def test_cross_project_asset_rejected(self):
        foreign = self.client.ok("/api/projects", {"name": "Other film"})["id"]
        aid = self.client.ok(
            "/api/projects/" + foreign + "/assets",
            raw=b"x",
            headers={"X-Filename": "x.mp4"},
        )["id"]
        with self.assertRaises(AssertionError):
            self.commit(tl(clip(asset=aid)))

    def test_asset_range_and_access(self):
        aid = self.upload()
        status, body, headers = self.client.request(
            self.url + "/assets/" + aid + "/media", headers={"Range": "bytes=0-2"}
        )
        self.assertEqual(status, 206)
        self.assertEqual(body, b"not")
        self.assertEqual(headers["content-range"], "bytes 0-2/16")
        self.assertEqual(
            self.other().request(self.url + "/assets/" + aid + "/media")[0], 404
        )

    def test_merge_preserves_both_parents(self):
        aid = self.upload()
        initial = self.commit(tl(clip(asset=aid)))
        self.client.ok(self.url + "/branches", {"name": "social", "head": initial})
        ours = self.commit(tl(clip(asset=aid, caption="Main")))
        theirs = self.commit(tl(clip(asset=aid, caption="Social")), branch="social")
        request = {
            "target": "main",
            "source": "social",
            "expected_target": ours,
            "expected_source": theirs,
        }
        preview = self.client.ok(self.url + "/merge", request)
        self.assertEqual(len(preview["conflicts"]), 1)
        self.assertEqual(
            self.client.request(self.url + "/merge", {**request, "apply": True})[0], 409
        )
        result = self.client.ok(
            self.url + "/merge",
            {**request, "apply": True, "resolutions": {"clips/one/caption": "theirs"}},
        )
        merged = next(c for c in self.snapshot()["commits"] if c["id"] == result["id"])
        self.assertEqual(merged["parents"], [ours, theirs])
        self.assertEqual(merged["timeline"]["clips"][0]["caption"], "Social")
        self.assertEqual(self.client.request(self.url + "/merge", request)[0], 409)

    def test_queue_exclusive_claim_and_stale_result(self):
        aid = self.upload()
        cid = self.commit(tl(clip(asset=aid)))
        self.client.ok(self.url + "/renders", {"commit": cid})
        with ThreadPoolExecutor(max_workers=4) as pool:
            claims = list(pool.map(lambda _: self.app.claim(), range(4)))
        self.assertEqual(sum(bool(c) for c in claims), 1)
        job = next(c for c in claims if c)
        with self.app.store.connect(immediate=True) as db:
            db.execute("UPDATE jobs SET expires=0 WHERE id=?", (job["id"],))
        retry = self.app.claim()
        self.assertEqual(job["id"], retry["id"])
        self.assertNotEqual(job["lease"], retry["lease"])
        with self.assertRaises(APIError):
            self.app.finish(job["id"], job["lease"], result="old")

    def test_segment_cache_and_assembly(self):
        aid = self.upload()
        cid = self.commit(tl(clip(asset=aid)))
        first = self.client.ok(self.url + "/renders", {"commit": cid})
        segment = self.app.claim()
        key = "segments/" + segment["payload"]["cache_key"] + ".mp4"
        self.app.finish(segment["id"], segment["lease"], result=key)
        assembly = self.app.claim()
        self.assertEqual(assembly["kind"], "assemble")
        self.app.finish(
            assembly["id"], assembly["lease"], result="exports/" + first["id"] + ".mp4"
        )
        second = self.client.ok(self.url + "/renders", {"commit": cid})
        self.assertEqual(second["cache_hits"], 1)
        self.assertEqual(self.app.claim()["kind"], "assemble")

    def test_failure_retry_limit(self):
        aid = self.upload()
        cid = self.commit(tl(clip(asset=aid)))
        self.client.ok(self.url + "/renders", {"commit": cid})
        for _ in range(3):
            job = self.app.claim()
            self.app.finish(job["id"], job["lease"], error="broken input")
        self.assertIsNone(self.app.claim())
        self.assertEqual(self.snapshot()["renders"][0]["status"], "failed")

    def test_forged_result_key_rejected(self):
        aid = self.upload()
        cid = self.commit(tl(clip(asset=aid)))
        self.client.ok(self.url + "/renders", {"commit": cid})
        job = self.app.claim()
        with self.assertRaises(APIError):
            self.app.finish(job["id"], job["lease"], result="exports/another-user.mp4")

    def test_cross_origin_rejected(self):
        self.assertEqual(
            self.client.request(
                "/api/logout", {}, headers={"Origin": "https://evil.example"}
            )[0],
            403,
        )

    def test_static_traversal_rejected(self):
        self.assertEqual(self.client.request("/../branchframe/server.py")[0], 404)

    def test_worker_auth_required(self):
        self.assertEqual(self.client.request("/internal/claim", {})[0], 401)
        with patch.dict("os.environ", {"BRANCHFRAME_WORKER_TOKEN": "test-secret"}):
            self.assertEqual(
                self.client.request(
                    "/internal/claim",
                    {},
                    headers={"Authorization": "Bearer test-secret"},
                )[0],
                200,
            )

    def test_static_ui_and_security_headers(self):
        status, body, headers = self.client.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"BranchFrame", body)
        self.assertIn("frame-ancestors 'none'", headers["content-security-policy"])


if __name__ == "__main__":
    unittest.main()
