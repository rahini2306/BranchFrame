"""SQLite WAL metadata and optional S3 object storage. No credentials in source."""

import contextlib
import os
from pathlib import Path
import sqlite3
import tempfile
import shutil

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,name TEXT NOT NULL,password TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user TEXT NOT NULL,expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,owner TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS members(project TEXT NOT NULL,user TEXT NOT NULL,role TEXT NOT NULL,PRIMARY KEY(project,user));
CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY,project TEXT NOT NULL,name TEXT NOT NULL,sha TEXT NOT NULL,size INTEGER NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS commits(id TEXT PRIMARY KEY,project TEXT NOT NULL,parents TEXT NOT NULL,timeline TEXT NOT NULL,message TEXT NOT NULL,author TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS branches(project TEXT NOT NULL,name TEXT NOT NULL,head TEXT NOT NULL,PRIMARY KEY(project,name));
CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY,project TEXT NOT NULL,commit_id TEXT NOT NULL,author TEXT NOT NULL,kind TEXT NOT NULL,body TEXT NOT NULL,at REAL NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS renders(id TEXT PRIMARY KEY,project TEXT NOT NULL,commit_id TEXT NOT NULL,status TEXT NOT NULL,created REAL NOT NULL,output TEXT,error TEXT,cache_hits INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,render TEXT NOT NULL,kind TEXT NOT NULL,position INTEGER NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,lease TEXT,expires REAL NOT NULL DEFAULT 0,result TEXT,error TEXT);
CREATE TABLE IF NOT EXISTS uploads(id TEXT PRIMARY KEY,project TEXT NOT NULL,owner TEXT NOT NULL,name TEXT NOT NULL,size INTEGER NOT NULL,fingerprint TEXT NOT NULL,created REAL NOT NULL,asset TEXT);
CREATE TABLE IF NOT EXISTS upload_parts(upload TEXT NOT NULL,position INTEGER NOT NULL,sha TEXT NOT NULL,PRIMARY KEY(upload,position));
CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY,object_key TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status,expires);
"""


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.objects = self.root / "objects"
        self.objects.mkdir(exist_ok=True)
        self.db = self.root / "metadata.sqlite3"
        with self.connect() as db:
            db.executescript(SCHEMA)
        self.bucket = os.environ.get("BRANCHFRAME_BUCKET")
        self.s3 = None
        if self.bucket:
            import boto3

            self.s3 = boto3.client("s3")

    @contextlib.contextmanager
    def connect(self, immediate=False):
        db = sqlite3.connect(self.db, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            db.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def path(self, key):
        path = (self.objects / key).resolve()
        if not path.is_relative_to(self.objects):
            raise ValueError("Invalid object key")
        return path

    def put(self, key, source):
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if Path(source).resolve() != target:
            with tempfile.NamedTemporaryFile(
                dir=target.parent, delete=False
            ) as staging:
                temporary = Path(staging.name)
            try:
                shutil.copyfile(source, temporary)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        if self.s3:
            self.s3.upload_file(str(target), self.bucket, key)
        return key

    def get(self, key):
        target = self.path(key)
        if not target.exists() and self.s3:
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=target.parent, delete=False
            ) as staging:
                temporary = Path(staging.name)
            try:
                self.s3.download_file(self.bucket, key, str(temporary))
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        return target
