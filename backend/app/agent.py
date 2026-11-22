import json
import re

from app.llm import get_client, MODEL
from app.search import search_documents
from app.tables import describe_tables, run_query
from app.web import search_web

MAX_STEPS = 6            # max tool-calling rounds per question
MAX_ROWS_TO_LLM = 100    # rows the LLM sees from one query
MAX_ROWS_IN_CITATION = 25  # rows saved in the citation preview

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "query_tables",
            "description": (
                "Run a read-only DuckDB SQL SELECT on the uploaded CSV tables. "
                "Use it for every number, count, total, ranking or lookup that "
                "lives in the tables. Joins across tables are allowed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "One SELECT (or WITH ... SELECT) statement.",
                    }
                },
                "required": ["sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_documents",
            "description": (
                "Search the uploaded documents (policies, job descriptions, terms, "
                "notices, data dictionaries). Returns passages from ALL versions, "
                "each with file name, date, section and a latest/superseded status."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to look for, in plain words.",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "make_chart",
            "description": (
                "Draw a bar, line or pie chart from the rows of an earlier "
                "query_tables result. The chart is shown automatically under your "
                "answer. Never type data values yourself: they are read from the query."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Label of the query result to plot, e.g. T1.",
                    },
                    "chart_type": {"type": "string", "enum": ["bar", "line", "pie"]},
                    "x_column": {
                        "type": "string",
                        "description": "Column for the categories / x axis (pie: slice names).",
                    },
                    "y_columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Numeric column(s) to plot (pie: exactly one).",
                    },
                    "title": {"type": "string"},
                    "x_label": {"type": "string"},
                    "y_label": {"type": "string"},
                },
                "required": ["source", "chart_type", "x_column", "y_columns",
                             "title", "x_label", "y_label"],
            },
        },
    },
]
WEB_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the public web. Use it only for facts that are not in the "
            "uploaded tables or documents, such as another company's policy or "
            "current public information. Returns titles, URLs and snippets."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "A short search query."}
            },
            "required": ["query"],
        },
    },
}

WEB_ON = """
WEB SEARCH IS ON for this message
- You also have a web_search tool. Use it for facts that are not in the
  uploaded files (for example another company's policy or current public
  information).
- Questions about the uploaded tables and documents are still answered from
  them. Do not use the web for those.
- When you combine sources, say clearly which facts came from the uploaded
  files and which came from the web, and cite each with its label
  ([T#], [D#] or [W#]).
- Web results can be wrong or out of date. Prefer official pages, name the
  source in your answer, and say so if sources disagree or you could not
  confirm a fact.
"""

WEB_OFF = """
WEB SEARCH IS OFF for this message
- You have no web access. If a question needs information that is not in the
  uploaded files (for example another company's policy), say that it is not in
  the uploaded files, that web search is off, and that the user can turn it
  on. Do not answer from memory.
"""

RULES = """You are an assistant that answers questions using ONLY what your tools return:
the user's uploaded tables and documents, and the web only when web search is
turned on for the message. Never use outside knowledge  .

HOW TO WORK
- Numbers, counts, totals and rankings come from query_tables. Never calculate
  or estimate in your head. Write DuckDB SQL (double-quote identifiers if needed;
  use CAST or strptime if a date column is stored as text) and report the values
  exactly as returned.
- If a question depends on a business definition (for example what counts as
  revenue, or what a status means), call search_documents for the definition
  first, then write the SQL.
- Policy, rules, job details and terms come from search_documents.
- Questions that mix both (for example "is this order's return allowed?") need
  both tools: look up the order data, then look up the rule, then combine them.
- Follow-up questions ("that store", "the second one") refer to earlier turns of
  this conversation. Resolve them from the conversation before using tools.

DOCUMENT VERSIONS
- Each passage is marked latest (newest version of that document) or
  superseded (a newer version exists).
- Decide fact by fact. Use the newest version that actually states the fact.
  If the newest version says nothing about it, you may use an older version,
  but you must say so in the answer: name the file and its date.
- If versions disagree, give the newest version's value and do not mix numbers
  from different versions.
- "latest" does not mean the passage contains the answer. Read the text.

STAYING HONEST
- Check that a passage or result is really about what was asked. A passage about
  one company, product or role does not answer a question about another.
- If the sources do not contain the answer, say clearly that you could not find
  it in the uploaded files. Do not guess and do not substitute a related fact.
- If the tools return nothing useful, say so plainly.

CITATIONS (required)
- Every fact must be followed by the reference label of the source that supports
it: [T1] for a table result, [D3] for a document passage, [W2] for a web
  result. Use the exact labels from the tool results. Combine as [D1][W1] when needed..
- Do not invent labels. Do not cite anything you did not use.
- An answer saying the information was not found needs no citation.

STYLE
- Be concise and direct. Use a markdown table when listing several rows.
- Lead with the answer, then add short supporting detail.
"""


