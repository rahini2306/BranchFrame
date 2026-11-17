import hashlib
import re
from unittest.mock import patch

from branchframe.domain import merge
from tests.test_api import APIFixture, Client
from tests.test_domain import clip, tl


class MemoryMail:
    configured = True

    def __init__(self):
        self.messages = []

    def send(self, to, subject, body):
        self.messages.append((to, subject, body))

    def token(self):
        return re.search(r"token=([^\s]+)", self.messages[-1][2])[1]


class AccountTests(APIFixture):
    def test_delivery_failure_does_not_leave_a_valid_token_or_block_retry(self):
        with patch.object(self.mail, "send", side_effect=OSError("mail offline")):
            self.assertEqual(
                self.client.request("/api/request-verification", {})[0], 503
            )
        with self.app.store.connect() as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM account_tokens").fetchone()[0], 0
            )
        self.client.ok("/api/request-verification", {})
        self.assertEqual(len(self.mail.messages), 1)

    def setUp(self):
        super().setUp()
        self.mail = MemoryMail()
        self.app.mailer = self.mail

    def test_verification_single_use_and_hashed_token(self):
        self.client.ok("/api/request-verification", {})
        token = self.mail.token()
        with self.app.store.connect() as db:
            value = db.execute("SELECT hash FROM account_tokens").fetchone()[0]
            self.assertNotEqual(value, token)
            self.assertEqual(value, hashlib.sha256(token.encode()).hexdigest())
        self.client.ok("/api/verify-email", {"token": token})
        self.assertTrue(self.client.ok("/api/me")["email_verified"])
        self.assertEqual(
            self.client.request("/api/verify-email", {"token": token})[0], 400
        )

    def test_reset_revokes_sessions_and_changes_password(self):
        anon = Client(self.app)
        anon.ok("/api/request-password-reset", {"email": "owner@example.test"})
        token = self.mail.token()
        anon.ok(
            "/api/reset-password",
            {"token": token, "password": "a different secure password"},
        )
        self.assertEqual(self.client.request("/api/me")[0], 401)
        anon.ok(
            "/api/login",
            {"email": "owner@example.test", "password": "a different secure password"},
        )
        self.assertEqual(
            anon.request(
                "/api/reset-password", {"token": token, "password": "another password"}
            )[0],
            400,
        )

    def test_unknown_reset_has_same_public_response(self):
        a = self.client.ok(
            "/api/request-password-reset", {"email": "owner@example.test"}
        )
        b = self.client.ok(
            "/api/request-password-reset", {"email": "nobody@example.test"}
        )
        self.assertEqual(a, b)
        self.assertEqual(len(self.mail.messages), 1)

    def test_expired_token_is_rejected(self):
        self.client.ok("/api/request-verification", {})
        token = self.mail.token()
        with self.app.store.connect(immediate=True) as db:
            db.execute("UPDATE account_tokens SET expires=0")
        self.assertEqual(
            self.client.request("/api/verify-email", {"token": token})[0], 400
        )

    def test_verified_mode_blocks_writes_until_verified(self):
        with patch.dict("os.environ", {"BRANCHFRAME_REQUIRE_VERIFIED_EMAIL": "1"}):
            self.assertEqual(
                self.client.request("/api/projects", {"name": "Blocked"})[0], 403
            )
            self.client.ok("/api/request-verification", {})
            self.client.ok("/api/verify-email", {"token": self.mail.token()})
            self.client.ok("/api/projects", {"name": "Allowed"})

    def test_mail_cooldown_and_recovery_unavailable_are_explicit(self):
        self.client.ok("/api/request-verification", {})
        self.client.ok("/api/request-verification", {})
        self.assertEqual(len(self.mail.messages), 1)
        self.mail.configured = False
        self.assertEqual(
            self.client.request(
                "/api/request-password-reset", {"email": "owner@example.test"}
            )[0],
            503,
        )


