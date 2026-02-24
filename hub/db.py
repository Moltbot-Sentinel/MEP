import sqlite3
import os
from typing import Optional

DB_FILE = "ledger.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ledger (
            node_id TEXT PRIMARY KEY,
            balance REAL NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

def get_balance(node_id: str) -> Optional[float]:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM ledger WHERE node_id = ?", (node_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return row[0]
    return None

def set_balance(node_id: str, balance: float):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO ledger (node_id, balance)
        VALUES (?, ?)
        ON CONFLICT(node_id) DO UPDATE SET balance = excluded.balance
    ''', (node_id, balance))
    conn.commit()
    conn.close()

def add_balance(node_id: str, amount: float):
    # Atomic addition
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # Ensure node exists first
    cursor.execute("SELECT balance FROM ledger WHERE node_id = ?", (node_id,))
    row = cursor.fetchone()
    if row is None:
        # Should not happen if correctly initialized, but fallback to amount
        cursor.execute("INSERT INTO ledger (node_id, balance) VALUES (?, ?)", (node_id, amount))
    else:
        cursor.execute("UPDATE ledger SET balance = balance + ? WHERE node_id = ?", (amount, node_id))
    conn.commit()
    conn.close()

def deduct_balance(node_id: str, amount: float) -> bool:
    # Atomic deduction, returns True if successful, False if insufficient balance
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

# Initialize database on module import
init_db()
