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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS account_sync_state (
                account_id TEXT PRIMARY KEY,
                last_seen_uid INTEGER,
                last_sync_at INTEGER
            )
        """)
    conn.close()
    prune_old_records(db_path=target_path)

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
        pattern = f"%{service.lower()}%"
        cur.execute(
            """
            SELECT account_id, service, code, received_at, subject
            FROM otp_cache
            WHERE received_at >= ? AND (LOWER(service) LIKE ? OR LOWER(subject) LIKE ?)
            ORDER BY received_at DESC LIMIT 1
            """,
            (min_time, pattern, pattern)
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

def get_last_seen_uid(account_id: str, db_path: Optional[str] = None) -> int:
    """Retrieve the highest processed UID for an account."""
    target_path = db_path or DB_PATH
    conn = sqlite3.connect(target_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT last_seen_uid FROM account_sync_state WHERE account_id = ?",
        (account_id,)
    )
    row = cur.fetchone()
    conn.close()
    return int(row[0]) if row and row[0] is not None else 0

def update_last_seen_uid(account_id: str, uid: int, db_path: Optional[str] = None):
    """Update the highest processed UID for an account (monotonically non-decreasing)."""
    target_path = db_path or DB_PATH
    conn = sqlite3.connect(target_path)
    with conn:
        conn.execute(
            """
            INSERT INTO account_sync_state (account_id, last_seen_uid, last_sync_at)
            VALUES (?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                last_seen_uid = MAX(account_sync_state.last_seen_uid, excluded.last_seen_uid),
                last_sync_at = excluded.last_sync_at
            """,
            (account_id, int(uid), int(time.time()))
        )
    conn.close()

def prune_old_records(max_otp_days: int = 14, max_msg_days: int = 60, db_path: Optional[str] = None):
    """Clean up expired OTP codes and ancient processed message records to prevent database bloat."""
    target_path = db_path or DB_PATH
    if not os.path.exists(target_path):
        return
    now = int(time.time())
    otp_threshold = now - (max_otp_days * 86400)
    msg_threshold = now - (max_msg_days * 86400)
    try:
        conn = sqlite3.connect(target_path)
        with conn:
            conn.execute("DELETE FROM otp_cache WHERE received_at < ?", (otp_threshold,))
            conn.execute("DELETE FROM processed_messages WHERE processed_at < ?", (msg_threshold,))
        conn.close()
    except Exception:
        pass