class CollaborationTests(APIFixture):
    def test_event_stream_includes_shared_revision_and_review_gate(self):
        self.client.ok(self.url + "/draft?branch=main")
        with patch("branchframe.collaboration.time.sleep", side_effect=BrokenPipeError):
            status, body, headers = self.client.request(
                self.url + "/events?branch=main"
            )
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "text/event-stream")
        self.assertIn(b"event: draft", body)
        self.assertIn(b'"revision":0', body)
        self.assertIn(b'"approval":', body)

    def start(self):
        aid = self.upload()
        self.commit(tl(clip(asset=aid)))
        other = self.other()
        self.client.ok(
            self.url + "/members", {"email": "other@example.test", "role": "editor"}
        )
        return aid, other, self.client.ok(self.url + "/draft?branch=main")

    def test_independent_simultaneous_edits_merge(self):
        aid, other, base = self.start()
        self.client.ok(
            self.url + "/draft",
            {
                "branch": "main",
                "revision": base["revision"],
                "timeline": tl(clip(asset=aid, caption="Title")),
            },
        )
        merged = other.ok(
            self.url + "/draft",
            {
                "branch": "main",
                "revision": base["revision"],
                "timeline": tl(clip(asset=aid, muted=True)),
            },
        )
        self.assertEqual(
            merged["timeline"], tl(clip(asset=aid, caption="Title", muted=True))
        )

    def test_conflicting_live_edits_preserve_server_and_reject_stale_save(self):
        aid, other, base = self.start()
        self.client.ok(
            self.url + "/draft",
            {
                "branch": "main",
                "revision": base["revision"],
                "timeline": tl(clip(asset=aid, caption="One")),
            },
        )
        status, _, _ = other.request(
            self.url + "/draft",
            {
                "branch": "main",
                "revision": base["revision"],
                "timeline": tl(clip(asset=aid, caption="Two")),
            },
        )
        self.assertEqual(status, 409)
        self.assertEqual(
            self.client.ok(self.url + "/draft?branch=main")["timeline"]["clips"][0][
                "caption"
            ],
            "One",
        )

    def test_commit_requires_latest_shared_draft(self):
        aid, other, base = self.start()
        other.ok(
            self.url + "/draft",
            {
                "branch": "main",
                "revision": 0,
                "timeline": tl(clip(asset=aid, caption="Shared")),
            },
        )
        head = self.snapshot()["branches"][0]["head"]
        self.assertEqual(
            self.client.request(
                self.url + "/commits",
                {
                    "branch": "main",
                    "expected_head": head,
                    "message": "Old",
                    "timeline": tl(clip(asset=aid)),
                },
            )[0],
            409,
        )

    def test_reviewer_cannot_modify_draft(self):
        aid, other, base = self.start()
        self.client.ok(
            self.url + "/members", {"email": "other@example.test", "role": "reviewer"}
        )
        self.assertEqual(
            other.request(
                self.url + "/draft",
                {"branch": "main", "revision": 0, "timeline": tl(clip(asset=aid))},
            )[0],
            403,
        )

    def test_audio_changes_merge_by_identity(self):
        a = {
            "id": "music1",
            "asset": "a" * 32,
            "start": 0,
            "end": 4,
            "at": 0,
            "volume": 0.2,
        }
        b = {**a, "id": "music2"}
        result = merge(tl(), {"clips": [], "audio": [a]}, {"clips": [], "audio": [b]})
        self.assertFalse(result["conflicts"])
        self.assertEqual(len(result["timeline"]["audio"]), 2)


class ApprovalTests(APIFixture):
    def test_independent_approval_blocks_and_then_allows_export(self):
        aid = self.upload()
        cid = self.commit(tl(clip(asset=aid)))
        self.client.ok(self.url + "/settings", {"require_approval": True})
        self.assertEqual(
            self.client.request(self.url + "/renders", {"commit": cid})[0], 403
        )
        self.assertEqual(
            self.client.request(
                self.url + "/reviews",
                {"commit": cid, "kind": "approve", "body": "Self approval"},
            )[0],
            400,
        )
        other = self.other()
        self.client.ok(
            self.url + "/members", {"email": "other@example.test", "role": "reviewer"}
        )
        other.ok(
            self.url + "/reviews",
            {"commit": cid, "kind": "approve", "body": "Reviewed"},
        )
        self.client.ok(self.url + "/renders", {"commit": cid})
        other.ok(
            self.url + "/reviews",
            {"commit": cid, "kind": "request_changes", "body": "Please revise"},
        )
        self.assertEqual(
            self.client.request(self.url + "/renders", {"commit": cid})[0], 403
        )
        other.ok(
            self.url + "/reviews",
            {"commit": cid, "kind": "approve", "body": "Resolved"},
        )
        self.client.ok(self.url + "/renders", {"commit": cid})
        newcid = self.commit(tl(clip(asset=aid, caption="New edit")))
        self.assertEqual(
            self.client.request(self.url + "/renders", {"commit": newcid})[0], 403
        )

    def test_new_project_defaults_to_approval_required(self):
        project = self.client.ok("/api/projects", {"name": "Reviewed film"})["id"]
        self.assertTrue(self.client.ok("/api/projects/" + project)["require_approval"])

    def test_only_owner_changes_policy(self):
        other = self.other()
        self.client.ok(
            self.url + "/members", {"email": "other@example.test", "role": "editor"}
        )
        self.assertEqual(
            other.request(self.url + "/settings", {"require_approval": False})[0], 403
        )


class EdgeTests(APIFixture):
    def test_edge_secret_guards_app_but_keeps_health_checks_working(self):
        with patch.dict(
            "os.environ", {"BRANCHFRAME_ORIGIN_SECRET": "private-test-origin"}
        ):
            self.assertEqual(self.client.request("/api/me")[0], 403)
            self.assertEqual(self.client.request("/api/health")[0], 200)
            self.assertEqual(
                self.client.request(
                    "/api/me", headers={"X-BranchFrame-Origin": "private-test-origin"}
                )[0],
                200,
            )
            self.assertEqual(self.client.request("/internal/claim", {})[0], 401)
            self.client.ok(
                "/api/login",
                {"email": "owner@example.test", "password": "test-password-123"},
                headers={
                    "X-BranchFrame-Origin": "private-test-origin",
                    "CloudFront-Viewer-Address": "203.0.113.4:12345",
                },
            )
            self.assertIn(("login", "203.0.113.4"), self.app.rate_limit.buckets)
