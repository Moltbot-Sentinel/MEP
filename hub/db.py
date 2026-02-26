import sqlite3
import os
import json
from typing import Optional

try:
    import psycopg2
    from psycopg2 import pool
except ImportError:
    psycopg2 = None

DB_FILE = os.getenv("MEP_SQLITE_PATH", "ledger.db")
DB_URL = os.getenv("MEP_DATABASE_URL")
PG_POOL_MIN = int(os.getenv("MEP_PG_POOL_MIN", "1"))
PG_POOL_MAX = int(os.getenv("MEP_PG_POOL_MAX", "5"))
_pg_pool: Optional["pool.SimpleConnectionPool"] = None

def _is_postgres() -> bool:
    return bool(DB_URL)

def _get_pg_pool():
    global _pg_pool
    if _pg_pool is None:
        if psycopg2 is None:
            raise RuntimeError("psycopg2 is required for Postgres")
        _pg_pool = pool.SimpleConnectionPool(PG_POOL_MIN, PG_POOL_MAX, DB_URL)
    return _pg_pool

def _get_conn():
    if _is_postgres():
        return _get_pg_pool().getconn()
    return sqlite3.connect(DB_FILE, check_same_thread=False)

def _release_conn(conn):
    if _is_postgres():
        _get_pg_pool().putconn(conn)
    else:
        conn.close()

def _row_to_dict(cursor, row):
    if row is None:
        return None
    columns = [desc[0] for desc in cursor.description]
    return dict(zip(columns, row))

