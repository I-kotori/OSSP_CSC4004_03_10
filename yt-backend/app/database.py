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

    cur.execute("""
        CREATE TABLE IF NOT EXISTS embedding_cache (
            text_hash   TEXT NOT NULL,
            model_name  TEXT NOT NULL,
            dim         INTEGER NOT NULL,
            embedding   BLOB NOT NULL,
            text_preview TEXT,
            hit_count   INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT (datetime('now')),
            updated_at  TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (text_hash, model_name)
        )
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_embedding_cache_model
        ON embedding_cache(model_name, updated_at)
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

    # 서버 재시작 시 메모리 큐는 사라지므로 DB에 남은 active job은 복구할 수 없다.
    # 그대로 두면 프론트가 죽은 job_id를 계속 폴링하므로 failed로 정리한다.
    cur.execute("""
        UPDATE jobs
        SET status='failed',
            progress=0,
            message='서버 재시작으로 중단됨. 다시 분석을 요청해주세요.',
            updated_at=datetime('now')
        WHERE status IN ('pending', 'processing')
    """)

    # 7일 지난 캐시 자동 만료
    cur.execute("""
        DELETE FROM analysis_cache
        WHERE analyzed_at < datetime('now', '-7 days')
    """)

    cur.execute("""
        DELETE FROM embedding_cache
        WHERE updated_at < datetime('now', '-30 days')
    """)

    conn.commit()
    conn.close()
