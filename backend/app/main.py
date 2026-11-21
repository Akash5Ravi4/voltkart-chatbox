from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()  # must run before the app reads GROQ_API_KEY

from app.db import get_conn, init_db
from app.llm import ask_llm

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