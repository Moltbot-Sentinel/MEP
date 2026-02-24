import sqlite3
from typing import Optional

DB_FILE = "ledger.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # Drop existing table to upgrade schema for Crypto Auth
    cursor.execute("DROP TABLE IF EXISTS ledger")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ledger (
            node_id TEXT PRIMARY KEY,
            pub_pem TEXT NOT NULL,
            balance REAL NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

def register_node(node_id: str, pub_pem: str) -> float:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM ledger WHERE node_id = ?", (node_id,))
    row = cursor.fetchone()
    if not row:
        cursor.execute("INSERT INTO ledger (node_id, pub_pem, balance) VALUES (?, ?, ?)", (node_id, pub_pem, 10.0))
        conn.commit()
        balance = 10.0
    else:
        balance = row[0]
    conn.close()
    return balance

def get_pub_pem(node_id: str) -> Optional[str]:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT pub_pem FROM ledger WHERE node_id = ?", (node_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

def get_balance(node_id: str) -> Optional[float]:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM ledger WHERE node_id = ?", (node_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

def set_balance(node_id: str, balance: float):
    # This is mainly for testing now
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE ledger SET balance = ? WHERE node_id = ?", (balance, node_id))
    conn.commit()
    conn.close()

def add_balance(node_id: str, amount: float):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE ledger SET balance = balance + ? WHERE node_id = ?", (amount, node_id))
    conn.commit()
    conn.close()

def deduct_balance(node_id: str, amount: float) -> bool:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM ledger WHERE node_id = ?", (node_id,))
    row = cursor.fetchone()
    if row is None or row[0] < amount:
        conn.close()
        return False
        
    cursor.execute("UPDATE ledger SET balance = balance - ? WHERE node_id = ?", (amount, node_id))
    conn.commit()
    conn.close()
    return True

init_db()