# ---------------------------------------------------------------- prompt

def _short(v, n=40):
    s = str(v)
    return s if len(s) <= n else s[: n - 1] + "…"


def build_system_prompt(conn, web_search: bool = False) -> tuple[str, list[str]]:
    tables = describe_tables(sample_rows=2)
    names = [t["table_name"] for t in tables]

    if tables:
        parts = ["UPLOADED TABLES (DuckDB):"]
        for t in tables:
            cols = ", ".join(f'{c["name"]} {c["type"]}' for c in t["columns"])
            parts.append(f'- {t["table_name"]} ({t["row_count"]} rows): {cols}')
            for row in t["sample_rows"]:
                parts.append(
                    "    sample: " + json.dumps({k: _short(v) for k, v in row.items()})
                )
        tables_text = "\n".join(parts)
    else:
        tables_text = "UPLOADED TABLES: none."

    docs = conn.execute(
        "SELECT f.name, d.family, d.doc_date FROM documents d "
        "JOIN files f ON f.id = d.file_id ORDER BY d.family, d.doc_date"
    ).fetchall()
    if docs:
        docs_text = "UPLOADED DOCUMENTS:\n" + "\n".join(
            f'- {d["name"]} (family: {d["family"]}, date: {d["doc_date"] or "unknown"})'
            for d in docs
        )
    else:
        docs_text = "UPLOADED DOCUMENTS: none."

    web_text = WEB_ON if web_search else WEB_OFF
    return f"{RULES}\n{web_text}\n{tables_text}\n\n{docs_text}", names


# ---------------------------------------------------------------- tools

def _tables_in_sql(sql: str, names: list[str]) -> list[str]:
    low = sql.lower()
    return [n for n in names if re.search(rf"\b{re.escape(n.lower())}\b", low)]


def _exec_tool(name: str, args: dict, state: dict, conn) -> str:
    if name == "query_tables":
        sql = str(args.get("sql", ""))
        try:
            res = run_query(sql)
        except Exception as e:
            return f"SQL error: {e}. Fix the query and try again."
        state["t"] += 1
        ref = f"T{state['t']}"
        rows = json.loads(json.dumps(res["rows"], default=str))  # make JSON-safe
        state["results"][ref] = {"columns": res["columns"], "rows": rows}
        state["cites"][ref] = {
            "type": "table",
            "tables": _tables_in_sql(res["sql"], state["table_names"]),
            "sql": res["sql"],
            "columns": res["columns"],
            "rows": rows[:MAX_ROWS_IN_CITATION],
            "row_count": len(rows),
        }
        shown = rows[:MAX_ROWS_TO_LLM]
        note = f" (showing first {len(shown)})" if len(rows) > len(shown) else ""
        return (
            f"[{ref}] Query ran OK. {len(rows)} row(s){note}.\n"
            + json.dumps({"columns": res["columns"], "rows": shown})
        )

    if name == "search_documents":
        query = str(args.get("query", ""))
        results = search_documents(conn, query, top_k=6)
        if not results:
            return "No documents are uploaded."
        lines = []
        for r in results:
            state["d"] += 1
            ref = f"D{state['d']}"
            state["cites"][ref] = {
                "type": "document",
                "file_name": r["file_name"],
                "doc_date": r["doc_date"],
                "location": r["location"],
                "status": r["status"],
                "family": r["family"],
                "text": r["text"],
            }
            status = (
                "latest version" if r["status"] == "latest"
                else "superseded: a newer version exists"
            )
            lines.append(
                f'[{ref}] file: {r["file_name"]} | date: {r["doc_date"]} | '
                f'section: {r["location"]} | {status}\n{r["text"]}'
            )
        return "\n\n".join(lines)

    if name == "make_chart":
        ref = str(args.get("source", "")).strip().strip("[]").upper()
        src = state["results"].get(ref)
        if src is None:
            return (f"No query result is labelled '{ref}'. Run query_tables first "
                    "and use its label (for example T1).")

        chart_type = str(args.get("chart_type", "")).lower()
        if chart_type not in ("bar", "line", "pie"):
            return "chart_type must be bar, line or pie."

        cols, rows = src["columns"], src["rows"]
        if not rows:
            return "That query returned no rows, so there is nothing to plot."
        lookup = {c.lower(): c for c in cols}

        x = lookup.get(str(args.get("x_column", "")).lower())
        ys_raw = args.get("y_columns") or []
        if isinstance(ys_raw, str):
            ys_raw = [ys_raw]
        ys = [lookup.get(str(y).lower()) for y in ys_raw]
        if x is None or not ys or None in ys:
            return f"Unknown column. The columns of {ref} are: {', '.join(cols)}."

        limit = 30 if chart_type == "pie" else 30
        if len(rows) > limit:
            return (f"{ref} has {len(rows)} rows, too many for a {chart_type} "
                    f"chart (max {limit}). Re-run the query with LIMIT or "
                    "aggregate it, then call make_chart again.")

        xi = cols.index(x)
        labels = [str(r[xi]) for r in rows]
        series = []
        for y in ys:
            yi = cols.index(y)
            try:
                vals = [float(r[yi]) for r in rows]
            except (TypeError, ValueError):
                return f"Column '{y}' is not numeric, so it can't be plotted."
            series.append({"name": y, "values": vals})

        if chart_type == "pie":
            series = series[:1]
            if any(v < 0 for v in series[0]["values"]):
                return "A pie chart can't show negative values. Use a bar chart."

        state["charts"].append(
            {
                "chart_type": chart_type,
                "title": str(args.get("title") or "Chart"),
                "x_label": str(args.get("x_label") or x),
                "y_label": str(args.get("y_label") or ys[0]),
                "labels": labels,
                "series": series,
                "source_ref": ref,
            }
        )
        return (f"Chart created from [{ref}] and will appear under your answer. "
                f"Cite [{ref}] in your reply.")
    
    if name == "web_search":
        if not state["web"]:
            return "Web search is turned off for this message."
        query = str(args.get("query", "")).strip()
        if not query:
            return "Give a search query."
        try:
            results = search_web(query)
        except Exception as e:
            return f"Web search failed: {e}"
        if not results:
            return "The web search returned no results."
        lines = []
        for r in results:
            state["w"] += 1
            ref = f"W{state['w']}"
            state["cites"][ref] = {
                "type": "web",
                "title": r["title"],
                "url": r["url"],
                "text": r["content"][:600],
            }
            lines.append(f'[{ref}] {r["title"]} | {r["url"]}\n{r["content"][:1200]}')
        return "\n\n".join(lines)
    
    return f"Unknown tool: {name}"


