from fastapi import FastAPI
from dotenv import load_dotenv

from app.db import get_conn, init_db

load_dotenv()
init_db()  # creates the tables on startup if they don't exist

app = FastAPI(title="VOLTKART CHATBOX")


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