def init_db():
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ledger (
            node_id TEXT PRIMARY KEY,
            pub_pem TEXT NOT NULL,
            balance REAL NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            consumer_id TEXT NOT NULL,
            provider_id TEXT,
            payload TEXT NOT NULL,
            bounty REAL NOT NULL,
            status TEXT NOT NULL,
            target_node TEXT,
            model_requirement TEXT,
            result_payload TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS idempotency (
            node_id TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            idem_key TEXT NOT NULL,
            response TEXT NOT NULL,
            status_code INTEGER NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (node_id, endpoint, idem_key)
        )
    ''')
    conn.commit()
    _release_conn(conn)

def register_node(node_id: str, pub_pem: str) -> float:
    conn = _get_conn()
    cursor = conn.cursor()
    if _is_postgres():
        cursor.execute("SELECT balance FROM ledger WHERE node_id = %s", (node_id,))
    else:
        cursor.execute("SELECT balance FROM ledger WHERE node_id = ?", (node_id,))
    row = cursor.fetchone()
    if not row:
        if _is_postgres():
            cursor.execute(
                "INSERT INTO ledger (node_id, pub_pem, balance) VALUES (%s, %s, %s) ON CONFLICT (node_id) DO NOTHING",
                (node_id, pub_pem, 10.0)
            )
        else:
            cursor.execute(
                "INSERT OR IGNORE INTO ledger (node_id, pub_pem, balance) VALUES (?, ?, ?)",
                (node_id, pub_pem, 10.0)
            )
        conn.commit()
    if _is_postgres():
        cursor.execute("SELECT balance FROM ledger WHERE node_id = %s", (node_id,))
    else:
        cursor.execute("SELECT balance FROM ledger WHERE node_id = ?", (node_id,))
    row = cursor.fetchone()
    _release_conn(conn)
    return row[0] if row else 10.0

def get_pub_pem(node_id: str) -> Optional[str]:
    conn = _get_conn()
    cursor = conn.cursor()
    if _is_postgres():
        cursor.execute("SELECT pub_pem FROM ledger WHERE node_id = %s", (node_id,))
    else:
        cursor.execute("SELECT pub_pem FROM ledger WHERE node_id = ?", (node_id,))
    row = cursor.fetchone()
    _release_conn(conn)
    return row[0] if row else None

def get_balance(node_id: str) -> Optional[float]:
    conn = _get_conn()
    cursor = conn.cursor()
    if _is_postgres():
        cursor.execute("SELECT balance FROM ledger WHERE node_id = %s", (node_id,))
    else:
        cursor.execute("SELECT balance FROM ledger WHERE node_id = ?", (node_id,))
    row = cursor.fetchone()
    _release_conn(conn)
    return row[0] if row else None

def set_balance(node_id: str, balance: float):
    conn = _get_conn()
    cursor = conn.cursor()
    if _is_postgres():
        cursor.execute("UPDATE ledger SET balance = %s WHERE node_id = %s", (balance, node_id))
    else:
        cursor.execute("UPDATE ledger SET balance = ? WHERE node_id = ?", (balance, node_id))
    conn.commit()
    _release_conn(conn)

def add_balance(node_id: str, amount: float):
    conn = _get_conn()
    cursor = conn.cursor()
    if _is_postgres():
        cursor.execute("UPDATE ledger SET balance = balance + %s WHERE node_id = %s", (amount, node_id))
    else:
        cursor.execute("UPDATE ledger SET balance = balance + ? WHERE node_id = ?", (amount, node_id))
    conn.commit()
    _release_conn(conn)

def deduct_balance(node_id: str, amount: float) -> bool:
    conn = _get_conn()
    cursor = conn.cursor()
    if _is_postgres():
        cursor.execute(
            "UPDATE ledger SET balance = balance - %s WHERE node_id = %s AND balance >= %s",
            (amount, node_id, amount)
        )
    else:
        cursor.execute(
            "UPDATE ledger SET balance = balance - ? WHERE node_id = ? AND balance >= ?",
            (amount, node_id, amount)
        )
    updated = cursor.rowcount
    conn.commit()
    _release_conn(conn)
    return updated > 0

def create_task(task_id: str, consumer_id: str, payload: str, bounty: float, status: str, target_node: Optional[str], model_requirement: Optional[str], created_at: float):
    conn = _get_conn()
    cursor = conn.cursor()
    if _is_postgres():
        cursor.execute(
            "INSERT INTO tasks (task_id, consumer_id, provider_id, payload, bounty, status, target_node, model_requirement, result_payload, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (task_id, consumer_id, None, payload, bounty, status, target_node, model_requirement, None, created_at, created_at)
        )
    else:
        cursor.execute(
            "INSERT INTO tasks (task_id, consumer_id, provider_id, payload, bounty, status, target_node, model_requirement, result_payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, consumer_id, None, payload, bounty, status, target_node, model_requirement, None, created_at, created_at)
        )
    conn.commit()
    _release_conn(conn)

def update_task_assignment(task_id: str, provider_id: str, status: str, updated_at: float):
    conn = _get_conn()
    cursor = conn.cursor()
    if _is_postgres():
        cursor.execute(
            "UPDATE tasks SET provider_id = %s, status = %s, updated_at = %s WHERE task_id = %s",
            (provider_id, status, updated_at, task_id)
        )
    else:
        cursor.execute(
            "UPDATE tasks SET provider_id = ?, status = ?, updated_at = ? WHERE task_id = ?",
            (provider_id, status, updated_at, task_id)
        )
    conn.commit()
    _release_conn(conn)

def update_task_result(task_id: str, provider_id: str, result_payload: str, status: str, updated_at: float):
    conn = _get_conn()
    cursor = conn.cursor()
    if _is_postgres():
        cursor.execute(
            "UPDATE tasks SET provider_id = %s, result_payload = %s, status = %s, updated_at = %s WHERE task_id = %s",
            (provider_id, result_payload, status, updated_at, task_id)
        )
    else:
        cursor.execute(
            "UPDATE tasks SET provider_id = ?, result_payload = ?, status = ?, updated_at = ? WHERE task_id = ?",
            (provider_id, result_payload, status, updated_at, task_id)
        )
    conn.commit()
    _release_conn(conn)

def update_task_status(task_id: str, status: str, updated_at: float):
    conn = _get_conn()
    cursor = conn.cursor()
    if _is_postgres():
        cursor.execute(
            "UPDATE tasks SET status = %s, provider_id = NULL, updated_at = %s WHERE task_id = %s",
            (status, updated_at, task_id)
        )
    else:
        cursor.execute(
            "UPDATE tasks SET status = ?, provider_id = NULL, updated_at = ? WHERE task_id = ?",
            (status, updated_at, task_id)
        )
    conn.commit()
    _release_conn(conn)

def assign_task_if_open(task_id: str, provider_id: str, updated_at: float) -> bool:
    conn = _get_conn()
    cursor = conn.cursor()
    if _is_postgres():
        cursor.execute(
            "UPDATE tasks SET provider_id = %s, status = 'assigned', updated_at = %s WHERE task_id = %s AND status = 'bidding' AND provider_id IS NULL",
            (provider_id, updated_at, task_id)
        )
    else:
        cursor.execute(
            "UPDATE tasks SET provider_id = ?, status = 'assigned', updated_at = ? WHERE task_id = ? AND status = 'bidding' AND provider_id IS NULL",
            (provider_id, updated_at, task_id)
        )
    updated = cursor.rowcount
    conn.commit()
    _release_conn(conn)
    return updated > 0

def cancel_task_if_open(task_id: str, updated_at: float) -> bool:
    conn = _get_conn()
    cursor = conn.cursor()
    if _is_postgres():
        cursor.execute(
            "UPDATE tasks SET status = 'cancelled', provider_id = NULL, updated_at = %s WHERE task_id = %s AND status = 'bidding' AND provider_id IS NULL",
            (updated_at, task_id)
        )
    else:
        cursor.execute(
            "UPDATE tasks SET status = 'cancelled', provider_id = NULL, updated_at = ? WHERE task_id = ? AND status = 'bidding' AND provider_id IS NULL",
            (updated_at, task_id)
        )
    updated = cursor.rowcount
    conn.commit()
    _release_conn(conn)
    return updated > 0

def get_task(task_id: str) -> Optional[dict]:
    conn = _get_conn()
    if not _is_postgres():
        conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    if _is_postgres():
        cursor.execute("SELECT * FROM tasks WHERE task_id = %s", (task_id,))
    else:
        cursor.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
    row = cursor.fetchone()
    if not row:
        _release_conn(conn)
        return None
    if _is_postgres():
        result = _row_to_dict(cursor, row)
        _release_conn(conn)
        return result
    result = dict(row)
    _release_conn(conn)
    return result

def get_active_tasks() -> list:
    conn = _get_conn()
    if not _is_postgres():
        conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    if _is_postgres():
        cursor.execute("SELECT * FROM tasks WHERE status IN ('bidding', 'assigned')")
    else:
        cursor.execute("SELECT * FROM tasks WHERE status IN ('bidding', 'assigned')")
    rows = cursor.fetchall()
    if _is_postgres():
        result = [_row_to_dict(cursor, row) for row in rows]
        _release_conn(conn)
        return result
    result = [dict(row) for row in rows]
    _release_conn(conn)
    return result

def get_idempotency(node_id: str, endpoint: str, idem_key: str) -> Optional[dict]:
    conn = _get_conn()
    cursor = conn.cursor()
    if _is_postgres():
        cursor.execute(
            "SELECT response, status_code FROM idempotency WHERE node_id = %s AND endpoint = %s AND idem_key = %s",
            (node_id, endpoint, idem_key)
        )
    else:
        cursor.execute(
            "SELECT response, status_code FROM idempotency WHERE node_id = ? AND endpoint = ? AND idem_key = ?",
            (node_id, endpoint, idem_key)
        )
    row = cursor.fetchone()
    if not row:
        _release_conn(conn)
        return None
    response = json.loads(row[0])
    result = {"response": response, "status_code": row[1]}
    _release_conn(conn)
    return result

def set_idempotency(node_id: str, endpoint: str, idem_key: str, response: dict, status_code: int, created_at: float):
    conn = _get_conn()
    cursor = conn.cursor()
    payload = json.dumps(response)
    if _is_postgres():
        cursor.execute(
            "INSERT INTO idempotency (node_id, endpoint, idem_key, response, status_code, created_at) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (node_id, endpoint, idem_key) DO NOTHING",
            (node_id, endpoint, idem_key, payload, status_code, created_at)
        )
    else:
        cursor.execute(
            "INSERT OR IGNORE INTO idempotency (node_id, endpoint, idem_key, response, status_code, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (node_id, endpoint, idem_key, payload, status_code, created_at)
        )
    conn.commit()
    _release_conn(conn)

init_db()
