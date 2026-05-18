import sqlite3
import os

DB_PATH = os.getenv("DB_PATH", "analyzer.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS analysis_cache (
            video_id      TEXT PRIMARY KEY,
            video_title   TEXT,
            result_json   TEXT NOT NULL,
            comment_count INTEGER,
            analyzed_at   TEXT DEFAULT (datetime('now'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            job_id      TEXT PRIMARY KEY,
            video_id    TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending',
            progress    INTEGER DEFAULT 0,
            message     TEXT,
            created_at  TEXT DEFAULT (datetime('now')),
            updated_at  TEXT DEFAULT (datetime('now'))
        )
    """)

    # 7일 지난 캐시 자동 만료
    cur.execute("""
        DELETE FROM analysis_cache
        WHERE analyzed_at < datetime('now', '-7 days')
    """)

    conn.commit()
    conn.close()
