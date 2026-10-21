#!/usr/bin/env python3
"""
Veras Kokbok - liten webbserver utan externa beroenden (endast Python 3 stdlib).

Miljövariabler:
  KOKBOK_HOST  adress att lyssna på (standard 127.0.0.1 - nås via `tailscale serve`)
  KOKBOK_PORT  port (standard 8090)
  KOKBOK_DB    sökväg till databasen (standard ./data/kokbok.db)
"""
import json
import mimetypes
import os
import re
import sqlite3
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = (BASE_DIR / "static").resolve()
DB_PATH = Path(os.environ.get("KOKBOK_DB", BASE_DIR / "data" / "kokbok.db"))
HOST = os.environ.get("KOKBOK_HOST", "127.0.0.1")
PORT = int(os.environ.get("KOKBOK_PORT", "8090"))
ENV = os.environ.get("KOKBOK_ENV", "prod")  # "test" visar en TEST-markering i appen

MAX_NAME = 120
MAX_TEXT = 20000
OVRIGT_NAME = "Övrigt"

# Startmall - skapas bara första gången databasen skapas. Allt här går att ta bort.
TEMPLATE = [
    ("Frukost & brunch", []),
    ("Förrätter & smårätter", []),
    ("Huvudrätter", ["Kött", "Fågel", "Fisk & skaldjur", "Vegetariskt", "Pasta & nudlar"]),
    ("Soppor & grytor", []),
    ("Sallader", []),
    ("Såser & tillbehör", []),
    ("Bakning", ["Bröd", "Kakor & småkakor", "Tårtor & långpannor"]),
    ("Efterrätter", []),
    ("Drycker", []),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id    INTEGER,
    type         TEXT NOT NULL CHECK (type IN ('category', 'recipe')),
    name         TEXT NOT NULL,
    ingredients  TEXT NOT NULL DEFAULT '',
    instructions TEXT NOT NULL DEFAULT '',
    is_system    INTEGER NOT NULL DEFAULT 0,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(parent_id);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

INSERT_SQL = (
    "INSERT INTO nodes (parent_id, type, name, ingredients, instructions, is_system, created_at, updated_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)

write_lock = threading.Lock()


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


# ---------------------------------------------------------------- databas

def connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = connect()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        with conn:
            now = time.time()
            if not conn.execute("SELECT 1 FROM meta WHERE key = 'seeded'").fetchone():
                for name, subs in TEMPLATE:
                    cur = conn.execute(INSERT_SQL, (None, "category", name, "", "", 0, now, now))
                    for sub in subs:
                        conn.execute(INSERT_SQL, (cur.lastrowid, "category", sub, "", "", 0, now, now))
                conn.execute("INSERT INTO meta (key, value) VALUES ('seeded', '1')")
            if not conn.execute("SELECT 1 FROM nodes WHERE is_system = 1").fetchone():
                conn.execute(INSERT_SQL, (None, "category", OVRIGT_NAME, "", "", 1, now, now))
    finally:
        conn.close()


def to_dict(row):
    return {
        "id": row["id"],
        "parent_id": row["parent_id"],
        "type": row["type"],
        "name": row["name"],
        "ingredients": row["ingredients"],
        "instructions": row["instructions"],
        "is_system": bool(row["is_system"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_node(conn, node_id, status=404):
    row = conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
    if not row:
        raise ApiError(status, "Hittade inte det du letade efter - ladda om sidan.")
    return row


def descendant_ids(conn, node_id):
    rows = conn.execute(
        """
        WITH RECURSIVE sub(id) AS (
            SELECT id FROM nodes WHERE parent_id = ?
            UNION ALL
            SELECT n.id FROM nodes n JOIN sub ON n.parent_id = sub.id
        )
        SELECT id FROM sub
        """,
        (node_id,),
    ).fetchall()
    return [r["id"] for r in rows]


def ovrigt_id(conn):
    return conn.execute("SELECT id FROM nodes WHERE is_system = 1 LIMIT 1").fetchone()["id"]


def clean_name(value):
    if not isinstance(value, str) or not value.strip():
        raise ApiError(400, "Namnet får inte vara tomt.")
    value = " ".join(value.split())
    if len(value) > MAX_NAME:
        raise ApiError(400, f"Namnet får vara högst {MAX_NAME} tecken.")
    return value


def clean_text(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ApiError(400, "Ogiltig text.")
    value = value.strip()
    if len(value) > MAX_TEXT:
        raise ApiError(400, "Texten är för lång.")
    return value


def parse_parent(value):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ApiError(400, "Ogiltig kategori.")


def validate_parent(conn, node_type, parent_id, node_id=None):
    if parent_id is None:
        if node_type == "recipe":
            raise ApiError(400, "Ett recept måste ligga i en kategori.")
        return None
    parent = get_node(conn, parent_id, status=400)
    if parent["type"] != "category":
        raise ApiError(400, "Det går bara att lägga saker i en kategori.")
    if node_id is not None and node_type == "category":
        if parent_id == node_id or parent_id in descendant_ids(conn, node_id):
            raise ApiError(400, "En kategori kan inte flyttas in i sig själv eller sina underkategorier.")
    return parent_id


def check_duplicate(conn, node_type, name, parent_id, node_id=None):
    rows = conn.execute(
        "SELECT id, name FROM nodes WHERE type = ? AND parent_id IS ?", (node_type, parent_id)
    ).fetchall()
    for r in rows:
        if r["id"] != node_id and r["name"].casefold() == name.casefold():
            what = "ett recept" if node_type == "recipe" else "en kategori"
            raise ApiError(409, f"Det finns redan {what} som heter \"{r['name']}\" här.")


def list_nodes(conn):
    return [to_dict(r) for r in conn.execute("SELECT * FROM nodes ORDER BY id")]


def create_node(conn, data):
    node_type = data.get("type")
    if node_type not in ("category", "recipe"):
        raise ApiError(400, "Okänd typ.")
    name = clean_name(data.get("name"))
    parent_id = validate_parent(conn, node_type, parse_parent(data.get("parent_id")))
    check_duplicate(conn, node_type, name, parent_id)
    ingredients = clean_text(data.get("ingredients")) if node_type == "recipe" else ""
    instructions = clean_text(data.get("instructions")) if node_type == "recipe" else ""
    now = time.time()
    cur = conn.execute(INSERT_SQL, (parent_id, node_type, name, ingredients, instructions, 0, now, now))
    return to_dict(get_node(conn, cur.lastrowid))


def update_node(conn, node_id, data):
    node = get_node(conn, node_id)
    node_type = node["type"]
    name = clean_name(data["name"]) if "name" in data else node["name"]
    parent_id = node["parent_id"]
    if "parent_id" in data:
        new_parent = parse_parent(data.get("parent_id"))
        if new_parent != parent_id:
            if node["is_system"]:
                raise ApiError(400, f"{node['name']} ligger alltid på högsta nivån och kan inte flyttas.")
            parent_id = validate_parent(conn, node_type, new_parent, node_id)
    check_duplicate(conn, node_type, name, parent_id, node_id)
    ingredients, instructions = node["ingredients"], node["instructions"]
    if node_type == "recipe":
        if "ingredients" in data:
            ingredients = clean_text(data.get("ingredients"))
        if "instructions" in data:
            instructions = clean_text(data.get("instructions"))
    conn.execute(
        "UPDATE nodes SET name = ?, parent_id = ?, ingredients = ?, instructions = ?, updated_at = ? WHERE id = ?",
        (name, parent_id, ingredients, instructions, time.time(), node_id),
    )
    return to_dict(get_node(conn, node_id))


def delete_node(conn, node_id):
    node = get_node(conn, node_id)
    if node["is_system"]:
        raise ApiError(400, f"{node['name']} kan inte tas bort - dit hamnar recept som saknar kategori.")
    if node["type"] == "recipe":
        conn.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
        return {"deleted_categories": 0, "deleted_recipes": 1, "moved_recipes": 0}

    ids = [node_id] + descendant_ids(conn, node_id)
    marks = ",".join("?" * len(ids))
    target = ovrigt_id(conn)
    moved = conn.execute(
        f"UPDATE nodes SET parent_id = ?, updated_at = ? WHERE type = 'recipe' AND id IN ({marks})",
        (target, time.time(), *ids),
    ).rowcount
    deleted = conn.execute(f"DELETE FROM nodes WHERE type = 'category' AND id IN ({marks})", ids).rowcount
    return {"deleted_categories": deleted, "deleted_recipes": 0, "moved_recipes": moved}


# ---------------------------------------------------------------- http

class Handler(BaseHTTPRequestHandler):
    server_version = "VerasKokbok/0.1"

    def log_message(self, fmt, *args):
        sys.stdout.write("%s - %s\n" % (self.address_string(), fmt % args))
        sys.stdout.flush()

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000_000:
            raise ApiError(413, "För mycket data.")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "Ogiltig JSON.")
        if not isinstance(data, dict):
            raise ApiError(400, "Ogiltig JSON.")
        return data

    def route(self, method):
        path = urlparse(self.path).path
        try:
            if path.startswith("/api/"):
                self.handle_api(method, path)
            elif method == "GET":
                self.serve_static(path)
            else:
                raise ApiError(405, "Metoden stöds inte.")
        except ApiError as e:
            self.send_json(e.status, {"error": e.message})
        except Exception:
            traceback.print_exc()
            self.send_json(500, {"error": "Internt serverfel."})

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PUT(self):
        self.route("PUT")

    def do_DELETE(self):
        self.route("DELETE")

    def handle_api(self, method, path):
        if path == "/api/health":
            return self.send_json(200, {"ok": True, "env": ENV})
        m = re.fullmatch(r"/api/nodes(?:/(\d+))?/?", path)
        if not m:
            raise ApiError(404, "Okänd adress.")
        node_id = int(m.group(1)) if m.group(1) else None

        if method == "GET" and node_id is None:
            conn = connect()
            try:
                return self.send_json(200, {"nodes": list_nodes(conn)})
            finally:
                conn.close()

        if method == "POST" and node_id is None:
            action, args, status = create_node, (self.read_json(),), 201
        elif method == "PUT" and node_id is not None:
            action, args, status = update_node, (node_id, self.read_json()), 200
        elif method == "DELETE" and node_id is not None:
            action, args, status = delete_node, (node_id,), 200
        else:
            raise ApiError(405, "Metoden stöds inte.")

        with write_lock:
            conn = connect()
            try:
                with conn:
                    result = action(conn, *args)
            finally:
                conn.close()
        payload = {"node": result} if action is not delete_node else result
        self.send_json(status, payload)

    def serve_static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        target = (STATIC_DIR / path.lstrip("/")).resolve()
        if STATIC_DIR not in target.parents or not target.is_file():
            raise ApiError(404, "Hittades inte.")
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)


def main():
    init_db()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Veras Kokbok [{ENV}] kör på http://{HOST}:{PORT}  (databas: {DB_PATH})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
