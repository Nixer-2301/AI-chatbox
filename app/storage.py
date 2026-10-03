"""SQLite 持久层：会话、消息、请求记录。

界面线程与请求线程都会写入，因此统一用一把可重入锁串行化。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from .models import ChatMessage, RequestRecord, Session, now_ts

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    profile_id  TEXT DEFAULT '',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    params      TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT DEFAULT '',
    reasoning   TEXT DEFAULT '',
    created_at  REAL NOT NULL,
    meta        TEXT DEFAULT '{}',
    tokens      INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, seq);

CREATE TABLE IF NOT EXISTS requests (
    id              TEXT PRIMARY KEY,
    session_id      TEXT DEFAULT '',
    profile_id      TEXT DEFAULT '',
    profile_name    TEXT DEFAULT '',
    model           TEXT DEFAULT '',
    protocol        TEXT DEFAULT '',
    created_at      REAL NOT NULL,
    method          TEXT DEFAULT 'POST',
    url             TEXT DEFAULT '',
    request_headers TEXT DEFAULT '{}',
    request_body    TEXT DEFAULT '',
    status          INTEGER,
    response_headers TEXT DEFAULT '{}',
    response_body   TEXT DEFAULT '',
    raw_stream      TEXT DEFAULT '',
    content         TEXT DEFAULT '',
    reasoning       TEXT DEFAULT '',
    usage           TEXT DEFAULT '{}',
    finish_reason   TEXT DEFAULT '',
    latency_ms      INTEGER DEFAULT 0,
    ttft_ms         INTEGER DEFAULT 0,
    streamed        INTEGER DEFAULT 0,
    error           TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_requests_session ON requests(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_requests_created ON requests(created_at DESC);
"""


def _dumps(obj) -> str:
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    try:
        return json.dumps(obj, ensure_ascii=False)
    except (TypeError, ValueError):
        return json.dumps(str(obj), ensure_ascii=False)


def _loads(text: str, fallback):
    if not text:
        return fallback
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return fallback