# ---------------------------------------------------------------- citations

MARK = re.compile(r"\[([DTW]\d+(?:\s*,\s*[DTW]\d+)*)\]")
OLD_MARK = re.compile(r"\s*\[\d+(?:\s*,\s*\d+)*\]")


def strip_markers(text: str) -> str:
    """Remove [1], [2, 3] markers (used when sending past answers back to the LLM)."""
    return OLD_MARK.sub("", text)


def finalize(answer: str, cites: dict, extra_refs=()) -> tuple[str, list[dict], dict]:
    """Keep only the sources the answer used, renumbered 1..n.
    extra_refs are always kept (a chart's data source, even if the model forgot to cite it)."""
    order: list[str] = []

    def repl(m):
        nums = []
        for ref in re.split(r"\s*,\s*", m.group(1)):
            if ref not in cites:
                continue  # the model invented a label: drop it
            if ref not in order:
                order.append(ref)
            nums.append(str(order.index(ref) + 1))
        return f"[{', '.join(nums)}]" if nums else ""

    text = MARK.sub(repl, answer)
    for ref in extra_refs:
        if ref in cites and ref not in order:
            order.append(ref)

    citations = []
    numbers = {}
    for i, ref in enumerate(order, start=1):
        c = dict(cites[ref])
        c["ref"] = i
        citations.append(c)
        numbers[ref] = i
    return text, citations, numbers


# ---------------------------------------------------------------- the loop

def _chat(client, messages, tools):
    last = None
    for _ in range(2):  # one retry: tool-call formatting glitches do happen
        try:
            return client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0,
            )
        except Exception as e:
            last = e
    raise last


def run_agent(conn, history: list[dict], web_search: bool = False) -> dict:
    client = get_client()
    prompt, table_names = build_system_prompt(conn, web_search)
    tools = TOOLS + [WEB_TOOL] if web_search else TOOLS
    state = {
        "t": 0, "d": 0, "w": 0, "cites": {}, "results": {}, "charts": [],
        "table_names": table_names, "web": web_search,
    }
    messages = [{"role": "system", "content": prompt}] + history

    answer = None
    for _ in range(MAX_STEPS):
        msg = _chat(client, messages, tools).choices[0].message
        if not msg.tool_calls:
            answer = msg.content or ""
            break

        messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            }
        )
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            result = _exec_tool(tc.function.name, args, state, conn)
            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": result}
            )

    if answer is None:
        answer = (
            "I couldn't finish working this out. "
            "Please try rephrasing or splitting the question."
        )

    text, citations, numbers = finalize(
        answer, state["cites"], [c["source_ref"] for c in state["charts"]]
    )
    charts = []
    for c in state["charts"]:
        spec = dict(c)
        spec["cite"] = numbers.get(spec.pop("source_ref"))  # which [n] is its source
        charts.append(spec)
    return {"answer": text, "citations": citations, "charts": charts}