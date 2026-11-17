"""Resumable 5 MiB uploads with per-part integrity and idempotent completion."""

import hashlib
import math
from pathlib import Path
import re
import shutil
import tempfile
import time

PART_SIZE = 5 * 1024 * 1024
MAX_UPLOAD = 250 * 1024 * 1024


def handle(handler, project, user, tail):
    # Imported lazily to avoid a module cycle.
    from .server import APIError, text, uid

    store = handler.app.store
    with store.connect() as db:
        handler.app.access(db, project, user, write=True)
    if not tail and handler.command == "POST":
        data = handler.body()
        name = text(data, "name", 160)
        size = data.get("size")
        fingerprint = text(data, "fingerprint", 64)
        if (
            isinstance(size, bool)
            or not isinstance(size, int)
            or not 0 < size <= MAX_UPLOAD
        ):
            raise APIError(400, "Upload size must be 1 byte to 250 MB")
        if Path(name).suffix.lower() not in (".mp4", ".mov", ".webm", ".m4v"):
            raise APIError(400, "Unsupported video format")
        if not re.fullmatch("[a-f0-9]{64}", fingerprint):
            raise APIError(400, "Invalid fingerprint")
        with store.connect(immediate=True) as db:
            expired = db.execute(
                "SELECT id FROM uploads WHERE created<?", (time.time() - 86400,)
            ).fetchall()
            for row in expired:
                shutil.rmtree(store.root / "uploads" / row["id"], ignore_errors=True)
                db.execute("DELETE FROM upload_parts WHERE upload=?", (row["id"],))
                db.execute("DELETE FROM uploads WHERE id=?", (row["id"],))
            row = db.execute(
                "SELECT * FROM uploads WHERE project=? AND owner=? AND fingerprint=? AND size=? AND name=? AND asset IS NULL",
                (project, user, fingerprint, size, name),
            ).fetchone()
            if not row:
                count = db.execute(
                    "SELECT COUNT(*) FROM uploads WHERE project=? AND asset IS NULL",
                    (project,),
                ).fetchone()[0]
                if count >= 5:
                    raise APIError(
                        429, "Finish an existing upload before starting another"
                    )
                upload_id = uid()
                db.execute(
                    "INSERT INTO uploads VALUES(?,?,?,?,?,?,?,NULL)",
                    (upload_id, project, user, name, size, fingerprint, time.time()),
                )
            else:
                upload_id = row["id"]
        return handler.reply(200, {"id": upload_id, "part_size": PART_SIZE})
    match = re.fullmatch(r"([a-f0-9]{32})(?:/(parts)/(\d+)|/(complete))?", tail)
    if not match:
        raise APIError(404, "Upload not found")
    upload_id = match[1]
    with store.connect(immediate=handler.command == "POST") as db:
        row = db.execute(
            "SELECT * FROM uploads WHERE id=? AND project=? AND owner=?",
            (upload_id, project, user),
        ).fetchone()
        if not row:
            raise APIError(404, "Upload not found")
        if handler.command == "GET":
            parts = {
                str(r["position"]): r["sha"]
                for r in db.execute(
                    "SELECT * FROM upload_parts WHERE upload=?", (upload_id,)
                )
            }
            return handler.reply(
                200,
                {
                    "id": upload_id,
                    "received": parts,
                    "part_size": PART_SIZE,
                    "asset": row["asset"],
                },
            )
        if row["asset"]:
            return handler.reply(200, {"id": row["asset"], "completed": True})
        directory = store.root / "uploads" / upload_id
        directory.mkdir(parents=True, exist_ok=True)
        if match[2] == "parts":
            position = int(match[3])
            total = math.ceil(row["size"] / PART_SIZE)
            if not 0 <= position < total:
                raise APIError(400, "Invalid part number")
            expected = min(PART_SIZE, row["size"] - position * PART_SIZE)
            if int(handler.headers.get("Content-Length", "0")) != expected:
                raise APIError(400, "Invalid part length")
            data = handler.rfile.read(expected)
            if len(data) != expected:
                raise APIError(400, "Upload interrupted")
            sha = hashlib.sha256(data).hexdigest()
            import hmac

            if not hmac.compare_digest(sha, handler.headers.get("X-Part-SHA256", "")):
                raise APIError(400, "Part checksum mismatch")
            with tempfile.NamedTemporaryFile(dir=directory, delete=False) as f:
                f.write(data)
                temporary = Path(f.name)
            temporary.replace(directory / str(position))
            db.execute(
                "INSERT INTO upload_parts VALUES(?,?,?) ON CONFLICT(upload,position) DO UPDATE SET sha=excluded.sha",
                (upload_id, position, sha),
            )
            db.commit()
            return handler.reply(200, {"ok": True, "sha": sha})
        if match[4] == "complete":
            expected = math.ceil(row["size"] / PART_SIZE)
            if (
                db.execute(
                    "SELECT COUNT(*) FROM upload_parts WHERE upload=?", (upload_id,)
                ).fetchone()[0]
                != expected
            ):
                raise APIError(409, "Upload is missing parts")
            combined = directory / "assembled"
            sha = hashlib.sha256()
            with combined.open("wb") as out:
                for i in range(expected):
                    with (directory / str(i)).open("rb") as part:
                        while block := part.read(1024 * 1024):
                            out.write(block)
                            sha.update(block)
            aid = uid()
            store.put("assets/" + project + "/" + aid, combined)
            db.execute(
                "INSERT INTO assets VALUES(?,?,?,?,?,?)",
                (aid, project, row["name"], sha.hexdigest(), row["size"], time.time()),
            )
            db.execute("UPDATE uploads SET asset=? WHERE id=?", (aid, upload_id))
            # Keep metadata for idempotent completion while discarding staged bytes.
            shutil.rmtree(directory, ignore_errors=True)
            db.commit()
            return handler.reply(201, {"id": aid, "completed": True})
        raise APIError(405, "Method not allowed")