class Storage:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    # -------------------------------------------------------------- 会话 --
    def create_session(self, title: str = "新会话", profile_id: str = "",
                       params: Optional[dict] = None) -> Session:
        session = Session(title=title, profile_id=profile_id, params=params or {})
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions(id,title,profile_id,created_at,updated_at,params)"
                " VALUES(?,?,?,?,?,?)",
                (session.id, session.title, session.profile_id, session.created_at,
                 session.updated_at, _dumps(session.params)),
            )
            self._conn.commit()
        return session

    def list_sessions(self) -> list[Session]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
        return [self._row_to_session(r) for r in rows]

    def get_session(self, session_id: str) -> Optional[Session]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
        return self._row_to_session(row) if row else None

    def update_session(self, session: Session) -> None:
        session.updated_at = now_ts()
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET title=?,profile_id=?,updated_at=?,params=? WHERE id=?",
                (session.title, session.profile_id, session.updated_at,
                 _dumps(session.params), session.id),
            )
            self._conn.commit()

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            self._conn.execute("DELETE FROM requests WHERE session_id=?", (session_id,))
            self._conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            self._conn.commit()

    @staticmethod
    def _row_to_session(row) -> Session:
        return Session(
            id=row["id"], title=row["title"], profile_id=row["profile_id"],
            created_at=row["created_at"], updated_at=row["updated_at"],
            params=_loads(row["params"], {}),
        )

    # -------------------------------------------------------------- 消息 --
    def add_message(self, message: ChatMessage) -> ChatMessage:
        with self._lock:
            if not message.seq:
                row = self._conn.execute(
                    "SELECT COALESCE(MAX(seq),0) AS m FROM messages WHERE session_id=?",
                    (message.session_id,),
                ).fetchone()
                message.seq = int(row["m"]) + 1
            self._conn.execute(
                "INSERT INTO messages(id,session_id,seq,role,content,reasoning,"
                "created_at,meta,tokens) VALUES(?,?,?,?,?,?,?,?,?)",
                (message.id, message.session_id, message.seq, message.role,
                 message.content, message.reasoning, message.created_at,
                 _dumps(message.meta), message.tokens),
            )
            self._conn.execute(
                "UPDATE sessions SET updated_at=? WHERE id=?",
                (now_ts(), message.session_id),
            )
            self._conn.commit()
        return message

    def update_message(self, message: ChatMessage) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE messages SET content=?,reasoning=?,meta=?,tokens=? WHERE id=?",
                (message.content, message.reasoning, _dumps(message.meta),
                 message.tokens, message.id),
            )
            self._conn.commit()

    def get_messages(self, session_id: str) -> list[ChatMessage]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE session_id=? ORDER BY seq ASC",
                (session_id,),
            ).fetchall()
        return [
            ChatMessage(
                id=r["id"], session_id=r["session_id"], seq=r["seq"], role=r["role"],
                content=r["content"], reasoning=r["reasoning"],
                created_at=r["created_at"], meta=_loads(r["meta"], {}),
                tokens=r["tokens"],
            )
            for r in rows
        ]

    def delete_message(self, message_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM messages WHERE id=?", (message_id,))
            self._conn.commit()

    def delete_messages_from(self, session_id: str, seq: int) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM messages WHERE session_id=? AND seq>=?", (session_id, seq)
            )
            self._conn.commit()

    # ---------------------------------------------------------- 请求记录 --
    REQUEST_COLUMNS = (
        "id", "session_id", "profile_id", "profile_name", "model", "protocol",
        "created_at", "method", "url", "request_headers", "request_body", "status",
        "response_headers", "response_body", "raw_stream", "content", "reasoning",
        "usage", "finish_reason", "latency_ms", "ttft_ms", "streamed", "error",
    )

    def add_request(self, record: RequestRecord) -> RequestRecord:
        values = (
            record.id, record.session_id, record.profile_id, record.profile_name,
            record.model, record.protocol, record.created_at, record.method,
            record.url, _dumps(record.request_headers), _dumps(record.request_body),
            record.status, _dumps(record.response_headers),
            _dumps(record.response_body), record.raw_stream, record.content,
            record.reasoning, _dumps(record.usage), record.finish_reason,
            record.latency_ms, record.ttft_ms, 1 if record.streamed else 0,
            record.error,
        )
        columns = ",".join(self.REQUEST_COLUMNS)
        holders = ",".join("?" * len(self.REQUEST_COLUMNS))
        with self._lock:
            self._conn.execute(
                f"INSERT OR REPLACE INTO requests({columns}) VALUES({holders})", values
            )
            self._conn.commit()
        return record

    def list_requests(self, session_id: Optional[str] = None,
                      limit: int = 500) -> list[RequestRecord]:
        with self._lock:
            if session_id:
                rows = self._conn.execute(
                    "SELECT * FROM requests WHERE session_id=?"
                    " ORDER BY created_at DESC LIMIT ?", (session_id, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM requests ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
        return [self._row_to_request(r) for r in rows]

    def get_request(self, request_id: str) -> Optional[RequestRecord]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM requests WHERE id=?", (request_id,)
            ).fetchone()
        return self._row_to_request(row) if row else None

    def delete_request(self, request_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM requests WHERE id=?", (request_id,))
            self._conn.commit()

    def clear_requests(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM requests")
            self._conn.commit()

    @staticmethod
    def _row_to_request(row) -> RequestRecord:
        return RequestRecord(
            id=row["id"], session_id=row["session_id"], profile_id=row["profile_id"],
            profile_name=row["profile_name"], model=row["model"],
            protocol=row["protocol"], created_at=row["created_at"],
            method=row["method"], url=row["url"],
            request_headers=_loads(row["request_headers"], {}),
            request_body=_loads(row["request_body"], None),
            status=row["status"],
            response_headers=_loads(row["response_headers"], {}),
            response_body=_loads(row["response_body"], None),
            raw_stream=row["raw_stream"], content=row["content"],
            reasoning=row["reasoning"], usage=_loads(row["usage"], {}),
            finish_reason=row["finish_reason"], latency_ms=row["latency_ms"],
            ttft_ms=row["ttft_ms"], streamed=bool(row["streamed"]),
            error=row["error"],
        )