#!/usr/bin/env python3
"""
Veras Kokbok - liten webbserver utan externa beroenden (endast Python 3 stdlib).

Miljövariabler:
  KOKBOK_HOST  adress att lyssna på (standard 127.0.0.1 - nås via `tailscale serve`)
  KOKBOK_PORT  port (standard 8090)
  KOKBOK_DB    sökväg till databasen (standard ./data/kokbok.db)
               Bilder sparas i mappen images/ bredvid databasen.
  KOKBOK_LAN   extra adresser att lyssna på, t.ex. "192.168.1.20:8443" (kommaseparerat).
               Används för att nå appen direkt på hemnätet utan Tailscale.
"""
import json
import mimetypes
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("text/javascript", ".js")
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

VERSION = "0.6.0"

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = (BASE_DIR / "static").resolve()
DB_PATH = Path(os.environ.get("KOKBOK_DB", BASE_DIR / "data" / "kokbok.db"))
IMAGES_DIR = (DB_PATH.parent / "images").resolve()
HOST = os.environ.get("KOKBOK_HOST", "127.0.0.1")
PORT = int(os.environ.get("KOKBOK_PORT", "8090"))
ENV = os.environ.get("KOKBOK_ENV", "prod")
LAN_LISTEN = os.environ.get("KOKBOK_LAN", "")

MAX_NAME = 120
MAX_TEXT = 20000
MAX_INGREDIENTS = 100
MAX_AMOUNT = 20
MAX_UNIT = 20
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_QUANTITY = 999
PANTRY_KINDS = ("vital", "static")
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

# Startmall för skafferiet - skapas en gång (v0.4.0). Allt går att ta bort.
PANTRY_TEMPLATE = [
    ("Salt", "static"), ("Svartpeppar", "static"), ("Olivolja", "static"), ("Rapsolja", "static"),
    ("Vetemjöl", "static"), ("Strösocker", "static"),
    ("Ägg", "vital"), ("Mjölk", "vital"), ("Smör", "vital"), ("Gul lök", "vital"), ("Vitlök", "vital"),
]

