import sqlite3
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "app.db"
UPLOAD_DIR = Path(__file__).resolve().parent.parent / "uploads"


def get_conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # lets us read columns by name
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS chats (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            title      TEXT NOT NULL DEFAULT 'New chat',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id    INTEGER NOT NULL,
            role       TEXT NOT NULL,          -- 'user' or 'assistant'
            content    TEXT NOT NULL,
            citations  TEXT,                   -- JSON string, filled in later
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS files (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,         -- original file name
            file_type   TEXT NOT NULL,         -- csv, pdf, docx, txt
            size_bytes  INTEGER NOT NULL,
            stored_path TEXT NOT NULL,
            uploaded_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS csv_tables (
            file_id    INTEGER PRIMARY KEY,
            table_name TEXT NOT NULL,
            row_count  INTEGER NOT NULL,
            FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS documents (
            file_id       INTEGER PRIMARY KEY,
            family        TEXT NOT NULL,
            doc_date      TEXT,              -- the date we use (YYYY-MM-DD)
            filename_date TEXT,
            body_date     TEXT,
            FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS chunks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id     INTEGER NOT NULL,
            location    TEXT NOT NULL,       -- 'Page 2' or a section heading
            chunk_index INTEGER NOT NULL,
            text        TEXT NOT NULL,
            FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
        );
        """
    )
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(chunks)").fetchall()]
    if "embedding" not in cols:
        conn.execute("ALTER TABLE chunks ADD COLUMN embedding BLOB")
    mcols = [r["name"] for r in conn.execute("PRAGMA table_info(messages)").fetchall()]
    if "charts" not in mcols:
        conn.execute("ALTER TABLE messages ADD COLUMN charts TEXT")
    if "web_search" not in mcols:
        conn.execute("ALTER TABLE messages ADD COLUMN web_search INTEGER")
    conn.commit()
    conn.close()
     