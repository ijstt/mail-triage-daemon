"""
Lightweight SQLite state storage to prevent duplicate notifications
and cache recent OTP codes for Antigravity MCP integration.
"""

import sqlite3
import os
import time
from typing import Optional, Dict, Any, List

DB_PATH = os.environ.get("MAIL_TRIAGE_DB", os.path.expanduser("~/.config/mail-triage/state.db"))

def init_db(db_path: Optional[str] = None):
    target_path = db_path or DB_PATH
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    conn = sqlite3.connect(target_path)
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_messages (
                account_id TEXT,
                message_id TEXT,
                processed_at INTEGER,
                category TEXT,
                subject TEXT,
                PRIMARY KEY (account_id, message_id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_processed_at ON processed_messages (processed_at)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS otp_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT,
                service TEXT,
                code TEXT,
                received_at INTEGER,
                subject TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_otp_received_at ON otp_cache (received_at)
        """)
    conn.close()

def is_processed(account_id: str, message_id: str, db_path: Optional[str] = None) -> bool:
    if not message_id:
        return False
    target_path = db_path or DB_PATH
    conn = sqlite3.connect(target_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM processed_messages WHERE account_id = ? AND message_id = ?",
        (account_id, message_id)
    )
    result = cur.fetchone() is not None
    conn.close()
    return result

def mark_processed(account_id: str, message_id: str, category: str, subject: str, db_path: Optional[str] = None):
    if not message_id:
        return
    target_path = db_path or DB_PATH
    conn = sqlite3.connect(target_path)
    with conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO processed_messages (account_id, message_id, processed_at, category, subject)
            VALUES (?, ?, ?, ?, ?)
            """,
            (account_id, message_id, int(time.time()), category, subject[:255])
        )
    conn.close()

def record_otp(account_id: str, service: str, code: str, subject: str, db_path: Optional[str] = None):
    target_path = db_path or DB_PATH
    conn = sqlite3.connect(target_path)
    with conn:
        conn.execute(
            """
            INSERT INTO otp_cache (account_id, service, code, received_at, subject)
            VALUES (?, ?, ?, ?, ?)
            """,
            (account_id, service, code, int(time.time()), subject[:255])
        )
    conn.close()

def get_latest_otp(service: Optional[str] = None, max_age_seconds: int = 900, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieve the most recent OTP received within max_age_seconds (default 15m)."""
    target_path = db_path or DB_PATH
    min_time = int(time.time()) - max_age_seconds
    conn = sqlite3.connect(target_path)
    cur = conn.cursor()

    if service:
        cur.execute(
            """
            SELECT account_id, service, code, received_at, subject
            FROM otp_cache
            WHERE received_at >= ? AND LOWER(service) LIKE ?
            ORDER BY received_at DESC LIMIT 1
            """,
            (min_time, f"%{service.lower()}%")
        )
    else:
        cur.execute(
            """
            SELECT account_id, service, code, received_at, subject
            FROM otp_cache
            WHERE received_at >= ?
            ORDER BY received_at DESC LIMIT 1
            """,
            (min_time,)
        )

    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "account_id": row[0],
        "service": row[1],
        "code": row[2],
        "received_at": row[3],
        "age_seconds": int(time.time()) - row[3],
        "subject": row[4]
    }

def get_recent_messages(
    limit: int = 10,
    category: Optional[str] = None,
    db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Retrieve recent processed messages across all monitored inboxes."""
    target_path = db_path or DB_PATH
    conn = sqlite3.connect(target_path)
    cur = conn.cursor()
    if category:
        cur.execute(
            """
            SELECT account_id, message_id, processed_at, category, subject
            FROM processed_messages
            WHERE UPPER(category) = UPPER(?)
            ORDER BY processed_at DESC LIMIT ?
            """,
            (category, limit)
        )
    else:
        cur.execute(
            """
            SELECT account_id, message_id, processed_at, category, subject
            FROM processed_messages
            ORDER BY processed_at DESC LIMIT ?
            """,
            (limit,)
        )
    rows = cur.fetchall()
    conn.close()

    now = int(time.time())
    results = []
    for r in rows:
        results.append({
            "account_id": r[0],
            "message_id": r[1],
            "processed_at": r[2],
            "age_seconds": now - r[2],
            "category": r[3],
            "subject": r[4]
        })
    return results
