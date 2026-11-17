"""Same-origin HTTP API, durable queue, project ACLs, and immutable revisions."""

import argparse
import hashlib
import hmac
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import time
from urllib.parse import urlsplit, parse_qs, unquote
import uuid

from .domain import canonical, digest, validate_timeline, merge, merge_base, diff
from .store import Store

WEB = Path(__file__).resolve().parent.parent / "web"
MAX_BODY = 1024 * 1024


def renderer_available():
    from .worker import find_ffmpeg

    return bool(find_ffmpeg())


def uid():
    return uuid.uuid4().hex


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    return (
        salt
        + ":"
        + hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 600000).hex()
    )


class APIError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


class App:
    def __init__(self, root):
        self.store = Store(root)

    def access(self, db, project, user, write=False, owner=False):
        row = db.execute(
            "SELECT role FROM members WHERE project=? AND user=?", (project, user)
        ).fetchone()
        if not row:
            raise APIError(404, "Project not found")
        if owner and row["role"] != "owner":
            raise APIError(403, "Owner access required")
        if write and row["role"] not in ("owner", "editor"):
            raise APIError(403, "Editor access required")
        return row["role"]

    def timeline(self, db, project, value):
        timeline = validate_timeline(value)
        for clip in timeline["clips"]:
            if not db.execute(
                "SELECT 1 FROM assets WHERE id=? AND project=?",
                (clip["asset"], project),
            ).fetchone():
                raise APIError(400, "Clip references an asset outside this project")
        return timeline

    def commit(self, db, project, parents, timeline, message, user):
        cid = uid()
        db.execute(
            "INSERT INTO commits VALUES(?,?,?,?,?,?,?)",
            (
                cid,
                project,
                canonical(parents),
                canonical(timeline),
                message,
                user,
                time.time(),
            ),
        )
        return cid

    def snapshot(self, db, project, user):
        role = self.access(db, project, user)
        result = dict(
            db.execute("SELECT * FROM projects WHERE id=?", (project,)).fetchone()
        )
        result["role"] = role
        for table in ("assets", "branches", "commits", "reviews", "renders"):
            result[table] = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM " + table + " WHERE project=?", (project,)
                )
            ]
        for c in result["commits"]:
            c["timeline"] = json.loads(c["timeline"])
            c["parents"] = json.loads(c["parents"])
        result["members"] = [
            dict(r)
            for r in db.execute(
                "SELECT u.name,u.email,m.role FROM members m JOIN users u ON u.id=m.user WHERE m.project=?",
                (project,),
            )
        ]
        for render in result["renders"]:
            render["jobs"] = [
                dict(r)
                for r in db.execute(
                    "SELECT id,kind,position,status,attempts,error FROM jobs WHERE render=? ORDER BY position",
                    (render["id"],),
                )
            ]
        return result

    def api(self, method, path, data, user, query):
        parts = [unquote(p) for p in path.strip("/").split("/")]
        with self.store.connect(immediate=method != "GET") as db:
            if path == "/api/me":
                return dict(
                    db.execute(
                        "SELECT id,email,name FROM users WHERE id=?", (user,)
                    ).fetchone()
                )
            if path == "/api/projects":
                if method == "GET":
                    return [
                        dict(r)
                        for r in db.execute(
                            "SELECT p.*,m.role FROM projects p JOIN members m ON m.project=p.id WHERE m.user=? ORDER BY created DESC",
                            (user,),
                        )
                    ]
                if method == "POST":
                    name = text(data, "name", 80)
                    project = uid()
                    db.execute(
                        "INSERT INTO projects VALUES(?,?,?,?)",
                        (project, name, user, time.time()),
                    )
                    db.execute(
                        "INSERT INTO members VALUES(?,?,?)", (project, user, "owner")
                    )
                    head = self.commit(
                        db, project, [], {"clips": []}, "Initial timeline", user
                    )
                    db.execute(
                        "INSERT INTO branches VALUES(?,?,?)", (project, "main", head)
                    )
                    return {"id": project}
            if len(parts) < 3 or parts[:2] != ["api", "projects"]:
                raise APIError(404, "Not found")
            project = parts[2]
            self.access(
                db,
                project,
                user,
                write=method != "GET" and (len(parts) < 4 or parts[3] != "reviews"),
            )
            if len(parts) == 3 and method == "GET":
                return self.snapshot(db, project, user)
            action = parts[3] if len(parts) > 3 else ""
            if action == "members" and method == "POST":
                self.access(db, project, user, owner=True)
                member = db.execute(
                    "SELECT id FROM users WHERE email=?",
                    (text(data, "email", 254).lower(),),
                ).fetchone()
                if not member:
                    raise APIError(400, "Ask this teammate to register first")
                role = data.get("role")
                if role not in ("editor", "reviewer"):
                    raise APIError(400, "Choose editor or reviewer")
                if member["id"] == user:
                    raise APIError(400, "Owner role cannot be changed")
                db.execute(
                    "INSERT INTO members VALUES(?,?,?) ON CONFLICT(project,user) DO UPDATE SET role=excluded.role",
                    (project, member["id"], role),
                )
                return {"ok": True}
            if action == "branches" and method == "POST":
                name = text(data, "name", 40)
                if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_/-]{0,39}", name):
                    raise APIError(400, "Invalid branch name")
                head = text(data, "head", 32)
                if not db.execute(
                    "SELECT 1 FROM commits WHERE id=? AND project=?", (head, project)
                ).fetchone():
                    raise APIError(400, "Unknown revision")
                if db.execute(
                    "SELECT 1 FROM branches WHERE project=? AND name=?", (project, name)
                ).fetchone():
                    raise APIError(409, "Branch already exists")
                db.execute("INSERT INTO branches VALUES(?,?,?)", (project, name, head))
                return {"ok": True}
            if action == "commits" and method == "POST":
                branch = text(data, "branch", 40)
                expected = text(data, "expected_head", 32)
                row = db.execute(
                    "SELECT head FROM branches WHERE project=? AND name=?",
                    (project, branch),
                ).fetchone()
                if not row or row["head"] != expected:
                    raise APIError(409, "Branch changed. Reload before committing.")
                timeline = self.timeline(db, project, data.get("timeline"))
                cid = self.commit(
                    db, project, [expected], timeline, text(data, "message", 200), user
                )
                db.execute(
                    "UPDATE branches SET head=? WHERE project=? AND name=?",
                    (cid, project, branch),
                )
                return {"id": cid}
            if action == "diff" and method == "GET":
                values = []
                for key in ("before", "after"):
                    row = db.execute(
                        "SELECT timeline FROM commits WHERE id=? AND project=?",
                        (query.get(key, [""])[0], project),
                    ).fetchone()
                    if not row:
                        raise APIError(404, "Revision not found")
                    values.append(json.loads(row["timeline"]))
                return diff(*values)
            if action == "merge" and method == "POST":
                target = text(data, "target", 40)
                source = text(data, "source", 40)
                if target == source:
                    raise APIError(400, "Choose two different branches")
                heads = {
                    r["name"]: r["head"]
                    for r in db.execute(
                        "SELECT * FROM branches WHERE project=?", (project,)
                    )
                }
                if target not in heads or source not in heads:
                    raise APIError(404, "Branch not found")
                for field, branch in [
                    ("expected_target", target),
                    ("expected_source", source),
                ]:
                    if data.get(field) != heads[branch]:
                        raise APIError(409, "Branches changed. Refresh merge preview.")
                commits = {
                    r["id"]: {
                        "parents": json.loads(r["parents"]),
                        "timeline": json.loads(r["timeline"]),
                    }
                    for r in db.execute(
                        "SELECT * FROM commits WHERE project=?", (project,)
                    )
                }
                base = merge_base(heads[target], heads[source], commits)
                result = merge(
                    commits[base]["timeline"],
                    commits[heads[target]]["timeline"],
                    commits[heads[source]]["timeline"],
                    data.get("resolutions"),
                )
                result["base"] = base
                if data.get("apply"):
                    if result["conflicts"]:
                        raise APIError(409, "Resolve every conflict before merging")
                    timeline = self.timeline(db, project, result["timeline"])
                    cid = self.commit(
                        db,
                        project,
                        [heads[target], heads[source]],
                        timeline,
                        "Merge " + source + " into " + target,
                        user,
                    )
                    db.execute(
                        "UPDATE branches SET head=? WHERE project=? AND name=?",
                        (cid, project, target),
                    )
                    result["id"] = cid
                return result
            if action == "reviews" and method == "POST":
                cid = text(data, "commit", 32)
                if not db.execute(
                    "SELECT 1 FROM commits WHERE id=? AND project=?", (cid, project)
                ).fetchone():
                    raise APIError(404, "Revision not found")
                kind = data.get("kind", "comment")
                if kind not in ("comment", "approve", "request_changes"):
                    raise APIError(400, "Invalid review type")
                at = float(data.get("at", 0))
                if not 0 <= at <= 3600:
                    raise APIError(400, "Invalid timestamp")
                db.execute(
                    "INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?)",
                    (
                        uid(),
                        project,
                        cid,
                        user,
                        kind,
                        text(data, "body", 1000),
                        at,
                        time.time(),
                    ),
                )
                return {"ok": True}
            if action == "renders" and method == "POST":
                cid = text(data, "commit", 32)
                row = db.execute(
                    "SELECT timeline FROM commits WHERE id=? AND project=?",
                    (cid, project),
                ).fetchone()
                if not row:
                    raise APIError(404, "Revision not found")
                clips = json.loads(row["timeline"])["clips"]
                if not clips:
                    raise APIError(400, "Add at least one clip before exporting")
                if (
                    db.execute(
                        "SELECT COUNT(*) FROM renders WHERE project=? AND status IN ('queued','rendering')",
                        (project,),
                    ).fetchone()[0]
                    >= 3
                ):
                    raise APIError(429, "Wait for an export to finish")
                rid = uid()
                db.execute(
                    "INSERT INTO renders(id,project,commit_id,status,created) VALUES(?,?,?,?,?)",
                    (rid, project, cid, "queued", time.time()),
                )
                hits = 0
                for i, clip in enumerate(clips):
                    asset = db.execute(
                        "SELECT sha FROM assets WHERE id=?", (clip["asset"],)
                    ).fetchone()
                    render_spec = {
                        k: v for k, v in clip.items() if k not in ("id", "asset")
                    }
                    key = digest(
                        {
                            "project": project,
                            "sha": asset["sha"],
                            "spec": render_spec,
                            "renderer": 2,
                        }
                    )
                    cached = db.execute(
                        "SELECT object_key FROM cache WHERE key=?", (key,)
                    ).fetchone()
                    payload = {"clip": clip, "project": project, "cache_key": key}
                    db.execute(
                        "INSERT INTO jobs(id,render,kind,position,payload,status,result) VALUES(?,?,?,?,?,?,?)",
                        (
                            uid(),
                            rid,
                            "segment",
                            i,
                            canonical(payload),
                            "done" if cached else "queued",
                            cached["object_key"] if cached else None,
                        ),
                    )
                    hits += bool(cached)
                db.execute("UPDATE renders SET cache_hits=? WHERE id=?", (hits, rid))
                self.advance(db, rid)
                return {"id": rid, "cache_hits": hits}
            raise APIError(404, "Not found")

    def advance(self, db, rid):
        rows = db.execute("SELECT * FROM jobs WHERE render=?", (rid,)).fetchall()
        if any(r["status"] == "failed" for r in rows):
            db.execute(
                "UPDATE renders SET status='failed',error='A render task failed after 3 attempts' WHERE id=?",
                (rid,),
            )
            return
        if any(r["kind"] == "assemble" for r in rows):
            return
        if rows and all(r["status"] == "done" for r in rows):
            keys = [r["result"] for r in sorted(rows, key=lambda r: r["position"])]
            db.execute(
                "INSERT INTO jobs(id,render,kind,position,payload,status) VALUES(?,?,?,?,?,?)",
                (
                    uid(),
                    rid,
                    "assemble",
                    len(keys),
                    canonical({"keys": keys}),
                    "queued",
                ),
            )

    def claim(self):
        with self.store.connect(immediate=True) as db:
            expired = db.execute(
                "SELECT id,render,attempts FROM jobs WHERE status='running' AND expires<?",
                (time.time(),),
            ).fetchall()
            for row in expired:
                db.execute(
                    "UPDATE jobs SET status=?,lease=NULL,error=? WHERE id=?",
                    (
                        "failed" if row["attempts"] >= 3 else "queued",
                        "Worker lease expired",
                        row["id"],
                    ),
                )
                self.advance(db, row["render"])
            row = db.execute(
                "SELECT j.* FROM jobs j JOIN renders r ON r.id=j.render WHERE j.status='queued' AND r.status!='failed' ORDER BY r.created,j.position LIMIT 1"
            ).fetchone()
            if not row:
                return None
            token = secrets.token_hex(24)
            db.execute(
                "UPDATE jobs SET status='running',attempts=attempts+1,lease=?,expires=? WHERE id=?",
                (token, time.time() + 120, row["id"]),
            )
            db.execute(
                "UPDATE renders SET status='rendering' WHERE id=?", (row["render"],)
            )
            job = dict(row)
            job["lease"] = token
            job["payload"] = json.loads(job["payload"])
            return job

    def heartbeat(self, jid, lease):
        with self.store.connect(immediate=True) as db:
            changed = db.execute(
                "UPDATE jobs SET expires=? WHERE id=? AND lease=? AND status='running'",
                (time.time() + 120, jid, lease),
            ).rowcount
            if not changed:
                raise APIError(409, "Lease no longer belongs to this worker")

    def finish(self, jid, lease, result=None, error=None):
        with self.store.connect(immediate=True) as db:
            job = db.execute(
                "SELECT * FROM jobs WHERE id=? AND lease=? AND status='running' AND expires>?",
                (jid, lease, time.time()),
            ).fetchone()
            if not job:
                raise APIError(409, "Stale worker result rejected")
            if error:
                status = "failed" if job["attempts"] >= 3 else "queued"
                db.execute(
                    "UPDATE jobs SET status=?,error=?,lease=NULL WHERE id=?",
                    (status, str(error)[:500], jid),
                )
            else:
                expected = (
                    ("segments/" + json.loads(job["payload"])["cache_key"] + ".mp4")
                    if job["kind"] == "segment"
                    else "exports/" + job["render"] + ".mp4"
                )
                if result != expected:
                    raise APIError(400, "Unexpected output object key")
                db.execute(
                    "UPDATE jobs SET status='done',result=?,error=NULL,lease=NULL WHERE id=?",
                    (result, jid),
                )
                if job["kind"] == "segment":
                    key = json.loads(job["payload"])["cache_key"]
                    db.execute("INSERT OR IGNORE INTO cache VALUES(?,?)", (key, result))
                else:
                    db.execute(
                        "UPDATE renders SET status='done',output=?,error=NULL WHERE id=?",
                        (result, job["render"]),
                    )
            self.advance(db, job["render"])


