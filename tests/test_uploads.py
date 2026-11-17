import hashlib
import shutil
from unittest.mock import patch
from tests.test_api import APIFixture


class ResumableTests(APIFixture):
    def begin(self, size=3):
        return self.client.ok(
            self.url + "/uploads",
            {"name": "demo.mp4", "size": size, "fingerprint": "a" * 64},
        )["id"]

    def part(self, uid, data, position=0, sha=None):
        return self.client.request(
            self.url + "/uploads/" + uid + "/parts/" + str(position),
            raw=data,
            headers={"X-Part-SHA256": sha or hashlib.sha256(data).hexdigest()},
        )

    def test_resume_and_idempotent_completion(self):
        upload = self.begin()
        self.assertEqual(self.begin(), upload)
        self.assertEqual(self.part(upload, b"abc")[0], 200)
        status = self.client.ok(self.url + "/uploads/" + upload)
        self.assertEqual(status["received"]["0"], hashlib.sha256(b"abc").hexdigest())
        first = self.client.ok(self.url + "/uploads/" + upload + "/complete", {})
        second = self.client.ok(self.url + "/uploads/" + upload + "/complete", {})
        self.assertEqual(first["id"], second["id"])
        body = self.client.ok(self.url + "/assets/" + first["id"] + "/media")
        self.assertEqual(body, b"abc")
        self.assertEqual(len(self.snapshot()["assets"]), 1)

    def test_completed_asset_is_committed_before_staging_cleanup(self):
        upload = self.begin()
        self.assertEqual(self.part(upload, b"abc")[0], 200)
        original = shutil.rmtree

        def check_before_cleanup(*args, **kwargs):
            with self.app.store.connect() as db:
                row = db.execute(
                    "SELECT asset FROM uploads WHERE id=?", (upload,)
                ).fetchone()
                self.assertIsNotNone(row["asset"])
            return original(*args, **kwargs)

        with patch(
            "branchframe.uploads.shutil.rmtree", side_effect=check_before_cleanup
        ):
            self.client.ok(self.url + "/uploads/" + upload + "/complete", {})

    def test_bad_checksum_rejected(self):
        upload = self.begin()
        self.assertEqual(self.part(upload, b"abc", sha="b" * 64)[0], 400)

    def test_incomplete_completion_rejected(self):
        upload = self.begin()
        self.assertEqual(
            self.client.request(self.url + "/uploads/" + upload + "/complete", {})[0],
            409,
        )

    def test_wrong_part_length_rejected(self):
        upload = self.begin()
        self.assertEqual(self.part(upload, b"ab")[0], 400)

    def test_upload_owner_isolation(self):
        upload = self.begin()
        other = self.other()
        self.client.ok(
            self.url + "/members", {"email": "other@example.test", "role": "editor"}
        )
        self.assertEqual(other.request(self.url + "/uploads/" + upload)[0], 404)
