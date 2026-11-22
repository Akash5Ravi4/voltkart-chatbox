import numpy as np
from sentence_transformers import SentenceTransformer

MODEL_NAME = "all-MiniLM-L6-v2"
_model = None


def get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def embed_texts(texts: list[str]) -> np.ndarray:
    vecs = get_model().encode(
        texts, normalize_embeddings=True, show_progress_bar=False
    )
    return np.asarray(vecs, dtype=np.float32)


def embed_missing(conn) -> int:
    """Embed every chunk that doesn't have a vector yet."""
    rows = conn.execute(
        "SELECT id, location, text FROM chunks WHERE embedding IS NULL"
    ).fetchall()
    if not rows:
        return 0
    # the heading/page is included so a chunk keeps its context
    vecs = embed_texts([f"{r['location']}: {r['text']}" for r in rows])
    for r, v in zip(rows, vecs):
        conn.execute(
            "UPDATE chunks SET embedding = ? WHERE id = ?", (v.tobytes(), r["id"])
        )
    conn.commit()
    return len(rows)


def _date_num(d: str | None) -> int:
    return int(d.replace("-", "")) if d else 0


def search_documents(conn, query: str, top_k: int = 6) -> list[dict]:
    embed_missing(conn)  # backfills chunks from before embeddings existed

    rows = conn.execute(
        "SELECT c.id, c.file_id, c.location, c.text, c.embedding, "
        "       f.name AS file_name, d.family, d.doc_date "
        "FROM chunks c "
        "JOIN files f ON f.id = c.file_id "
        "JOIN documents d ON d.file_id = c.file_id "
        "WHERE c.embedding IS NOT NULL"
    ).fetchall()
    if not rows:
        return []

    mat = np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
    q = embed_texts([query])[0]
    sims = mat @ q  # cosine similarity (vectors are normalized)

    # 1. the best matches overall
    chosen = set(int(i) for i in np.argsort(-sims)[:top_k])

    # 2. for every family that was hit, add the best chunk of each other version
    families = {rows[i]["family"] for i in chosen}
    best_per_file: dict[int, int] = {}
    for i, r in enumerate(rows):
        if r["family"] in families:
            cur = best_per_file.get(r["file_id"])
            if cur is None or sims[i] > sims[cur]:
                best_per_file[r["file_id"]] = i
    chosen.update(best_per_file.values())

    # 3. which version is the newest in each family?
    latest: dict[str, int] = {}
    for r in rows:
        latest[r["family"]] = max(latest.get(r["family"], 0), _date_num(r["doc_date"]))

    results = []
    for i in chosen:
        r = rows[i]
        is_latest = _date_num(r["doc_date"]) == latest[r["family"]]
        results.append(
            {
                "chunk_id": r["id"],
                "file_id": r["file_id"],
                "file_name": r["file_name"],
                "family": r["family"],
                "doc_date": r["doc_date"],
                "location": r["location"],
                "status": "latest" if is_latest else "superseded",
                "score": round(float(sims[i]), 3),
                "text": r["text"],
            }
        )

    # 4. families ordered by their best score; newest version first inside each
    family_best: dict[str, float] = {}
    for x in results:
        family_best[x["family"]] = max(family_best.get(x["family"], -1), x["score"])
    family_rank = {
        f: n for n, f in enumerate(sorted(family_best, key=lambda f: -family_best[f]))
    }
    results.sort(
        key=lambda x: (family_rank[x["family"]], -_date_num(x["doc_date"]), -x["score"])
    )
    return results