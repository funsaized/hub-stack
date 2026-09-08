"""Persistent invocation history served by the existing Ollama proxy."""

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


class ActivityStore:
    def __init__(self, path, retention_days=30):
        self.path = path
        self.retention_days = retention_days
        self.lock = threading.Lock()
        self.last_cleanup = 0
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS invocations (
                id TEXT PRIMARY KEY, started REAL NOT NULL, model TEXT, endpoint TEXT,
                source TEXT, purpose TEXT, state TEXT, status INTEGER, duration REAL,
                ttft REAL, prompt_tokens INTEGER, generated_tokens INTEGER,
                request TEXT, response TEXT, truncated INTEGER DEFAULT 0, error TEXT
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS invocation_time ON invocations(started DESC)")
            db.execute("UPDATE invocations SET state='interrupted', error='Proxy restarted before completion' WHERE state='running'")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def start(self, model, endpoint, source, purpose, request):
        identifier = uuid.uuid4().hex
        now = time.time()
        with self.lock, self.connect() as db:
            if now - self.last_cleanup > 3600:
                if self.retention_days > 0:
                    db.execute("DELETE FROM invocations WHERE started < ? AND state != 'running'", (now - self.retention_days * 86400,))
                self.last_cleanup = now
            db.execute("""INSERT INTO invocations
                (id, started, model, endpoint, source, purpose, state, request)
                VALUES (?, ?, ?, ?, ?, ?, 'running', ?)""",
                (identifier, now, model, endpoint, source, purpose, request))
        return identifier

    def finish(self, identifier, status, duration, ttft, final, response, truncated, error):
        final = final or {}
        state = 'error' if status >= 400 or error else 'completed'
        with self.connect() as db:
            db.execute("""UPDATE invocations SET state=?, status=?, duration=?, ttft=?,
                prompt_tokens=?, generated_tokens=?, response=?, truncated=?, error=? WHERE id=?""",
                (state, status, duration, ttft, final.get('prompt_eval_count'),
                 final.get('eval_count'), response, truncated, error, identifier))

    def detail(self, identifier):
        with self.connect() as db:
            row = db.execute("SELECT * FROM invocations WHERE id=?", (identifier,)).fetchone()
        return dict(row) if row else None

    def search(self, params):
        clauses, args = [], []
        for field in ('model', 'source', 'state'):
            if params.get(field, [''])[0]:
                clauses.append(f'{field} = ?')
                args.append(params[field][0])
        query = params.get('q', [''])[0]
        if query:
            clauses.append("(instr(lower(coalesce(request,'') || coalesce(response,'') || source || purpose), lower(?)) > 0)")
            args.append(query)
        since = float(params.get('since', ['0'])[0])
        clauses.append('started >= ?')
        args.append(since)
        where = ' WHERE ' + ' AND '.join(clauses)
        offset = max(0, int(params.get('offset', ['0'])[0]))
        with self.connect() as db:
            rows = db.execute("""SELECT id, started, model, endpoint, source, purpose, state,
                status, duration, ttft, prompt_tokens, generated_tokens, truncated,
                substr(request, 1, 200) AS preview FROM invocations""" + where +
                ' ORDER BY started DESC LIMIT 50 OFFSET ?', (*args, offset)).fetchall()
            stats = db.execute("""SELECT count(*) AS requests,
                coalesce(sum(state='running'),0) AS running,
                coalesce(sum(state IN ('error','interrupted')),0) AS errors,
                sum(prompt_tokens) AS prompt_tokens, sum(generated_tokens) AS generated_tokens,
                avg(duration) AS average_duration FROM invocations""" + where, args).fetchone()
            models = [r[0] for r in db.execute('SELECT DISTINCT model FROM invocations ORDER BY model')]
            sources = [r[0] for r in db.execute('SELECT DISTINCT source FROM invocations ORDER BY source')]
        return dict(rows=[dict(r) for r in rows], stats=dict(stats), models=models,
                    sources=sources, retention_days=self.retention_days)


def serve(handler, store):
    parsed = urlsplit(handler.path)
    if parsed.path not in ('/activity', '/activity/', '/activity/api/requests') and not parsed.path.startswith('/activity/api/requests/'):
        return False
    status = 200
    content_type = 'application/json; charset=utf-8'
    try:
        if parsed.path in ('/activity', '/activity/'):
            body = Path(__file__).with_name('activity.html').read_bytes()
            content_type = 'text/html; charset=utf-8'
        elif store is None:
            status, body = 503, json.dumps({'error': 'Activity storage unavailable; check proxy logs'}).encode()
        elif parsed.path == '/activity/api/requests':
            body = json.dumps(store.search(parse_qs(parsed.query))).encode()
        else:
            record = store.detail(parsed.path.rsplit('/', 1)[-1])
            status = 200 if record else 404
            body = json.dumps(record or {'error': 'Invocation not found'}).encode()
    except (ValueError, OverflowError):
        status, body = 400, b'{"error":"Invalid query parameter"}'
    except (OSError, sqlite3.Error):
        status, body = 503, b'{"error":"Activity storage unavailable; check proxy logs"}'
    handler.send_response(status)
    handler.send_header('Content-Type', content_type)
    handler.send_header('Content-Length', str(len(body)))
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('X-Content-Type-Options', 'nosniff')
    handler.end_headers()
    handler.wfile.write(body)
    return True
