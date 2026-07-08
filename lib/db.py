"""SQLiteを使ったデータアクセス層。

事業所ごとの加算マスタ、レセプト請求明細、画面上の列マッピング設定を保持する。
"""
import json
import sqlite3
from pathlib import Path

import pandas as pd

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS offices (
    office_code TEXT PRIMARY KEY,
    office_name TEXT,
    service_type TEXT
);

CREATE TABLE IF NOT EXISTS addition_master (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    office_code TEXT NOT NULL,
    addition_code TEXT NOT NULL,
    addition_name TEXT,
    category TEXT DEFAULT '個別',
    unit INTEGER,
    valid_from TEXT,
    valid_to TEXT,
    note TEXT,
    UNIQUE(office_code, addition_code)
);

CREATE TABLE IF NOT EXISTS import_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_type TEXT NOT NULL,
    filename TEXT,
    target_month TEXT,
    imported_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER,
    office_code TEXT NOT NULL,
    office_name TEXT,
    user_code TEXT,
    user_name TEXT,
    target_month TEXT NOT NULL,
    addition_code TEXT NOT NULL,
    addition_name TEXT,
    unit INTEGER,
    count INTEGER,
    FOREIGN KEY(batch_id) REFERENCES import_batches(id)
);

CREATE TABLE IF NOT EXISTS column_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_type TEXT NOT NULL,
    header_signature TEXT NOT NULL,
    mapping_json TEXT NOT NULL,
    saved_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(file_type, header_signature)
);
"""


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


# --- 事業所 -----------------------------------------------------------

def upsert_office(conn, office_code: str, office_name: str = "", service_type: str = "") -> None:
    conn.execute(
        """
        INSERT INTO offices (office_code, office_name, service_type)
        VALUES (?, ?, ?)
        ON CONFLICT(office_code) DO UPDATE SET
            office_name = excluded.office_name,
            service_type = COALESCE(NULLIF(excluded.service_type, ''), offices.service_type)
        """,
        (office_code, office_name, service_type),
    )
    conn.commit()


def list_offices(conn) -> pd.DataFrame:
    return pd.read_sql("SELECT * FROM offices ORDER BY office_code", conn)


# --- 加算マスタ ---------------------------------------------------------

def replace_addition_master(conn, office_code: str, rows: list[dict]) -> None:
    """指定事業所の加算マスタを丸ごと置き換える。"""
    conn.execute("DELETE FROM addition_master WHERE office_code = ?", (office_code,))
    for row in rows:
        conn.execute(
            """
            INSERT INTO addition_master
                (office_code, addition_code, addition_name, category, unit, valid_from, valid_to, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                office_code,
                row.get("addition_code"),
                row.get("addition_name"),
                row.get("category") or "個別",
                row.get("unit"),
                row.get("valid_from"),
                row.get("valid_to"),
                row.get("note"),
            ),
        )
    conn.commit()


def get_addition_master(conn, office_code: str | None = None) -> pd.DataFrame:
    if office_code:
        return pd.read_sql(
            "SELECT * FROM addition_master WHERE office_code = ? ORDER BY addition_code",
            conn, params=(office_code,),
        )
    return pd.read_sql("SELECT * FROM addition_master ORDER BY office_code, addition_code", conn)


def update_addition_category(conn, addition_id: int, category: str) -> None:
    conn.execute("UPDATE addition_master SET category = ? WHERE id = ?", (category, addition_id))
    conn.commit()


def delete_addition_master_row(conn, addition_id: int) -> None:
    conn.execute("DELETE FROM addition_master WHERE id = ?", (addition_id,))
    conn.commit()


# --- レセプト請求明細 ----------------------------------------------------

def create_import_batch(conn, file_type: str, filename: str, target_month: str = "") -> int:
    cur = conn.execute(
        "INSERT INTO import_batches (file_type, filename, target_month) VALUES (?, ?, ?)",
        (file_type, filename, target_month),
    )
    conn.commit()
    return cur.lastrowid


def delete_receipts_for_month(conn, target_month: str) -> None:
    conn.execute("DELETE FROM receipts WHERE target_month = ?", (target_month,))
    conn.commit()


def save_receipts(conn, batch_id: int, rows: list[dict]) -> None:
    for row in rows:
        conn.execute(
            """
            INSERT INTO receipts
                (batch_id, office_code, office_name, user_code, user_name,
                 target_month, addition_code, addition_name, unit, count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                row.get("office_code"),
                row.get("office_name"),
                row.get("user_code"),
                row.get("user_name"),
                row.get("target_month"),
                row.get("addition_code"),
                row.get("addition_name"),
                row.get("unit"),
                row.get("count"),
            ),
        )
    conn.commit()


def get_receipts(conn, office_code: str | None = None, target_month: str | None = None) -> pd.DataFrame:
    query = "SELECT * FROM receipts WHERE 1=1"
    params = []
    if office_code:
        query += " AND office_code = ?"
        params.append(office_code)
    if target_month:
        query += " AND target_month = ?"
        params.append(target_month)
    return pd.read_sql(query, conn, params=params)


def list_target_months(conn) -> list[str]:
    df = pd.read_sql(
        "SELECT DISTINCT target_month FROM receipts WHERE target_month IS NOT NULL AND target_month != '' "
        "ORDER BY target_month DESC",
        conn,
    )
    return df["target_month"].tolist()


# --- 列マッピング設定 ----------------------------------------------------

def save_mapping(conn, file_type: str, header_signature: str, mapping: dict) -> None:
    conn.execute(
        """
        INSERT INTO column_mappings (file_type, header_signature, mapping_json)
        VALUES (?, ?, ?)
        ON CONFLICT(file_type, header_signature) DO UPDATE SET
            mapping_json = excluded.mapping_json,
            saved_at = CURRENT_TIMESTAMP
        """,
        (file_type, header_signature, json.dumps(mapping, ensure_ascii=False)),
    )
    conn.commit()


def load_mapping(conn, file_type: str, header_signature: str) -> dict | None:
    cur = conn.execute(
        "SELECT mapping_json FROM column_mappings WHERE file_type = ? AND header_signature = ?",
        (file_type, header_signature),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return json.loads(row[0])
