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

    # 같은 영상 중복 pending/processing 정리
    active_rows = cur.execute("""
        SELECT video_id, job_id FROM jobs
        WHERE status IN ('pending', 'processing')
        ORDER BY video_id, updated_at DESC, created_at DESC, job_id DESC
    """).fetchall()

    seen_video_ids = set()
    stale_job_ids = []
    for row in active_rows:
        video_id = row["video_id"]
        if video_id in seen_video_ids:
            stale_job_ids.append((row["job_id"],))
            continue
        seen_video_ids.add(video_id)

    if stale_job_ids:
        cur.executemany("""
            UPDATE jobs SET status='failed', progress=0,
            message='중복 작업 정리됨', updated_at=datetime('now')
            WHERE job_id=?
        """, stale_job_ids)

    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_active_video
        ON jobs(video_id)
        WHERE status IN ('pending', 'processing')
    """)

    # 7일 지난 캐시 자동 만료
    cur.execute("""
        DELETE FROM analysis_cache
        WHERE analyzed_at < datetime('now', '-7 days')
    """)

    conn.commit()
    conn.close()