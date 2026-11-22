from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile, File
from pydantic import BaseModel

load_dotenv()  # load .env before the app modules run

from app.db import get_conn, init_db, UPLOAD_DIR
from app.llm import ask_llm
from app.tables import (
    make_table_name, load_csv, drop_table, describe_tables, run_query,
)
from app.docs import ingest_document

init_db()

app = FastAPI(title="VOLTKART CHATBOX")

HISTORY_LIMIT = 20  # how many recent messages we send to the LLM


class ChatRequest(BaseModel):
    content: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chats")
def create_chat():
    conn = get_conn()
    cur = conn.execute("INSERT INTO chats DEFAULT VALUES")
    conn.commit()
    chat_id = cur.lastrowid
    conn.close()
    return {"id": chat_id, "title": "New chat"}


@app.get("/chats")
def list_chats():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM chats ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/chats/{chat_id}/messages")
def get_messages(chat_id: int):
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, role, content, citations, created_at "
        "FROM messages WHERE chat_id = ? ORDER BY id",
        (chat_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/chats/{chat_id}/messages")
def send_message(chat_id: int, body: ChatRequest):
    conn = get_conn()

    chat = conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
    if chat is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Chat not found")

    # 1. save the user's message
    conn.execute(
        "INSERT INTO messages (chat_id, role, content) VALUES (?, 'user', ?)",
        (chat_id, body.content),
    )
    # name the chat after its first message
    if chat["title"] == "New chat":
        conn.execute(
            "UPDATE chats SET title = ? WHERE id = ?",
            (body.content[:40], chat_id),
        )
    conn.commit()

    # 2. load this chat's recent history (oldest first)
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE chat_id = ? "
        "ORDER BY id DESC LIMIT ?",
        (chat_id, HISTORY_LIMIT),
    ).fetchall()
    history = [dict(r) for r in reversed(rows)]

    # 3. ask the LLM
    try:
        answer = ask_llm(history)
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=502, detail=f"LLM error: {e}")

    # 4. save and return the answer
    conn.execute(
        "INSERT INTO messages (chat_id, role, content) VALUES (?, 'assistant', ?)",
        (chat_id, answer),
    )
    conn.commit()
    conn.close()
    return {"role": "assistant", "content": answer}

ALLOWED_TYPES = {
    ".csv": "csv",
    ".pdf": "pdf",
    ".docx": "docx",
    ".txt": "txt",
    ".md": "txt",  # treated as plain text
}


def remove_file(conn, file_id: int) -> bool:
    """Delete a file's record, its stored copy, and its DuckDB table (if a CSV)."""
    row = conn.execute(
        "SELECT stored_path FROM files WHERE id = ?", (file_id,)
    ).fetchone()
    if row is None:
        return False
    t = conn.execute(
        "SELECT table_name FROM csv_tables WHERE file_id = ?", (file_id,)
    ).fetchone()
    if t:
        drop_table(t["table_name"])
    Path(row["stored_path"]).unlink(missing_ok=True)
    conn.execute("DELETE FROM files WHERE id = ?", (file_id,))  # cascades to csv_tables
    conn.commit()
    return True


@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    safe_name = Path(file.filename).name
    ext = Path(safe_name).suffix.lower()
    if ext not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Use CSV, PDF, DOCX or TXT.",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="The file is empty.")

    file_type = ALLOWED_TYPES[ext]
    UPLOAD_DIR.mkdir(exist_ok=True)
    conn = get_conn()

    # re-uploading a CSV with the same name replaces the old one
    if file_type == "csv":
        tname = make_table_name(safe_name)
        for old in conn.execute(
            "SELECT file_id FROM csv_tables WHERE table_name = ?", (tname,)
        ).fetchall():
            remove_file(conn, old["file_id"])

    cur = conn.execute(
        "INSERT INTO files (name, file_type, size_bytes, stored_path) "
        "VALUES (?, ?, ?, '')",
        (safe_name, file_type, len(data)),
    )
    file_id = cur.lastrowid
    stored_path = UPLOAD_DIR / f"{file_id}_{safe_name}"
    stored_path.write_bytes(data)
    conn.execute(
        "UPDATE files SET stored_path = ? WHERE id = ?", (str(stored_path), file_id)
    )
    conn.commit()

    result = {
        "id": file_id,
        "name": safe_name,
        "file_type": file_type,
        "size_bytes": len(data),
    }

    if file_type == "csv":
        try:
            info = load_csv(stored_path, safe_name)
        except Exception as e:
            remove_file(conn, file_id)
            conn.close()
            raise HTTPException(status_code=400, detail=f"Could not read CSV: {e}")
        conn.execute(
            "INSERT INTO csv_tables (file_id, table_name, row_count) VALUES (?, ?, ?)",
            (file_id, info["table_name"], info["rows"]),
        )
        conn.commit()
        result.update(
            table_name=info["table_name"], rows=info["rows"], columns=info["columns"]
        )
    if file_type in ("pdf", "docx", "txt"):
        try:
            info = ingest_document(conn, file_id, stored_path, file_type, safe_name)
            conn.commit()
        except Exception as e:
            remove_file(conn, file_id)
            conn.close()
            raise HTTPException(status_code=400, detail=f"Could not read document: {e}")
        result.update(info)
    conn.close()
    return result

@app.get("/files")
def list_files():
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, name, file_type, size_bytes, uploaded_at "
        "FROM files ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.delete("/files/{file_id}")
def delete_file(file_id: int):
    conn = get_conn()
    found = remove_file(conn, file_id)
    conn.close()
    if not found:
        raise HTTPException(status_code=404, detail="File not found")
    return {"deleted": file_id}

class SqlRequest(BaseModel):
    sql: str


@app.get("/tables")
def get_tables():
    return describe_tables()


@app.post("/sql")
def sql_test(body: SqlRequest):
    """Dev/test route: run a read-only SELECT against the uploaded tables."""
    try:
        return run_query(body.sql)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/documents")
def list_documents():
    """Documents grouped by family, oldest to newest."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT f.id, f.name, d.family, d.doc_date, d.filename_date, d.body_date, "
        "(SELECT COUNT(*) FROM chunks c WHERE c.file_id = f.id) AS chunks "
        "FROM documents d JOIN files f ON f.id = d.file_id "
        "ORDER BY d.family, d.doc_date"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/documents/{file_id}/chunks")
def document_chunks(file_id: int):
    conn = get_conn()
    rows = conn.execute(
        "SELECT location, chunk_index, text FROM chunks "
        "WHERE file_id = ? ORDER BY id",
        (file_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]