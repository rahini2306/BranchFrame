"""Revisioned shared drafts, independent approval gates, and SSE notifications."""

import json
import time

from .domain import canonical, merge


def approval(db, project, cid):
    setting = db.execute(
        "SELECT require_approval FROM project_settings WHERE project=?", (project,)
    ).fetchone()
    required = bool(setting and setting[0])
    commit = db.execute(
        "SELECT author FROM commits WHERE id=? AND project=?", (cid, project)
    ).fetchone()
    latest = {}
    for r in db.execute(
        "SELECT * FROM reviews WHERE project=? AND commit_id=? AND kind IN ('approve','request_changes') ORDER BY created,id",
        (project, cid),
    ):
        if commit and r["author"] != commit["author"]:
            latest[r["author"]] = r["kind"]
    approvals = sum(v == "approve" for v in latest.values())
    blocking = sum(v == "request_changes" for v in latest.values())
    return {
        "required": required,
        "approvals": approvals,
        "blocking": blocking,
        "allowed": not required or (approvals > 0 and blocking == 0),
    }


def draft(db, project, branch, user):
    from .server import APIError

    head = db.execute(
        "SELECT head FROM branches WHERE project=? AND name=?", (project, branch)
    ).fetchone()
    if not head:
        raise APIError(404, "Branch not found")
    row = db.execute(
        "SELECT * FROM drafts WHERE project=? AND branch=?", (project, branch)
    ).fetchone()
    if not row:
        timeline = db.execute(
            "SELECT timeline FROM commits WHERE id=?", (head[0],)
        ).fetchone()[0]
        db.execute(
            "INSERT INTO drafts VALUES(?,?,0,?,?,?,?)",
            (project, branch, head[0], timeline, user, time.time()),
        )
        db.execute(
            "INSERT INTO draft_history VALUES(?,?,0,?)", (project, branch, timeline)
        )
        row = db.execute(
            "SELECT * FROM drafts WHERE project=? AND branch=?", (project, branch)
        ).fetchone()
    value = dict(row)
    value["timeline"] = json.loads(value["timeline"])
    return value


def replace_draft(db, project, branch, head, timeline, user):
    row = db.execute(
        "SELECT revision FROM drafts WHERE project=? AND branch=?", (project, branch)
    ).fetchone()
    revision = (row[0] + 1) if row else 0
    data = canonical(timeline)
    db.execute(
        "INSERT INTO drafts VALUES(?,?,?,?,?,?,?) ON CONFLICT(project,branch) DO UPDATE SET revision=excluded.revision,head=excluded.head,timeline=excluded.timeline,author=excluded.author,updated=excluded.updated",
        (project, branch, revision, head, data, user, time.time()),
    )
    db.execute(
        "INSERT OR REPLACE INTO draft_history VALUES(?,?,?,?)",
        (project, branch, revision, data),
    )
    db.execute(
        "DELETE FROM draft_history WHERE project=? AND branch=? AND revision<?",
        (project, branch, revision - 100),
    )


def action(app, db, project, user, method, name, data, query):
    from .server import APIError, text

    if name == "settings":
        if method != "POST":
            raise APIError(405, "Method not allowed")
        app.access(db, project, user, owner=True)
        required = data.get("require_approval")
        if not isinstance(required, bool):
            raise APIError(400, "require_approval must be boolean")
        db.execute(
            "INSERT OR REPLACE INTO project_settings VALUES(?,?)",
            (project, int(required)),
        )
        return {"ok": True}
    branch = (
        text(data, "branch", 40)
        if method == "POST"
        else query.get("branch", ["main"])[0]
    )
    current = draft(db, project, branch, user)
    if method == "POST":
        app.access(db, project, user, write=True)
        revision = data.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int):
            raise APIError(400, "Invalid draft revision")
        base = db.execute(
            "SELECT timeline FROM draft_history WHERE project=? AND branch=? AND revision=?",
            (project, branch, revision),
        ).fetchone()
        if not base:
            raise APIError(
                409,
                "Draft history expired. Reload the shared draft; your work can be saved to another branch.",
            )
        incoming = app.timeline(db, project, data.get("timeline"))
        result = merge(json.loads(base[0]), current["timeline"], incoming)
        if result["conflicts"]:
            raise APIError(
                409,
                "Conflicting live edits. Reload the shared draft or save your work to a new branch.",
            )
        timeline = app.timeline(db, project, result["timeline"])
        if timeline != current["timeline"]:
            replace_draft(db, project, branch, current["head"], timeline, user)
        current = draft(db, project, branch, user)
    db.execute(
        "INSERT INTO presence VALUES(?,?,?) ON CONFLICT(project,user) DO UPDATE SET seen=excluded.seen",
        (project, user, time.time()),
    )
    current["presence"] = [
        dict(r)
        for r in db.execute(
            "SELECT u.id,u.name FROM presence p JOIN users u ON p.user=u.id WHERE p.project=? AND p.seen>?",
            (project, time.time() - 35),
        )
    ]
    return current


def events(handler, project, branch):
    from .server import APIError

    user = handler.user()
    with handler.app.store.connect() as db:
        handler.app.access(db, project, user)
    handler.send_response(200)
    handler.headers_common()
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Connection", "keep-alive")
    handler.send_header("X-Accel-Buffering", "no")
    handler.end_headers()
    previous = None
    deadline = time.time() + 300
    try:
        while time.time() < deadline:
            if handler.user() != user:
                return
            with handler.app.store.connect(immediate=True) as db:
                handler.app.access(db, project, user)
                db.execute(
                    "INSERT INTO presence VALUES(?,?,?) ON CONFLICT(project,user) DO UPDATE SET seen=excluded.seen",
                    (project, user, time.time()),
                )
                row = db.execute(
                    "SELECT revision,head,author FROM drafts WHERE project=? AND branch=?",
                    (project, branch),
                ).fetchone()
                head = db.execute(
                    "SELECT head FROM branches WHERE project=? AND name=?",
                    (project, branch),
                ).fetchone()
                online = [
                    dict(r)
                    for r in db.execute(
                        "SELECT u.id,u.name FROM presence p JOIN users u ON p.user=u.id WHERE p.project=? AND p.seen>?",
                        (project, time.time() - 35),
                    )
                ]
                gate = approval(db, project, head[0]) if head else None
            payload = {
                "revision": row["revision"] if row else -1,
                "head": head[0] if head else None,
                "presence": online,
                "approval": gate,
            }
            data = canonical(payload)
            if data != previous:
                handler.wfile.write(("event: draft\ndata: " + data + "\n\n").encode())
                previous = data
            else:
                handler.wfile.write(b": heartbeat\n\n")
            handler.wfile.flush()
            time.sleep(2)
    except (BrokenPipeError, ConnectionResetError, APIError, OSError):
        return
    finally:
        handler.close_connection = True