# Enheter som känns igen när en ingrediensrad tolkas ("2 dl mjölk" -> 2 | dl | mjölk).
# Samma lista finns i static/index.html.
UNITS = [
    "krm", "tsk", "msk", "ml", "cl", "dl", "l", "g", "hg", "kg", "st", "förp", "paket", "påse",
    "burk", "flaska", "klyfta", "klyftor", "skiva", "skivor", "kruka", "knippe", "nypa", "näve",
    "skvätt", "bit", "bitar", "port",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id    INTEGER,
    type         TEXT NOT NULL CHECK (type IN ('category', 'recipe')),
    name         TEXT NOT NULL,
    ingredients  TEXT NOT NULL DEFAULT '',   -- används inte sedan v0.3.0 (se tabellen ingredients)
    instructions TEXT NOT NULL DEFAULT '',
    is_system    INTEGER NOT NULL DEFAULT 0,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(parent_id);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS ingredients (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id  INTEGER NOT NULL,
    position   INTEGER NOT NULL,
    amount     TEXT NOT NULL DEFAULT '',
    unit       TEXT NOT NULL DEFAULT '',
    name       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ingredients_recipe ON ingredients(recipe_id, position);
CREATE TABLE IF NOT EXISTS pantry (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('vital', 'static')),
    quantity   INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""

INSERT_SQL = (
    "INSERT INTO nodes (parent_id, type, name, instructions, is_system, created_at, updated_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)

IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
IMAGE_NAME_RE = re.compile(r"\d+-[0-9a-f]{16}\.(?:jpg|png|webp)")

write_lock = threading.Lock()


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


# ---------------------------------------------------------------- tolkning av ingrediensrader

_NUM = r"(?:\d+\s+\d+/\d+|\d+/\d+|\d+(?:[.,]\d+)?(?:\s?[½¼¾⅓⅔⅛])?|[½¼¾⅓⅔⅛])"
_AMOUNT_RE = re.compile(rf"^\s*({_NUM}(?:\s*[-–]\s*{_NUM})?)(?=\s|$)\s*(.*)$")


def parse_line(line):
    """'2 dl mjölk' -> {'amount': '2', 'unit': 'dl', 'name': 'mjölk'}"""
    line = " ".join(line.replace("\t", " ").split()).lstrip("-•* ").strip()
    amount, unit, rest = "", "", line
    m = _AMOUNT_RE.match(line)
    if m and m.group(2):
        amount, rest = m.group(1).strip(), m.group(2)
    parts = rest.split(" ", 1)
    if len(parts) == 2 and parts[0].rstrip(".").casefold() in UNITS:
        unit, rest = parts[0].rstrip(".").casefold(), parts[1]
    return {"amount": amount[:MAX_AMOUNT], "unit": unit[:MAX_UNIT], "name": rest.strip()[:MAX_NAME]}


# ---------------------------------------------------------------- databas

def connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(nodes)")}
        if "image" not in cols:
            conn.execute("ALTER TABLE nodes ADD COLUMN image TEXT")
        with conn:
            now = time.time()
            if not conn.execute("SELECT 1 FROM meta WHERE key = 'seeded'").fetchone():
                for name, subs in TEMPLATE:
                    cur = conn.execute(INSERT_SQL, (None, "category", name, "", 0, now, now))
                    for sub in subs:
                        conn.execute(INSERT_SQL, (cur.lastrowid, "category", sub, "", 0, now, now))
                conn.execute("INSERT INTO meta (key, value) VALUES ('seeded', '1')")
            if not conn.execute("SELECT 1 FROM nodes WHERE is_system = 1").fetchone():
                conn.execute(INSERT_SQL, (None, "category", OVRIGT_NAME, "", 1, now, now))
            migrate_ingredients(conn)
            if not conn.execute("SELECT 1 FROM meta WHERE key = 'pantry_seeded'").fetchone():
                if not conn.execute("SELECT 1 FROM pantry").fetchone():
                    for name, kind in PANTRY_TEMPLATE:
                        conn.execute(
                            "INSERT INTO pantry (name, kind, quantity, created_at, updated_at) VALUES (?, ?, 0, ?, ?)",
                            (name, kind, now, now),
                        )
                conn.execute("INSERT INTO meta (key, value) VALUES ('pantry_seeded', '1')")
    finally:
        conn.close()


def migrate_ingredients(conn):
    """v0.3.0: fritextingredienser -> rader med mängd/enhet/namn. Körs en gång."""
    if conn.execute("SELECT 1 FROM meta WHERE key = 'ingredients_v2'").fetchone():
        return
    rows = conn.execute("SELECT id, ingredients FROM nodes WHERE type = 'recipe' AND ingredients != ''").fetchall()
    for r in rows:
        lines = [l for l in r["ingredients"].splitlines() if l.strip()]
        for pos, line in enumerate(lines[:MAX_INGREDIENTS]):
            p = parse_line(line)
            if p["name"]:
                conn.execute(
                    "INSERT INTO ingredients (recipe_id, position, amount, unit, name) VALUES (?, ?, ?, ?, ?)",
                    (r["id"], pos, p["amount"], p["unit"], p["name"]),
                )
        conn.execute("UPDATE nodes SET ingredients = '' WHERE id = ?", (r["id"],))
    conn.execute("INSERT INTO meta (key, value) VALUES ('ingredients_v2', '1')")
    if rows:
        print(f"Migrerade ingredienser för {len(rows)} recept till rader.", flush=True)


def image_url(row):
    if not row["image"]:
        return None
    return f"/images/{row['image']}"


def to_dict(row, ingredients=None):
    is_recipe = row["type"] == "recipe"
    return {
        "id": row["id"],
        "parent_id": row["parent_id"],
        "type": row["type"],
        "name": row["name"],
        "ingredients": (ingredients or []) if is_recipe else [],
        "instructions": row["instructions"] if is_recipe else "",
        "image_url": image_url(row) if is_recipe else None,
        "is_system": bool(row["is_system"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def ingredients_for(conn, recipe_id):
    return [
        {"amount": r["amount"], "unit": r["unit"], "name": r["name"]}
        for r in conn.execute(
            "SELECT amount, unit, name FROM ingredients WHERE recipe_id = ? ORDER BY position", (recipe_id,)
        )
    ]


def node_dict(conn, node_id):
    row = get_node(conn, node_id)
    return to_dict(row, ingredients_for(conn, node_id) if row["type"] == "recipe" else None)


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
    value = value.replace("\r\n", "\n").strip()
    if len(value) > MAX_TEXT:
        raise ApiError(400, "Texten är för lång.")
    return value


def clean_ingredients(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ApiError(400, "Ingredienserna har fel format.")
    out = []
    for i, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ApiError(400, "Ingredienserna har fel format.")
        amount = " ".join(str(item.get("amount") or "").split())
        unit = " ".join(str(item.get("unit") or "").split())
        name = " ".join(str(item.get("name") or "").split())
        if not (amount or unit or name):
            continue  # helt tom rad
        if not name:
            raise ApiError(400, f"Ingrediensrad {i} saknar ingrediens.")
        if len(amount) > MAX_AMOUNT or len(unit) > MAX_UNIT or len(name) > MAX_NAME:
            raise ApiError(400, f"Ingrediensrad {i} är för lång.")
        out.append({"amount": amount, "unit": unit, "name": name})
    if len(out) > MAX_INGREDIENTS:
        raise ApiError(400, f"Ett recept kan ha högst {MAX_INGREDIENTS} ingredienser.")
    return out


def save_ingredients(conn, recipe_id, items):
    conn.execute("DELETE FROM ingredients WHERE recipe_id = ?", (recipe_id,))
    conn.executemany(
        "INSERT INTO ingredients (recipe_id, position, amount, unit, name) VALUES (?, ?, ?, ?, ?)",
        [(recipe_id, pos, it["amount"], it["unit"], it["name"]) for pos, it in enumerate(items)],
    )


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


# ---------------------------------------------------------------- operationer
# Varje operation får `ctx`, där filer som ska tas bort efter en lyckad transaktion samlas.

def list_nodes(conn):
    ings = {}
    for r in conn.execute("SELECT recipe_id, amount, unit, name FROM ingredients ORDER BY recipe_id, position"):
        ings.setdefault(r["recipe_id"], []).append({"amount": r["amount"], "unit": r["unit"], "name": r["name"]})
    return [to_dict(r, ings.get(r["id"])) for r in conn.execute("SELECT * FROM nodes ORDER BY id")]


def create_node(conn, ctx, data):
    node_type = data.get("type")
    if node_type not in ("category", "recipe"):
        raise ApiError(400, "Okänd typ.")
    name = clean_name(data.get("name"))
    parent_id = validate_parent(conn, node_type, parse_parent(data.get("parent_id")))
    check_duplicate(conn, node_type, name, parent_id)
    instructions = clean_text(data.get("instructions")) if node_type == "recipe" else ""
    ingredients = clean_ingredients(data.get("ingredients")) if node_type == "recipe" else []
    now = time.time()
    cur = conn.execute(INSERT_SQL, (parent_id, node_type, name, instructions, 0, now, now))
    if node_type == "recipe":
        save_ingredients(conn, cur.lastrowid, ingredients)
    return {"node": node_dict(conn, cur.lastrowid)}


def update_node(conn, ctx, node_id, data):
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
    instructions = node["instructions"]
    if node_type == "recipe":
        if "instructions" in data:
            instructions = clean_text(data.get("instructions"))
        if "ingredients" in data:
            save_ingredients(conn, node_id, clean_ingredients(data.get("ingredients")))
    conn.execute(
        "UPDATE nodes SET name = ?, parent_id = ?, instructions = ?, updated_at = ? WHERE id = ?",
        (name, parent_id, instructions, time.time(), node_id),
    )
    return {"node": node_dict(conn, node_id)}


def delete_node(conn, ctx, node_id):
    node = get_node(conn, node_id)
    if node["is_system"]:
        raise ApiError(400, f"{node['name']} kan inte tas bort - dit hamnar recept som saknar kategori.")
    if node["type"] == "recipe":
        conn.execute("DELETE FROM ingredients WHERE recipe_id = ?", (node_id,))
        conn.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
        if node["image"]:
            ctx["remove"].append(node["image"])
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


# ---------------------------------------------------------------- skafferi

def pantry_dict(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "quantity": row["quantity"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_pantry(conn, item_id):
    row = conn.execute("SELECT * FROM pantry WHERE id = ?", (item_id,)).fetchone()
    if not row:
        raise ApiError(404, "Hittade inte ingrediensen - ladda om sidan.")
    return row


def clean_kind(value):
    if value not in PANTRY_KINDS:
        raise ApiError(400, "Välj Vital eller Statisk.")
    return value


def clean_quantity(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
        raise ApiError(400, "Antalet måste vara ett heltal.")
    value = int(value)
    if not 0 <= value <= MAX_QUANTITY:
        raise ApiError(400, f"Antalet måste vara mellan 0 och {MAX_QUANTITY}.")
    return value


def check_pantry_duplicate(conn, name, item_id=None):
    for r in conn.execute("SELECT id, name FROM pantry"):
        if r["id"] != item_id and r["name"].casefold() == name.casefold():
            raise ApiError(409, f"\"{r['name']}\" finns redan bland ingredienserna.")


def list_pantry(conn):
    return [pantry_dict(r) for r in conn.execute("SELECT * FROM pantry ORDER BY name COLLATE NOCASE")]


def create_pantry(conn, ctx, data):
    name = clean_name(data.get("name"))
    kind = clean_kind(data.get("kind"))
    quantity = clean_quantity(data.get("quantity", 0))
    check_pantry_duplicate(conn, name)
    now = time.time()
    cur = conn.execute(
        "INSERT INTO pantry (name, kind, quantity, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (name, kind, quantity, now, now),
    )
    return {"item": pantry_dict(get_pantry(conn, cur.lastrowid))}


def update_pantry(conn, ctx, item_id, data):
    row = get_pantry(conn, item_id)
    name = clean_name(data["name"]) if "name" in data else row["name"]
    kind = clean_kind(data["kind"]) if "kind" in data else row["kind"]
    quantity = clean_quantity(data["quantity"]) if "quantity" in data else row["quantity"]
    check_pantry_duplicate(conn, name, item_id)
    conn.execute(
        "UPDATE pantry SET name = ?, kind = ?, quantity = ?, updated_at = ? WHERE id = ?",
        (name, kind, quantity, time.time(), item_id),
    )
    return {"item": pantry_dict(get_pantry(conn, item_id))}


def adjust_pantry(conn, ctx, item_id, data):
    """+1 / -1 räknas på servern, så att två telefoner som trycker samtidigt inte skriver över varandra."""
    row = get_pantry(conn, item_id)
    delta = data.get("delta")
    if isinstance(delta, bool) or not isinstance(delta, int) or not -MAX_QUANTITY <= delta <= MAX_QUANTITY:
        raise ApiError(400, "Ogiltig ändring.")
    if row["kind"] != "vital":
        raise ApiError(400, "Statiska ingredienser finns alltid och har inget antal.")
    quantity = max(0, min(MAX_QUANTITY, row["quantity"] + delta))
    conn.execute("UPDATE pantry SET quantity = ?, updated_at = ? WHERE id = ?", (quantity, time.time(), item_id))
    return {"item": pantry_dict(get_pantry(conn, item_id))}


def delete_pantry(conn, ctx, item_id):
    get_pantry(conn, item_id)
    conn.execute("DELETE FROM pantry WHERE id = ?", (item_id,))
    return {"deleted": 1}


def sniff_image(data):
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def set_image(conn, ctx, node_id, data):
    node = get_node(conn, node_id)
    if node["type"] != "recipe":
        raise ApiError(400, "Bara recept kan ha bild.")
    ext = sniff_image(data)
    if not ext:
        raise ApiError(415, "Bilden måste vara JPG, PNG eller WebP.")
    filename = f"{node_id}-{secrets.token_hex(8)}.{ext}"
    tmp = IMAGES_DIR / (filename + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(IMAGES_DIR / filename)
    ctx["on_fail_remove"].append(filename)
    if node["image"]:
        ctx["remove"].append(node["image"])
    conn.execute("UPDATE nodes SET image = ?, updated_at = ? WHERE id = ?", (filename, time.time(), node_id))
    return {"node": node_dict(conn, node_id)}


def remove_image(conn, ctx, node_id):
    node = get_node(conn, node_id)
    if node["image"]:
        ctx["remove"].append(node["image"])
    conn.execute("UPDATE nodes SET image = NULL, updated_at = ? WHERE id = ?", (time.time(), node_id))
    return {"node": node_dict(conn, node_id)}


def unlink_images(names):
    for name in names:
        if IMAGE_NAME_RE.fullmatch(name or ""):
            try:
                (IMAGES_DIR / name).unlink()
            except FileNotFoundError:
                pass


# ---------------------------------------------------------------- http

class Handler(BaseHTTPRequestHandler):
    server_version = f"VerasKokbok/{VERSION}"

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

    def read_body(self, limit):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(400, "Ogiltig längd.")
        if length > limit:
            raise ApiError(413, "Filen är för stor." if limit == MAX_IMAGE_BYTES else "För mycket data.")
        return self.rfile.read(length) if length else b""

    def read_json(self):
        raw = self.read_body(1_000_000) or b"{}"
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
            elif method == "GET" and path.startswith("/images/"):
                self.serve_image(path[len("/images/"):])
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

    def run_write(self, action, *args):
        ctx = {"remove": [], "on_fail_remove": []}
        with write_lock:
            conn = connect()
            try:
                with conn:
                    result = action(conn, ctx, *args)
            except BaseException:
                unlink_images(ctx["on_fail_remove"])
                raise
            finally:
                conn.close()
        unlink_images(ctx["remove"])
        return result

    def handle_api(self, method, path):
        if path == "/api/health":
            return self.send_json(200, {"ok": True, "env": ENV, "version": VERSION})

        if re.fullmatch(r"/api/pantry/?", path):
            if method == "GET":
                conn = connect()
                try:
                    return self.send_json(200, {"items": list_pantry(conn)})
                finally:
                    conn.close()
            if method == "POST":
                return self.send_json(201, self.run_write(create_pantry, self.read_json()))
            raise ApiError(405, "Metoden stöds inte.")

        m = re.fullmatch(r"/api/pantry/(\d+)(/adjust)?/?", path)
        if m:
            item_id = int(m.group(1))
            if m.group(2):
                if method == "POST":
                    return self.send_json(200, self.run_write(adjust_pantry, item_id, self.read_json()))
            elif method == "PUT":
                return self.send_json(200, self.run_write(update_pantry, item_id, self.read_json()))
            elif method == "DELETE":
                return self.send_json(200, self.run_write(delete_pantry, item_id))
            raise ApiError(405, "Metoden stöds inte.")

        m = re.fullmatch(r"/api/nodes/(\d+)/image/?", path)
        if m:
            node_id = int(m.group(1))
            if method == "PUT":
                return self.send_json(200, self.run_write(set_image, node_id, self.read_body(MAX_IMAGE_BYTES)))
            if method == "DELETE":
                return self.send_json(200, self.run_write(remove_image, node_id))
            raise ApiError(405, "Metoden stöds inte.")

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
            return self.send_json(201, self.run_write(create_node, self.read_json()))
        if method == "PUT" and node_id is not None:
            return self.send_json(200, self.run_write(update_node, node_id, self.read_json()))
        if method == "DELETE" and node_id is not None:
            return self.send_json(200, self.run_write(delete_node, node_id))
        raise ApiError(405, "Metoden stöds inte.")

    def serve_file(self, target, ctype, cache):
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def serve_image(self, name):
        if not IMAGE_NAME_RE.fullmatch(name) or not (IMAGES_DIR / name).is_file():
            raise ApiError(404, "Bilden hittades inte.")
        ctype = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[name.rsplit(".", 1)[1]]
        # Filnamnet byts vid varje ny bild, så den kan cachas länge.
        self.serve_file(IMAGES_DIR / name, ctype, "public, max-age=31536000, immutable")

    def serve_static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        target = (STATIC_DIR / path.lstrip("/")).resolve()
        if STATIC_DIR not in target.parents or not target.is_file():
            raise ApiError(404, "Hittades inte.")
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self.serve_file(target, ctype, "no-cache")


def parse_listen(value):
    """'192.168.1.20:8443' -> ('192.168.1.20', 8443)"""
    host, sep, port = value.strip().rpartition(":")
    host = host.strip("[]")
    if not sep or not host or not port.isdigit() or not 0 < int(port) < 65536:
        raise SystemExit(f"KOKBOK_LAN: ogiltig adress {value!r} - skriv t.ex. 192.168.1.20:8443")
    return host, int(port)


def serve_extra(addr):
    """Extra lyssnare, t.ex. på hemnätet. Finns inte adressen än (nätverket uppe sent,
    ny IP från routern) försöker den igen var 30:e sekund i stället för att stoppa appen."""
    warned = False
    while True:
        try:
            srv = ThreadingHTTPServer(addr, Handler)
        except OSError as e:
            if not warned:
                print(f"Kan inte lyssna på {addr[0]}:{addr[1]} ({e.strerror}). Försöker igen var 30:e sekund.", flush=True)
                warned = True
            time.sleep(30)
            continue
        print(f"Lyssnar även på http://{addr[0]}:{addr[1]}", flush=True)
        srv.serve_forever()
        return


def main():
    init_db()
    extra = [parse_listen(v) for v in LAN_LISTEN.split(",") if v.strip()]
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Veras Kokbok {VERSION} [{ENV}] kör på http://{HOST}:{PORT}  (databas: {DB_PATH})", flush=True)
    for addr in extra:
        threading.Thread(target=serve_extra, args=(addr,), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
