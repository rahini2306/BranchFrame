"""Single-use account recovery tokens and configurable SMTP/SES delivery."""

import hashlib
import json
import os
import secrets
import smtplib
import ssl
import time
from email.message import EmailMessage
from pathlib import Path


class Mailer:
    def __init__(self, root):
        self.root = Path(root)

    @property
    def configured(self):
        return bool(
            os.environ.get("BRANCHFRAME_DEV_MAILBOX") == "1"
            or (
                os.environ.get("BRANCHFRAME_MAIL_FROM")
                and os.environ.get("BRANCHFRAME_PUBLIC_URL")
                and (
                    os.environ.get("SMTP_HOST")
                    or os.environ.get("BRANCHFRAME_MAIL_PROVIDER") == "ses"
                )
            )
        )

    def send(self, to, subject, body):
        if os.environ.get("BRANCHFRAME_DEV_MAILBOX") == "1":
            directory = self.root / "outbox"
            directory.mkdir(exist_ok=True, mode=0o700)
            path = directory / (secrets.token_hex(12) + ".json")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump({"to": to, "subject": subject, "body": body}, f)
            return
        sender = os.environ["BRANCHFRAME_MAIL_FROM"]
        if os.environ.get("BRANCHFRAME_MAIL_PROVIDER") == "ses":
            import boto3

            boto3.client("sesv2").send_email(
                FromEmailAddress=sender,
                Destination={"ToAddresses": [to]},
                Content={
                    "Simple": {
                        "Subject": {"Data": subject},
                        "Body": {"Text": {"Data": body}},
                    }
                },
            )
            return
        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(
            os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587")), timeout=20
        ) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            if os.environ.get("SMTP_USER"):
                smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
            smtp.send_message(msg)


class RateLimit:
    def __init__(self):
        import threading

        self.lock = threading.Lock()
        self.buckets = {}

    def check(self, key, limit=12, window=900):
        from .server import APIError

        now = time.time()
        with self.lock:
            if len(self.buckets) > 10000:
                self.buckets = {
                    k: [x for x in v if now - x < window]
                    for k, v in self.buckets.items()
                    if v and now - v[-1] < window
                }
            items = [x for x in self.buckets.get(key, []) if now - x < window]
            if len(items) >= limit:
                raise APIError(429, "Too many attempts. Please try again later.")
            self.buckets[key] = items + [now]


def issue(app, user, kind):
    if not app.mailer.configured:
        return False
    with app.store.connect(immediate=True) as db:
        row = db.execute("SELECT email FROM users WHERE id=?", (user,)).fetchone()
        if not row:
            return False
        recent = db.execute(
            "SELECT 1 FROM account_tokens WHERE user=? AND kind=? AND created>?",
            (user, kind, time.time() - 60),
        ).fetchone()
        if recent:
            return True
        token = secrets.token_urlsafe(32)
        ttl = 1800 if kind == "reset" else 86400
        db.execute(
            "UPDATE account_tokens SET used=1 WHERE user=? AND kind=?", (user, kind)
        )
        db.execute(
            "INSERT INTO account_tokens VALUES(?,?,?,?,0,?)",
            (
                hashlib.sha256(token.encode()).hexdigest(),
                user,
                kind,
                time.time() + ttl,
                time.time(),
            ),
        )
    base = os.environ.get("BRANCHFRAME_PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/")
    link = base + "/account.html#mode=" + kind + "&token=" + token
    subject = (
        "Reset your BranchFrame password"
        if kind == "reset"
        else "Verify your BranchFrame email"
    )
    try:
        _deliver(app, row["email"], subject, link, kind)
    except Exception:
        with app.store.connect(immediate=True) as db:
            db.execute(
                "DELETE FROM account_tokens WHERE hash=?",
                (hashlib.sha256(token.encode()).hexdigest(),),
            )
        from .server import APIError

        raise APIError(
            503, "Email delivery failed. Please try again shortly."
        ) from None
    return True


def _deliver(app, email, subject, link, kind):
    app.mailer.send(
        email,
        subject,
        subject
        + "\n\n"
        + link
        + "\n\nThis link is single-use and expires "
        + ("in 30 minutes." if kind == "reset" else "in 24 hours.")
        + "\nIf you did not request this, ignore this email.",
    )
    return True


def handle(handler, path):
    from .server import APIError, password_hash

    app = handler.app
    if handler.command != "POST":
        raise APIError(405, "Method not allowed")
    data = handler.body()
    app.rate_limit.check(("account", handler.client_ip()))
    if path == "/api/request-verification":
        user = handler.user()
        if not app.mailer.configured:
            raise APIError(
                503, "Email delivery is not configured for this installation."
            )
        issue(app, user, "verify")
        return handler.reply(
            200, {"ok": True, "message": "Check your email for a verification link."}
        )
    if path == "/api/request-password-reset":
        if not app.mailer.configured:
            raise APIError(
                503, "Email recovery is currently unavailable. Contact the site owner."
            )
        email = data.get("email", "")
        if not isinstance(email, str) or len(email) > 254:
            raise APIError(400, "Enter your email address")
        with app.store.connect() as db:
            row = db.execute(
                "SELECT id FROM users WHERE email=?", (email.strip().lower(),)
            ).fetchone()
        if row:
            issue(app, row["id"], "reset")
        return handler.reply(
            200,
            {
                "ok": True,
                "message": "If an account matches, a reset link will be sent.",
            },
        )
    kind = "reset" if path == "/api/reset-password" else "verify"
    token = data.get("token", "")
    if not isinstance(token, str) or not 20 <= len(token) <= 200:
        raise APIError(400, "Invalid or expired link")
    password = data.get("password", "")
    if kind == "reset" and (
        not isinstance(password, str) or not 10 <= len(password) <= 200
    ):
        raise APIError(400, "Use a password with 10–200 characters")
    with app.store.connect(immediate=True) as db:
        row = db.execute(
            "SELECT * FROM account_tokens WHERE hash=? AND kind=? AND used=0 AND expires>?",
            (hashlib.sha256(token.encode()).hexdigest(), kind, time.time()),
        ).fetchone()
        if not row:
            raise APIError(400, "Invalid or expired link")
        db.execute("UPDATE account_tokens SET used=1 WHERE hash=?", (row["hash"],))
        if kind == "verify":
            db.execute(
                "INSERT OR REPLACE INTO verified_emails VALUES(?,?)",
                (row["user"], time.time()),
            )
        else:
            db.execute(
                "UPDATE users SET password=? WHERE id=?",
                (password_hash(password), row["user"]),
            )
            db.execute("DELETE FROM sessions WHERE user=?", (row["user"],))
            db.execute(
                "UPDATE account_tokens SET used=1 WHERE user=? AND kind='reset'",
                (row["user"],),
            )
    return handler.reply(
        200,
        {
            "ok": True,
            "message": "Email verified."
            if kind == "verify"
            else "Password updated. Sign in again.",
        },
    )