def text(data, key, limit):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise APIError(400, "Invalid " + key)
    return value.strip()


class Handler(BaseHTTPRequestHandler):
    server_version = "BranchFrame"

    @property
    def app(self):
        return self.server.app

    def headers_common(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; media-src 'self' blob:; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'",
        )
        self.send_header("Cache-Control", "no-store")

    def reply(self, status, value, extra=None):
        payload = canonical(value).encode()
        self.send_response(status)
        self.headers_common()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def user(self):
        jar = cookies.SimpleCookie()
        jar.load(self.headers.get("Cookie", ""))
        token = jar["bf_session"].value if "bf_session" in jar else ""
        with self.app.store.connect() as db:
            row = db.execute(
                "SELECT user FROM sessions WHERE token=? AND expires>?",
                (hashlib.sha256(token.encode()).hexdigest(), time.time()),
            ).fetchone()
        if not row:
            raise APIError(401, "Please sign in")
        return row["user"]

    def body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 <= length <= MAX_BODY:
            raise APIError(413, "Request too large")
        value = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(value, dict):
            raise APIError(400, "Expected a JSON object")
        return value

    def handle_request(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        if self.command == "POST":
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                raise APIError(403, "Cross-origin request rejected")
        if path == "/api/health":
            return self.reply(
                200,
                {
                    "ok": True,
                    "renderer": renderer_available(),
                    "storage": "s3" if self.app.store.bucket else "local",
                },
            )
        if path.startswith("/internal/"):
            if self.command != "POST":
                raise APIError(405, "Method not allowed")
            configured = os.environ.get("BRANCHFRAME_WORKER_TOKEN", "")
            if not configured or not hmac.compare_digest(
                self.headers.get("Authorization", ""), "Bearer " + configured
            ):
                raise APIError(401, "Invalid worker credentials")
            data = self.body()
            if path == "/internal/claim":
                return self.reply(200, self.app.claim())
            if path == "/internal/heartbeat":
                self.app.heartbeat(data["id"], data["lease"])
                return self.reply(200, {"ok": True})
            if path == "/internal/finish":
                self.app.finish(
                    data["id"], data["lease"], data.get("result"), data.get("error")
                )
                return self.reply(200, {"ok": True})
            raise APIError(404, "Not found")
        if path in ("/api/register", "/api/login") and self.command == "POST":
            data = self.body()
            email = text(data, "email", 254).lower()
            password = text(data, "password", 200)
            if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
                raise APIError(400, "Enter a valid email")
            if len(password) < 10:
                raise APIError(400, "Use a password with at least 10 characters")
            with self.app.store.connect(immediate=True) as db:
                row = db.execute(
                    "SELECT * FROM users WHERE email=?", (email,)
                ).fetchone()
                if path == "/api/register":
                    if row:
                        raise APIError(409, "An account with this email already exists")
                    user = uid()
                    db.execute(
                        "INSERT INTO users VALUES(?,?,?,?)",
                        (user, email, text(data, "name", 60), password_hash(password)),
                    )
                else:
                    if not row or not hmac.compare_digest(
                        password_hash(password, row["password"].split(":")[0]),
                        row["password"],
                    ):
                        raise APIError(401, "Email or password is incorrect")
                    user = row["id"]
                token = secrets.token_urlsafe(32)
                db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
                db.execute(
                    "INSERT INTO sessions VALUES(?,?,?)",
                    (
                        hashlib.sha256(token.encode()).hexdigest(),
                        user,
                        time.time() + 86400 * 7,
                    ),
                )
            secure = (
                "; Secure" if os.environ.get("BRANCHFRAME_SECURE_COOKIE") == "1" else ""
            )
            return self.reply(
                200,
                {"ok": True},
                {
                    "Set-Cookie": "bf_session="
                    + token
                    + "; HttpOnly; SameSite=Strict; Path=/; Max-Age=604800"
                    + secure
                },
            )
        if path.startswith("/api/"):
            user = self.user()
            if path == "/api/logout" and self.command == "POST":
                jar = cookies.SimpleCookie()
                jar.load(self.headers.get("Cookie", ""))
                token = jar["bf_session"].value
                with self.app.store.connect(immediate=True) as db:
                    db.execute(
                        "DELETE FROM sessions WHERE token=?",
                        (hashlib.sha256(token.encode()).hexdigest(),),
                    )
                return self.reply(
                    200,
                    {"ok": True},
                    {
                        "Set-Cookie": "bf_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
                    },
                )
            resumable = re.fullmatch(
                r"/api/projects/([a-f0-9]{32})/uploads(?:/(.*))?", path
            )
            if resumable:
                from .uploads import handle

                return handle(self, resumable[1], user, resumable[2] or "")
            upload = re.fullmatch(r"/api/projects/([a-f0-9]{32})/assets", path)
            if upload and self.command == "POST":
                return self.upload(upload[1], user)
            media = re.fullmatch(
                r"/api/projects/([a-f0-9]{32})/(assets|renders)/([a-f0-9]{32})/media",
                path,
            )
            if media and self.command == "GET":
                project, kind, item = media.groups()
                with self.app.store.connect() as db:
                    self.app.access(db, project, user)
                    if kind == "assets":
                        row = db.execute(
                            "SELECT id,name FROM assets WHERE id=? AND project=?",
                            (item, project),
                        ).fetchone()
                        key = "assets/" + project + "/" + item
                    else:
                        row = db.execute(
                            "SELECT output FROM renders WHERE id=? AND project=? AND status='done'",
                            (item, project),
                        ).fetchone()
                        key = row["output"] if row else ""
                    if not row:
                        raise APIError(404, "Media not found")
                return self.file(
                    self.app.store.get(key),
                    (mimetypes.guess_type(row["name"])[0] or "video/mp4")
                    if kind == "assets"
                    else "video/mp4",
                )
            return self.reply(
                200,
                self.app.api(
                    self.command,
                    path,
                    self.body() if self.command == "POST" else {},
                    user,
                    parse_qs(parsed.query),
                ),
            )
        if self.command != "GET":
            raise APIError(405, "Method not allowed")
        target = (
            (WEB / unquote(path).lstrip("/")).resolve()
            if path != "/"
            else WEB / "index.html"
        )
        if not target.is_relative_to(WEB):
            raise APIError(404, "Not found")
        return self.file(
            target, mimetypes.guess_type(target)[0] or "application/octet-stream"
        )

    def upload(self, project, user):
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 250 * 1024 * 1024:
            raise APIError(413, "Upload a video up to 250 MB")
        with self.app.store.connect() as db:
            self.app.access(db, project, user, write=True)
        name = unquote(self.headers.get("X-Filename", "video.mp4"))[:160]
        if Path(name).suffix.lower() not in (".mp4", ".mov", ".webm", ".m4v"):
            raise APIError(400, "Supported formats: MP4, MOV, WebM, M4V")
        aid = uid()
        sha = hashlib.sha256()
        with tempfile.NamedTemporaryFile(dir=self.app.store.root, delete=False) as f:
            temp = Path(f.name)
            try:
                remaining = length
                while remaining:
                    block = self.rfile.read(min(1024 * 1024, remaining))
                    if not block:
                        raise APIError(400, "Upload interrupted")
                    f.write(block)
                    sha.update(block)
                    remaining -= len(block)
                f.flush()
                self.app.store.put("assets/" + project + "/" + aid, temp)
            finally:
                temp.unlink(missing_ok=True)
        with self.app.store.connect(immediate=True) as db:
            db.execute(
                "INSERT INTO assets VALUES(?,?,?,?,?,?)",
                (aid, project, name, sha.hexdigest(), length, time.time()),
            )
        return self.reply(201, {"id": aid})

    def file(self, path, mime):
        if not path.is_file():
            raise APIError(404, "File not found")
        size = path.stat().st_size
        start = 0
        end = size - 1
        status = 200
        range_header = self.headers.get("Range")
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
            if not match or not any(match.groups()):
                raise APIError(416, "Unsupported byte range")
            if match[1]:
                start = int(match[1])
                end = min(int(match[2]), end) if match[2] else end
            else:
                start = max(0, size - int(match[2]))
            if start > end or start >= size:
                raise APIError(416, "Range not satisfiable")
            status = 206
        self.send_response(status)
        self.headers_common()
        self.send_header("Content-Type", mime)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with path.open("rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining:
                block = f.read(min(65536, remaining))
                if not block:
                    break
                self.wfile.write(block)
                remaining -= len(block)

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def dispatch(self):
        try:
            self.handle_request()
        except APIError as e:
            self.reply(e.status, {"error": e.message})
        except (ValueError, KeyError, TypeError) as e:
            self.reply(400, {"error": str(e)[:200]})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            import traceback

            traceback.print_exc()
            self.reply(500, {"error": "Unexpected server error; see server logs"})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data", default=os.environ.get("BRANCHFRAME_DATA", "data"))
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    app = App(args.data)
    if renderer_available():
        from .worker import local_loop

        for _ in range(args.workers):
            threading.Thread(target=local_loop, args=(app,), daemon=True).start()
    else:
        print(
            "FFmpeg is unavailable. Editing/versioning work; export jobs wait for a renderer.",
            flush=True,
        )
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.app = app
    print(f"BranchFrame: http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
