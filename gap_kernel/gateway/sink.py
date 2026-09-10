"""Harmless credential-protected tool: append a note to a durable local outbox.

No email is sent. Idempotency is enforced at the side-effect owner, including
when the gateway loses a response after the write committed.
"""
import hashlib
import hmac
import json
import sqlite3
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import Field

from gap_kernel.gateway.app import BodyLimit
from gap_kernel.gateway.models import StrictModel


class NoteRequest(StrictModel):
    idempotency_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    target: str = Field(pattern=r"^demo$")
    message: str = Field(min_length=1, max_length=2000)


def create_sink_app(token_file: str, db_path: str):
    token = Path(token_file).read_text().strip()
    if len(token) < 32:
        raise ValueError("tool token too short")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
    db.execute("CREATE TABLE IF NOT EXISTS notes (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
    lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(app):
        yield
        db.close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.add_middleware(BodyLimit)

    def authenticated(authorization: str = Header(default="")):
        if not hmac.compare_digest(authorization, "Bearer " + token):
            raise HTTPException(401, "tool_authentication_required")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/records/{target}", dependencies=[Depends(authenticated)])
    def lookup(target: str):
        if target != "demo":
            raise HTTPException(404, "unknown_target")
        with lock:
            count = db.execute("SELECT count(*) FROM notes").fetchone()[0]
        return {"target": "demo", "name": "Local demonstration recipient", "notes": count}

    @app.post("/notify", dependencies=[Depends(authenticated)])
    def notify(request: NoteRequest):
        payload = json.dumps({"target": request.target, "message": request.message}, sort_keys=True)
        with lock:
            db.execute("BEGIN IMMEDIATE")
            try:
                old = db.execute("SELECT payload FROM notes WHERE id=?",
                                 (request.idempotency_key,)).fetchone()
                if old and old[0] != payload:
                    raise HTTPException(409, "idempotency_content_mismatch")
                db.execute("INSERT OR IGNORE INTO notes VALUES (?,?)", (request.idempotency_key, payload))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        return {"receipt": request.idempotency_key, "status": "recorded",
                "content_sha256": hashlib.sha256(payload.encode()).hexdigest()}

    return app


def main():
    import argparse
    import uvicorn
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8091)
    args = parser.parse_args()
    uvicorn.run(create_sink_app(args.token_file, args.db), host=args.host,
                port=args.port, access_log=False)


if __name__ == "__main__":
    main()
