"""
분석 파이프라인

단계:
  1. fetch_comments()       : YouTube Data API로 댓글 수집
  2. clean_comments()       : 텍스트 정제 (특수문자, 자음/모음 제거)
  3. embed_comments()       : jhgan/ko-sroberta-multitask 임베딩 (CPU)
  4. find_best_eps()        : k-distance graph로 최적 eps 자동 탐색
  5. cluster_comments()     : DBSCAN 군집화
  6. label_clusters()       : gemma4:e4b (Ollama 로컬)로 라벨 생성
  7. merge_small_clusters() : 군집 많으면 target=4 내외로 합치기
  8. build_timeline()       : published_at 기준 시간대별 비율 계산
  9. 결과 SQLite 캐시 저장

"""

import os
import re
import json
import time
import uuid
import queue
import hashlib
import logging
import sqlite3
import threading
import requests
import numpy as np
from datetime import datetime, timezone
from collections import defaultdict

from app.database import get_conn
from app.dbscan_scratch import fit_predict_cosine_dbscan
from app.logging_config import configure_app_logging

configure_app_logging()
logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL", "gemma4:e4b")
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "jhgan/ko-sroberta-multitask")
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "32"))
EMBEDDING_CACHE_ENABLED = os.getenv("EMBEDDING_CACHE_ENABLED", "1").lower() not in ("0", "false", "no")
EMBEDDING_CACHE_CHUNK_SIZE = int(os.getenv("EMBEDDING_CACHE_CHUNK_SIZE", "500"))
MIN_COMMENT_CHARS = int(os.getenv("MIN_COMMENT_CHARS", "3"))

_embedding_model = None
_embedding_model_lock = threading.Lock()
_embedding_cache_table_ready = False
_embedding_cache_lock = threading.Lock()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize_clusters(clusters: list) -> list:
    return [{k: v for k, v in c.items() if k != "source_ids"} for c in clusters]


def _embedding_text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ensure_embedding_cache_table():
    global _embedding_cache_table_ready
    if _embedding_cache_table_ready:
        return

    with _embedding_cache_lock:
        if _embedding_cache_table_ready:
            return

        conn = get_conn()
        conn.execute("""
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
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_embedding_cache_model
            ON embedding_cache(model_name, updated_at)
        """)
        conn.commit()
        conn.close()
        _embedding_cache_table_ready = True


def _load_cached_embeddings(text_hashes: list[str]) -> dict[str, np.ndarray]:
    if not text_hashes:
        return {}

    _ensure_embedding_cache_table()
    cached = {}
    unique_hashes = list(dict.fromkeys(text_hashes))
    conn = get_conn()
    try:
        for start in range(0, len(unique_hashes), EMBEDDING_CACHE_CHUNK_SIZE):
            chunk = unique_hashes[start:start + EMBEDDING_CACHE_CHUNK_SIZE]
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"""
                SELECT text_hash, dim, embedding
                FROM embedding_cache
                WHERE model_name = ?
                  AND text_hash IN ({placeholders})
                """,
                (EMBEDDING_MODEL_NAME, *chunk),
            ).fetchall()
            for row in rows:
                vector = np.frombuffer(row["embedding"], dtype=np.float32)
                if int(row["dim"]) != int(vector.size):
                    logger.warning(
                        "[EmbeddingCache] 손상된 캐시 무시: hash=%s dim=%s actual=%s",
                        row["text_hash"][:12],
                        row["dim"],
                        vector.size,
                    )
                    continue
                cached[row["text_hash"]] = vector.copy()

        if cached:
            conn.executemany(
                """
                UPDATE embedding_cache
                SET hit_count = hit_count + 1,
                    updated_at = datetime('now')
                WHERE text_hash = ? AND model_name = ?
                """,
                [(text_hash, EMBEDDING_MODEL_NAME) for text_hash in cached.keys()],
            )
            conn.commit()
    finally:
        conn.close()

    return cached


def _save_cached_embeddings(items: list[tuple[str, str, np.ndarray]]):
    if not items:
        return

    _ensure_embedding_cache_table()
    conn = get_conn()
    try:
        conn.executemany(
            """
            INSERT INTO embedding_cache
                (text_hash, model_name, dim, embedding, text_preview, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(text_hash, model_name) DO UPDATE SET
                dim = excluded.dim,
                embedding = excluded.embedding,
                text_preview = excluded.text_preview,
                updated_at = datetime('now')
            """,
            [
                (
                    text_hash,
                    EMBEDDING_MODEL_NAME,
                    int(vector.size),
                    sqlite3.Binary(np.ascontiguousarray(vector, dtype=np.float32).tobytes()),
                    text[:120],
                )
                for text_hash, text, vector in items
            ],
        )
        conn.commit()
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# 1. 댓글 수집
# ─────────────────────────────────────────────────────────────

def fetch_comments(video_id: str) -> list:
    from googleapiclient.discovery import build
    api_key = os.getenv("YOUTUBE_API_KEY")
    youtube = build("youtube", "v3", developerKey=api_key)
    comments_data, page_token = [], None
    seen_comment_ids = set()

    def append_comment(comment_id: str, snippet: dict, *, parent_id: str = "", parent_text: str = "", is_reply: bool = False):
        if comment_id and comment_id in seen_comment_ids:
            return
        if comment_id:
            seen_comment_ids.add(comment_id)

        comments_data.append({
            "comment_id":   comment_id or "",
            "parent_id":    parent_id or "",
            "parent_text":  parent_text or "",
            "is_reply":     is_reply,
            "author":       snippet.get("authorDisplayName", ""),
            "text":         snippet.get("textOriginal", ""),
            "likes":        snippet.get("likeCount", 0),
            "published_at": snippet.get("publishedAt", ""),
        })

    while True:
        response = youtube.commentThreads().list(
            part="snippet,replies",  # replies 추가
            videoId=video_id,
            maxResults=100,
            pageToken=page_token,
        ).execute()

        for item in response.get("items", []):
            # 최상위 댓글
            top_comment = item["snippet"]["topLevelComment"]
            top_comment_id = top_comment.get("id", item.get("id", ""))
            s = top_comment["snippet"]
            top_text = s.get("textOriginal", "")
            append_comment(top_comment_id, s)

            # 대댓글 (replies가 있으면)
            reply_count = item["snippet"].get("totalReplyCount", 0)
            replies = item.get("replies", {}).get("comments", [])

            if replies:
                # replies에 포함된 대댓글 (최대 5개)
                for reply in replies:
                    r = reply["snippet"]
                    append_comment(
                        reply.get("id", ""),
                        r,
                        parent_id=top_comment_id,
                        parent_text=top_text,
                        is_reply=True,
                    )

            # 대댓글이 5개 초과면 별도 API 호출 필요
            if reply_count > len(replies):
                reply_page_token = None
                while True:
                    reply_response = youtube.comments().list(
                        part="snippet",
                        parentId=top_comment_id,
                        maxResults=100,
                        pageToken=reply_page_token,
                    ).execute()
                    for reply in reply_response.get("items", []):
                        r = reply["snippet"]
                        append_comment(
                            reply.get("id", ""),
                            r,
                            parent_id=top_comment_id,
                            parent_text=top_text,
                            is_reply=True,
                        )
                    reply_page_token = reply_response.get("nextPageToken")
                    if not reply_page_token:
                        break

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return comments_data


def fetch_video_title(video_id: str) -> str:
    from googleapiclient.discovery import build
    fallback = f"영상 ({video_id})"
    try:
        api_key = os.getenv("YOUTUBE_API_KEY")
        if not api_key:
            return fallback

        youtube = build("youtube", "v3", developerKey=api_key)
        response = youtube.videos().list(
            part="snippet",
            id=video_id,
            maxResults=1,
        ).execute()
        items = response.get("items", [])
        if not items:
            return fallback

        return items[0].get("snippet", {}).get("title") or fallback
    except Exception:
        logger.exception("[YouTube] 영상 제목 조회 실패: video_id=%s", video_id)
        return fallback


# ─────────────────────────────────────────────────────────────
# 2. 텍스트 정제
# ─────────────────────────────────────────────────────────────

URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
PHONE_RE = re.compile(r"\b\d{2,4}[-.\s]?\d{3,4}[-.\s]?\d{4}\b")
SPAM_KEYWORDS = ("무료체험", "오픈채팅", "텔레그램", "카톡", "바카라", "도박")


def _clean_comment_text(raw: str) -> str:
    raw = raw or ""
    without_url = URL_RE.sub(" ", raw)
    cleaned = re.sub(r"[^가-힣a-zA-Z0-9\s]", " ", without_url)
    cleaned = re.sub(r"[ㄱ-ㅎㅏ-ㅣ]+", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _is_low_value_comment(raw: str, cleaned: str) -> bool:
    compact = re.sub(r"\s+", "", cleaned)
    if len(compact) < MIN_COMMENT_CHARS:
        return True
    if URL_RE.search(raw or "") or PHONE_RE.search(raw or ""):
        return True
    if len(set(compact)) <= 1 and len(compact) >= 3:
        return True

    lowered = (raw or "").lower()
    return any(keyword in lowered for keyword in SPAM_KEYWORDS)


def clean_comments(comments_data: list) -> tuple:
    texts, meta = [], []
    seen = set()  # 추가, 중복 확인
    stats = {
        "raw_count": len(comments_data),
        "kept_count": 0,
        "filtered_count": 0,
        "duplicate_count": 0,
        "reply_context_count": 0,
    }

    for item in comments_data:
        raw = item.get("text", "")
        cleaned = _clean_comment_text(raw)
        if not cleaned or _is_low_value_comment(raw, cleaned):
            stats["filtered_count"] += 1
            continue

        parent_cleaned = _clean_comment_text(item.get("parent_text", ""))
        if item.get("is_reply") and parent_cleaned:
            embedding_text = f"부모댓글 {parent_cleaned} 대댓글 {cleaned}"
            stats["reply_context_count"] += 1
        else:
            embedding_text = cleaned

        dedupe_key = embedding_text if item.get("is_reply") else cleaned
        if dedupe_key in seen:
            stats["duplicate_count"] += 1
            continue

        seen.add(dedupe_key)
        enriched = dict(item)
        enriched["cleaned_text"] = cleaned
        enriched["embedding_text"] = embedding_text
        texts.append(cleaned)
        meta.append(enriched)

    stats["kept_count"] = len(texts)
    clean_comments.last_stats = stats
    logger.info(
        "[Clean] "
        f"raw={stats['raw_count']}, kept={stats['kept_count']}, "
        f"filtered={stats['filtered_count']}, duplicates={stats['duplicate_count']}, "
        f"reply_context={stats['reply_context_count']}"
    )
    return texts, meta


clean_comments.last_stats = {}


# ─────────────────────────────────────────────────────────────
# 3. 임베딩
# ─────────────────────────────────────────────────────────────

def get_embedding_model():
    global _embedding_model
    if _embedding_model is not None:
        return _embedding_model

    with _embedding_model_lock:
        if _embedding_model is None:
            from sentence_transformers import SentenceTransformer
            started = time.time()
            logger.info("[Embedding] 모델 로드 시작: %s", EMBEDDING_MODEL_NAME)
            _embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
            logger.info("[Embedding] 모델 로드 완료 (%.1fs)", time.time() - started)
    return _embedding_model


def embed_comments(texts: list):
    """
    jhgan/ko-sroberta-multitask 모델로 텍스트 벡터화.
    CPU 환경에서 3000개 기준 약 10~15분 소요.
    """

    if not texts:
        embed_comments.last_stats = {
            "enabled": EMBEDDING_CACHE_ENABLED,
            "input_count": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "saved_count": 0,
        }
        return np.empty((0, 0), dtype=np.float32)

    text_hashes = [_embedding_text_hash(text) for text in texts]
    cached_by_hash = {}
    if EMBEDDING_CACHE_ENABLED:
        try:
            cached_by_hash = _load_cached_embeddings(text_hashes)
        except Exception:
            logger.exception("[EmbeddingCache] 캐시 조회 실패, 전체 임베딩으로 진행")
            cached_by_hash = {}

    missing_indexes = [
        idx for idx, text_hash in enumerate(text_hashes)
        if text_hash not in cached_by_hash
    ]
    missing_texts = [texts[idx] for idx in missing_indexes]

    logger.info(
        "[EmbeddingCache] enabled=%s, input=%s, hit=%s, miss=%s",
        EMBEDDING_CACHE_ENABLED,
        len(texts),
        len(texts) - len(missing_indexes),
        len(missing_indexes),
    )

    new_embeddings = np.empty((0, 0), dtype=np.float32)
    if missing_texts:
        model = get_embedding_model()
        # KoSimCSE-roberta 사용해봤는데 별로였음.
        # jhgan/ko-sroberta-multitask 대비 군집이 덜 나옴.
        new_embeddings = model.encode(
            missing_texts,
            show_progress_bar=True,
            batch_size=EMBEDDING_BATCH_SIZE,
            convert_to_numpy=True,
        ).astype(np.float32)

        if EMBEDDING_CACHE_ENABLED:
            try:
                _save_cached_embeddings([
                    (text_hashes[idx], texts[idx], vector)
                    for idx, vector in zip(missing_indexes, new_embeddings)
                ])
            except Exception:
                logger.exception("[EmbeddingCache] 캐시 저장 실패")

    vectors = {}
    vectors.update(cached_by_hash)
    for idx, vector in zip(missing_indexes, new_embeddings):
        vectors[text_hashes[idx]] = np.asarray(vector, dtype=np.float32)

    if not vectors:
        embeddings = np.empty((0, 0), dtype=np.float32)
    else:
        embeddings = np.vstack([vectors[text_hash] for text_hash in text_hashes]).astype(np.float32)

    embed_comments.last_stats = {
        "enabled": EMBEDDING_CACHE_ENABLED,
        "model": EMBEDDING_MODEL_NAME,
        "batch_size": EMBEDDING_BATCH_SIZE,
        "input_count": len(texts),
        "cache_hits": len(texts) - len(missing_indexes),
        "cache_misses": len(missing_indexes),
        "saved_count": int(len(new_embeddings)) if getattr(new_embeddings, "ndim", 0) == 2 else 0,
        "dimension": int(embeddings.shape[1]) if getattr(embeddings, "ndim", 0) == 2 and embeddings.size else None,
    }
    return embeddings


embed_comments.last_stats = {}


# ─────────────────────────────────────────────────────────────
# 4. 최적 eps 자동 탐색
# ─────────────────────────────────────────────────────────────

def find_best_eps(embeddings, min_samples: int) -> float:
    """
    k-distance graph의 elbow 지점을 eps로 사용.
    DBSCAN eps를 데이터에 맞게 자동으로 결정하는 표준 방법.
    실패 시 댓글 수 기반 경험값으로 폴백.
    """
    try:
        from sklearn.neighbors import NearestNeighbors
        k = min(min_samples, len(embeddings) - 1)
        distances, _ = NearestNeighbors(
            n_neighbors=k, metric='cosine'
        ).fit(embeddings).kneighbors(embeddings)
        k_distances = np.sort(distances[:, -1])
        elbow_idx = int(np.argmax(np.diff(k_distances)))
        eps = float(k_distances[elbow_idx])

        # 댓글 수에 따라 상한 다르게 클램핑
        n = len(embeddings)
        if n < 500:
            return max(0.15, min(0.25, eps))
        elif n < 2000:
            return max(0.2, min(0.30, eps))
        else:
            return max(0.25, min(0.35, eps))

    except Exception:
        n = len(embeddings)
        if n < 500:
            return 0.25
        elif n < 2000:
            return 0.30
        return 0.35


# ─────────────────────────────────────────────────────────────
# 5. DBSCAN 군집화
# ─────────────────────────────────────────────────────────────

def cluster_comments(embeddings) -> list:
    """
    DBSCAN으로 군집화.
    - min_samples: 전체의 0.8%, 최소 5 최대 30
    - eps: find_best_eps()로 자동 탐색
    반환: 각 댓글의 군집 번호 리스트 (-1은 노이즈)
    """

    n = len(embeddings)

    if n < 100:
        min_samples = max(3, int(n * 0.05))
    elif n < 500:
        min_samples = max(5, int(n * 0.02))
    elif n < 2000:
        min_samples = max(10, int(n * 0.01))
    else:
        min_samples = max(15, min(20, int(n * 0.008)))

    try:
        labels, stats = fit_predict_cosine_dbscan(embeddings, min_samples=min_samples)
        cluster_comments.last_stats = stats
        logger.info(
            "[DBSCAN] "
            f"backend={stats['backend']}, mode={stats.get('mode')}, "
            f"n={n}, eps={stats['eps']:.3f}, min_samples={min_samples}, "
            f"clusters={stats['cluster_count']}, noise={stats['noise_count']}, "
            f"seconds={stats['seconds']:.3f}"
        )
        return labels
    except Exception as e:
        logger.exception("[DBSCAN] scratch 실패, sklearn fallback 사용")
        from sklearn.cluster import DBSCAN

        eps = find_best_eps(embeddings, min_samples)
        labels = DBSCAN(eps=eps, min_samples=min_samples, metric='cosine').fit_predict(embeddings).tolist()
        cluster_comments.last_stats = {
            "backend": "sklearn_fallback",
            "n": n,
            "eps": round(float(eps), 6),
            "min_samples": int(min_samples),
            "cluster_count": len(set(l for l in labels if l != -1)),
            "noise_count": sum(1 for l in labels if l == -1),
        }
        logger.info("[DBSCAN] backend=sklearn_fallback, n=%s, eps=%.3f, min_samples=%s", n, eps, min_samples)
        return labels

    # Mock
    n = len(embeddings)
    return (
        [0] * int(n * 0.40) +
        [1] * int(n * 0.25) +
        [2] * int(n * 0.15) +
        [-1] * (n - int(n * 0.40) - int(n * 0.25) - int(n * 0.15))
    )


cluster_comments.last_stats = {}


# ─────────────────────────────────────────────────────────────
# 6-A. gemma4:e4b 라벨링 (Ollama)
# ─────────────────────────────────────────────────────────────

def label_cluster_with_llm(top_comments: list, fallback_texts: list = None) -> dict:
    """
    gemma4:e4b에 군집 대표 댓글을 보내서 라벨/감성/태그 생성.

    /api/chat + system 프롬프트 방식 사용.
    system 프롬프트에 <|think|> 토큰을 넣지 않으면
    gemma4의 thinking 모드가 비활성화되어 빠르게 응답.

    Ollama 호출 실패 시 TF-IDF 폴백.
    """
    comments_str = "\n".join(f"- {c}" for c in top_comments[:5])
    user_prompt = (
        "다음 유튜브 댓글 군집의 핵심 여론을 분석해주세요.\n\n"
        f"댓글:\n{comments_str}\n\n"
        "라벨 작성 규칙:\n"
        "- label은 반드시 '무엇에 대한 어떤 입장' 형태로 작성하세요.\n"
        "- '통합 반대'처럼 주어가 빠진 라벨은 금지합니다.\n"
        "- 댓글에 보이는 핵심 대상(예: 공항, 민영화, 적자, 하향평준화, 재분배)을 label 또는 tags에 포함하세요.\n"
        "- 좋은 예: '공항 통합 반대', '민영화 의혹', '적자 우려', '하향평준화 반대'\n"
        "- 나쁜 예: '통합 대한 반대', '강력한 반대', '재정적 반대'\n\n"
        "아래 JSON 형식으로만 답하세요:\n"
        '{"label": "16자 이내 대상 포함 라벨", '
        '"summary": "이 군집 여론을 한 문장으로 요약", '
        '"sentiment": "positive 또는 negative 또는 neutral 중 하나", '
        '"tags": ["키워드1", "키워드2", "키워드3"]}'
    )

    try:
        res = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "stream": False,
                "messages": [
                    {
                        "role": "system",
                        # <|think|> 토큰 없음 → thinking 비활성화, 빠른 응답
                        "content": (
                            "당신은 유튜브 댓글 여론 분석 전문가입니다. "
                            "라벨에는 반드시 여론의 대상이 되는 주어를 포함하세요. "
                            "요청받은 JSON 형식으로만 답하세요. "
                            "설명이나 부연은 절대 하지 마세요."
                        ),
                    },
                    {
                        "role": "user",
                        "content": user_prompt,
                    },
                ],
            },
            timeout=300,
        )
        if res.status_code >= 400:
            logger.warning("[Ollama] /api/chat 실패: status=%s body=%s", res.status_code, res.text[:500])
            raise requests.HTTPError(f"Ollama chat failed: {res.status_code}", response=res)

        # /api/chat 응답 형식: {"message": {"content": "..."}}
        text = res.json().get("message", {}).get("content", "")

        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            result = json.loads(match.group())
            if all(k in result for k in ("label", "sentiment", "tags", "summary")):
                result["sentiment"] = result["sentiment"].strip().lower()
                if result["sentiment"] not in ("positive", "negative", "neutral"):
                    result["sentiment"] = "neutral"
                return _sanitize_cluster_info(result, top_comments)

        logger.warning("[Ollama] /api/chat JSON 파싱 실패: content=%s", text[:500])

    except Exception:
        logger.exception("[LLM 라벨링 실패 → /api/generate 재시도]")

    try:
        prompt = (
            "당신은 유튜브 댓글 여론 분석 전문가입니다. "
            "라벨에는 반드시 여론의 대상이 되는 주어를 포함하고, 설명 없이 JSON만 답하세요.\n\n"
            f"{user_prompt}"
        )
        res = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "stream": False,
                "prompt": prompt,
            },
            timeout=300,
        )
        if res.status_code >= 400:
            logger.warning("[Ollama] /api/generate 실패: status=%s body=%s", res.status_code, res.text[:500])
            raise requests.HTTPError(f"Ollama generate failed: {res.status_code}", response=res)

        text = res.json().get("response", "")
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            result = json.loads(match.group())
            if all(k in result for k in ("label", "sentiment", "tags", "summary")):
                result["sentiment"] = result["sentiment"].strip().lower()
                if result["sentiment"] not in ("positive", "negative", "neutral"):
                    result["sentiment"] = "neutral"
                return _sanitize_cluster_info(result, top_comments)

        logger.warning("[Ollama] /api/generate JSON 파싱 실패: content=%s", text[:500])

    except Exception:
        logger.exception("[LLM 라벨링 실패 → TF-IDF 폴백]")

    return _tfidf_label(fallback_texts or top_comments)


# ─────────────────────────────────────────────────────────────
# 6-B. TF-IDF 폴백 라벨링
# ─────────────────────────────────────────────────────────────

KOREAN_STOPWORDS = {
    "그냥", "진짜", "정말", "너무", "계속", "이제", "이미", "아직", "역시",
    "하는", "해서", "하면", "하고", "없는", "있는", "같은", "이런", "저런", "그런",
    "입니다", "합니다", "하세요", "마세요", "됩니다", "때문", "댓글", "영상", "사람",
    "절대", "그대로", "만드는", "둬라", "네버네버", "높아지겠지", "돈빼다가",
    "넘길려고", "하지", "잘해라", "먹여", "살리기", "그놈의", "그만하자",
}

KEYWORD_HINTS = (
    "민영화", "공항", "통합", "반대", "찬성", "공공성", "균형발전", "하향평준화",
    "적자", "폐쇄", "허브", "수사", "요금", "비리", "마약", "단속", "공산주의",
)

SENTIMENT_KEYWORDS = {"반대", "찬성", "비판", "우려", "걱정"}


def _clean_keyword(keyword: str) -> str:
    keyword = re.sub(r"[^가-힣a-zA-Z0-9\s]", " ", str(keyword or ""))
    keyword = re.sub(r"\s+", " ", keyword).strip()
    words = []
    for word in keyword.split():
        if word.lower() in KOREAN_STOPWORDS:
            continue
        for ending in ("했습니다", "합니다", "됩니다", "하라고", "하자는", "하자", "했다", "한다", "하는", "하면", "해서", "하지"):
            if word.endswith(ending) and len(word) > len(ending) + 1:
                word = word[: -len(ending)]
                break
        for particle in ("으로", "이나"):
            if word.endswith(particle) and len(word) > len(particle) + 1:
                word = word[: -len(particle)]
                break
        if len(word) > 3 and word[-1] == "나":
            word = word[:-1]
        if len(word) > 2 and word[-1] in "은는이가을를에의도만":
            word = word[:-1]
        if word.lower() not in KOREAN_STOPWORDS:
            words.append(word)
    return " ".join(words).strip()


def _is_good_keyword(keyword: str) -> bool:
    compact = re.sub(r"\s+", "", keyword or "")
    if len(compact) < 2 or len(compact) > 14:
        return False
    parts = keyword.split()
    if len(parts) > 1 and len(set(parts)) < len(parts):
        return False
    if compact.lower() in KOREAN_STOPWORDS:
        return False
    if _is_generic_cluster_label(compact):
        return False
    if len(set(compact)) <= 1:
        return False
    return bool(re.search(r"[가-힣a-zA-Z]", compact))


def _expand_keyword_parts(keyword: str) -> list:
    expanded = []
    compact = keyword.replace(" ", "")
    for hint in KEYWORD_HINTS:
        if hint in compact and hint != compact:
            expanded.append(hint)
    expanded.append(keyword)
    return expanded


def _keyword_label(tags: list) -> str:
    if not tags:
        return "기타 의견"

    for sentiment in ("반대", "찬성", "우려", "비판"):
        if sentiment in tags:
            topic = next(
                (
                    tag for tag in tags
                    if tag not in SENTIMENT_KEYWORDS and sentiment not in tag.replace(" ", "")
                ),
                None,
            )
            if topic:
                return f"{topic} {sentiment}"

    if len(tags) >= 2:
        a = tags[0].replace(" ", "")
        b = tags[1].replace(" ", "")
        if a in b:
            return tags[1]
        if b in a:
            return tags[0]

    return " ".join(tags[:2])


def _frequency_keywords(texts: list, limit: int = 6) -> list:
    scores = defaultdict(float)
    doc_counts = defaultdict(int)

    for text in texts:
        raw_words = re.findall(r"[가-힣a-zA-Z0-9]{2,}", text or "")
        cleaned_words = []
        seen_in_doc = set()

        for raw_word in raw_words:
            cleaned = _clean_keyword(raw_word)
            if not cleaned:
                continue

            for keyword in _expand_keyword_parts(cleaned):
                if not _is_good_keyword(keyword):
                    continue
                key = keyword.replace(" ", "").lower()
                if key not in seen_in_doc:
                    doc_counts[keyword] += 1
                    seen_in_doc.add(key)
                scores[keyword] += 1.0
                if keyword in KEYWORD_HINTS:
                    scores[keyword] += 1.5

            cleaned_words.append(cleaned)

        for a, b in zip(cleaned_words, cleaned_words[1:]):
            phrase = _clean_keyword(f"{a} {b}")
            if not _is_good_keyword(phrase):
                continue
            parts = phrase.split()
            if len(parts) > 1 and len(set(parts)) < len(parts):
                continue
            scores[phrase] += 0.8

    ranked = sorted(
        scores,
        key=lambda keyword: (
            -(scores[keyword] + doc_counts[keyword] * 1.5),
            len(keyword),
            keyword,
        ),
    )
    return _dedupe_keywords(ranked)[:limit]


def _dedupe_keywords(keywords: list) -> list:
    result = []
    seen = set()
    for keyword in keywords:
        cleaned = _clean_keyword(keyword)
        if not _is_good_keyword(cleaned):
            continue
        key = cleaned.replace(" ", "").lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def _comment_quality_score(text: str) -> float:
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return -100.0

    words = re.findall(r"[가-힣a-zA-Z0-9]{2,}", text or "")
    cleaned_words = _dedupe_keywords(words)
    score = 0.0
    score += min(len(cleaned_words), 6) * 2.0

    length = len(compact)
    if 12 <= length <= 80:
        score += 4.0
    elif length < 6:
        score -= 6.0
    elif length > 120:
        score -= 2.0

    if len(set(compact)) <= max(2, len(compact) // 5):
        score -= 8.0

    repeated_words = re.findall(r"([가-힣a-zA-Z0-9]{2,})(?:\s*\1){1,}", text or "")
    score -= len(repeated_words) * 4.0

    if not cleaned_words:
        score -= 6.0

    return score


def _select_representative_comments(cluster_texts: list, limit: int = 5) -> list:
    candidates = [t for t in cluster_texts if t and len(t.strip()) > 3]
    if not candidates:
        return cluster_texts[:limit]

    deduped = []
    seen = set()
    for text in candidates:
        key = re.sub(r"\s+", "", text)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(text)

    ranked = sorted(deduped, key=lambda text: (-_comment_quality_score(text), len(text)))
    return ranked[:limit]


def _sanitize_cluster_info(info: dict, source_texts: list) -> dict:
    tags = info.get("tags") or []
    if isinstance(tags, str):
        tags = re.split(r"[,/·\s]+", tags)
    tags = _dedupe_keywords(tags)

    label = _clean_keyword(info.get("label") or "")
    if not _is_good_keyword(label):
        label = " ".join(tags[:2]) if tags else "기타 의견"

    if len(label) > 14 and tags:
        label = " ".join(tags[:2])

    summary = info.get("summary")
    if summary:
        summary = re.sub(r"\s+", " ", str(summary)).strip()

    sentiment = str(info.get("sentiment") or _estimate_sentiment(source_texts)).strip().lower()
    if sentiment not in ("positive", "negative", "neutral"):
        sentiment = "neutral"

    return {
        "label": label,
        "summary": summary,
        "sentiment": sentiment,
        "tags": tags[:3],
    }


def _tfidf_label(texts: list) -> dict:
    tags = _frequency_keywords(texts, limit=6)

    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        tfidf = TfidfVectorizer(
            max_features=80,
            min_df=1,
            ngram_range=(1, 2),
            token_pattern=r"(?u)\b[가-힣a-zA-Z0-9]{2,}\b",
        )
        matrix = tfidf.fit_transform(texts)
        terms = tfidf.get_feature_names_out()
        weights = np.asarray(matrix.sum(axis=0)).ravel()
        candidates = [terms[i] for i in weights.argsort()[::-1]]
        tags = _dedupe_keywords(tags + candidates)[:3]
    except Exception:
        tags = tags[:3]

    label = _keyword_label(tags)

    return _sanitize_cluster_info({
        "label": label,
        "summary": None,
        "sentiment": _estimate_sentiment(texts),
        "tags": tags,
    }, texts)


def _estimate_sentiment(texts: list) -> str:
    pos_kw = ["좋", "기대", "혁신", "발전", "희망", "훌륭", "최고", "응원"]
    neg_kw = ["나쁘", "위험", "걱정", "문제", "반대", "실망", "최악", "비판"]
    pos = sum(1 for t in texts for k in pos_kw if k in t)
    neg = sum(1 for t in texts for k in neg_kw if k in t)
    if pos > neg * 1.5:
        return "positive"
    if neg > pos * 1.5:
        return "negative"
    return "neutral"


def _is_generic_cluster_label(label: str) -> bool:
    normalized = re.sub(r"\s+", "", label or "").lower()
    return normalized in {
        "기타",
        "기타의견",
        "분류안됨",
        "분류불가",
        "기타댓글",
        "others",
        "noise",
    }


def _refine_generic_cluster_info(top_comments: list, fallback_label: str = "기타 의견") -> dict:
    """
    UI에는 하나의 군집으로 합쳐졌지만 검색에는 부적합한 generic 라벨을
    대표 댓글 기반 라벨/태그로 다시 바꾼다.
    """
    comments = [c for c in top_comments if c and len(c.strip()) > 3][:5]
    if not comments:
        return {
            "label": fallback_label,
            "summary": None,
            "sentiment": "neutral",
            "tags": [],
        }

    info = label_cluster_with_llm(comments)
    label = info.get("label") or fallback_label
    tags = [tag for tag in info.get("tags", []) if not _is_generic_cluster_label(tag)]

    if _is_generic_cluster_label(label) and tags:
        label = " ".join(tags[:2])

    return {
        "label": label,
        "summary": info.get("summary"),
        "sentiment": info.get("sentiment", "neutral"),
        "tags": tags[:5],
    }


# ─────────────────────────────────────────────────────────────
# 6-C. 전체 군집 라벨링 실행
# ─────────────────────────────────────────────────────────────

def label_clusters(texts: list, labels: list) -> list:
    """
    각 군집에 대해 LLM 라벨링 실행.
    노이즈(-1)는 LLM 호출 없이 고정 라벨 사용.
    """
    total = len(texts)
    cluster_map = defaultdict(list)
    for i, label in enumerate(labels):
        cluster_map[label].append(texts[i])

    results = []
    for label_id, cluster_texts in cluster_map.items():
        is_noise = (label_id == -1)
        count = len(cluster_texts)
        top_comments = _select_representative_comments(cluster_texts, limit=5)

        if is_noise:
            logger.info("[라벨링] 노이즈 (%s개) gemma4:e4b 호출 중...", count)
            info = label_cluster_with_llm(top_comments, fallback_texts=cluster_texts)
            info["label"] = "분류 안 됨"  # 라벨은 고정, summary/tags는 LLM이 생성
        else:
            logger.info("[라벨링] 군집 %s (%s개) gemma4:e4b 호출 중...", label_id, count)
            info = label_cluster_with_llm(top_comments, fallback_texts=cluster_texts)

        results.append({
            "id": "noise" if is_noise else f"cluster_{label_id}",
            "label": info["label"],
            "summary": info.get("summary", None),
            "sentiment": info["sentiment"],
            "percent": round(count / total * 100, 1),
            "comment_count": count,
            "top_comments": top_comments,
            "tags": info["tags"],
            "source_ids": ["noise"] if is_noise else [f"cluster_{label_id}"],  # 추가
        })

    # 크기순 정렬, 노이즈는 맨 뒤
    results.sort(key=lambda x: (x["id"] == "noise", -x["comment_count"]))
    return results


# ─────────────────────────────────────────────────────────────
# 7. 군집 후처리 (4개 내외로 정리)
# ─────────────────────────────────────────────────────────────

def merge_small_clusters(clusters: list, target: int = 4) -> list:
    """
    군집이 target개보다 많으면 작은 것들을 '기타 의견'으로 합침.
    노이즈는 항상 별도 유지.

    DBSCAN 특성상 군집 수가 가변이므로,
    UI에서 "4개 정도" 보여주기 위한 후처리.
    """
    real = [c for c in clusters if c["id"] != "noise"]
    noise = next((c for c in clusters if c["id"] == "noise"), None)

    if len(real) <= target:
        return clusters

    real.sort(key=lambda x: -x["comment_count"])
    main = real[:target - 1]
    others = real[target - 1:]
    others_top_comments = [c for cl in others for c in cl["top_comments"]][:5]
    refined = _refine_generic_cluster_info(others_top_comments, fallback_label="기타 의견")

    merged = {
        "id": "others",
        "label": refined["label"],
        "summary": refined.get("summary"),
        "sentiment": refined.get("sentiment", "neutral"),
        "percent": round(sum(c["percent"] for c in others), 1),
        "comment_count": sum(c["comment_count"] for c in others),
        "top_comments": others_top_comments,
        "tags": refined.get("tags", []),
        "source_ids": [sid for cl in others for sid in cl.get("source_ids", [cl["id"]])],  # 추가
    }

    result = main + [merged]
    if noise:
        result.append(noise)
    return result


# ─────────────────────────────────────────────────────────────
# 8. 시간대별 분석
# ─────────────────────────────────────────────────────────────

def build_timeline(meta: list, labels: list, clusters: list) -> list:
    """
    댓글 published_at 기준 시간대별 군집 비율 계산.
    """
    from datetime import datetime, timezone, timedelta

    # 시간대 구간 정의 (시간 단위)
    slots = [
        ("0-2시간",   0,   2),
        ("2-4시간",   2,   4),
        ("4-8시간",   4,   8),
        ("8-12시간",  8,  12),
        ("12-24시간", 12, 24),
        ("1-2일",     24, 48),
        ("2-3일",     48, 72),
        ("3일+",      72, float('inf')),
    ]

    cluster_ids = [c["id"] for c in clusters]

    # published_at 파싱 + 가장 오래된 댓글 기준점으로 삼기
    parsed = []
    for i, item in enumerate(meta):
        pub = item.get("published_at", "")
        try:
            dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            parsed.append((dt, labels[i]))
        except Exception:
            continue

    if not parsed:
        # 파싱 실패 시 전체 비율 그대로 모든 슬롯에 적용
        base = {c["id"]: c["percent"] for c in clusters}
        return [{"label": slot[0], "clusters": base} for slot in slots]

    first_dt = min(dt for dt, _ in parsed)

    timeline = []
    for slot_label, hour_start, hour_end in slots:
        slot_comments = [
            label for dt, label in parsed
            if hour_start <= (dt - first_dt).total_seconds() / 3600 < hour_end
        ]

        if not slot_comments:
            # 해당 시간대 댓글 없으면 0으로
            timeline.append({
                "label": slot_label,
                "clusters": {cid: 0.0 for cid in cluster_ids}
            })
            continue

        total = len(slot_comments)
        counts = defaultdict(int)
        for label in slot_comments:
            cid = "noise" if label == -1 else f"cluster_{label}"
            counts[cid] += 1

        timeline.append({
            "label": slot_label,
            "clusters": {
                cid: round(counts.get(cid, 0) / total * 100, 1)
                for cid in cluster_ids
            }
        })

    return timeline


# ─────────────────────────────────────────────────────────────
# 캐시 조회 / 저장
# ─────────────────────────────────────────────────────────────

def get_cached_result(video_id: str, mark_cached: bool = True):
    conn = get_conn()
    row = conn.execute(
        "SELECT result_json FROM analysis_cache WHERE video_id = ?",
        (video_id,)
    ).fetchone()
    conn.close()
    if row:
        result = json.loads(row["result_json"])
        result["clusters"] = _serialize_clusters(result.get("clusters", []))
        result["cached"] = mark_cached
        return result
    return None


def save_result_to_cache(video_id: str, result: dict):
    conn = get_conn()
    conn.execute(
        """
        INSERT OR REPLACE INTO analysis_cache
            (video_id, video_title, result_json, comment_count, analyzed_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        """,
        (
            video_id,
            result.get("video_title"),
            json.dumps(result, ensure_ascii=False),
            result.get("total_comments", 0),
        ),
    )
    conn.commit()
    conn.close()

# ─────────────────────────────────────────────────────────────
# 작업 상태 관리
# ─────────────────────────────────────────────────────────────

def get_active_job(video_id: str):
    conn = get_conn()
    row = conn.execute("""
        SELECT * FROM jobs WHERE video_id = ?
        AND status IN ('pending', 'processing')
        ORDER BY updated_at DESC LIMIT 1
    """, (video_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def get_or_create_job(video_id: str):
    active_job = get_active_job(video_id)
    if active_job:
        return active_job, False

    job_id = str(uuid.uuid4())
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO jobs (job_id, video_id, status, progress) VALUES (?, ?, 'pending', 0)",
            (job_id, video_id),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        row = conn.execute("""
            SELECT * FROM jobs WHERE video_id = ?
            AND status IN ('pending', 'processing')
            ORDER BY updated_at DESC LIMIT 1
        """, (video_id,)).fetchone()
        conn.close()
        if row:
            return dict(row), False
        raise
    else:
        conn.close()
        return {
            "job_id": job_id, "video_id": video_id,
            "status": "pending", "progress": 0, "message": None
        }, True


def update_job(job_id: str, status: str, progress: int, message: str = None):
    conn = get_conn()
    conn.execute(
        """
        UPDATE jobs
        SET status=?, progress=?, message=?, updated_at=datetime('now')
        WHERE job_id=?
        """,
        (status, progress, message, job_id),
    )
    conn.commit()
    conn.close()


def get_job(job_id: str):
    conn = get_conn()
    row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

# ─────────────────────────────────────────────────────────────
# Ollama 모델 언로드
# ─────────────────────────────────────────────────────────────

def unload_ollama_model():
    try:
        requests.post(f"{OLLAMA_BASE_URL}/api/generate", json={
            "model": OLLAMA_MODEL,
            "keep_alive": 0
        }, timeout=10)
        logger.info("[Ollama] 모델 언로드 완료")
    except Exception as e:
        logger.warning("[Ollama] 언로드 실패: %s", e)


# ─────────────────────────────────────────────────────────────
# 메인 분석 실행 (백그라운드 호출)
# ─────────────────────────────────────────────────────────────

def _run_analysis_internal(job_id: str, video_id: str):
    pipeline_start = time.time()
    started_at = _utc_now_iso()
    profile_steps = []

    def log_step(step: str, step_start: float = None, extra: dict = None):
        now = time.time()
        elapsed = now - pipeline_start
        timestamp = _utc_now_iso()
        entry = {
            "step": step,
            "timestamp": timestamp,
            "elapsed_seconds": round(elapsed, 3),
        }
        if step_start:
            step_seconds = now - step_start
            entry["step_seconds"] = round(step_seconds, 3)
            logger.info("[%s][%.1fs] %s (단계: %.1fs)", timestamp, elapsed, step, step_seconds)
        else:
            logger.info("[%s][%.1fs] %s", timestamp, elapsed, step)
        if extra:
            entry.update(extra)
        profile_steps.append(entry)
        return now

    try:
        t = log_step("댓글 수집 시작")
        update_job(job_id, "processing", 10, "댓글 수집 중...")
        comments_data = fetch_comments(video_id)
        video_title = fetch_video_title(video_id)
        t = log_step(f"댓글 수집 완료 ({len(comments_data)}개)", t)

        update_job(job_id, "processing", 20, "텍스트 정제 중...")
        texts, meta = clean_comments(comments_data)
        clean_stats = getattr(clean_comments, "last_stats", {})
        t = log_step(f"정제 완료 ({len(texts)}개)", t, {"cleaning": clean_stats})

        if not texts:
            update_job(job_id, "failed", 0, "정제 후 유효한 댓글이 없습니다.")
            return
        if len(texts) < 50:
            update_job(job_id, "failed", 0, "댓글이 너무 적어 분석이 어렵습니다. (최소 50개 필요)")
            return

        update_job(job_id, "processing", 35, f"임베딩 중... ({len(texts)}개, CPU라 시간이 걸려요)")
        embedding_texts = [item.get("embedding_text", text) for text, item in zip(texts, meta)]
        embeddings = embed_comments(embedding_texts)
        embedding_stats = getattr(embed_comments, "last_stats", {})
        t = log_step(
            "임베딩 완료",
            t,
            {
                "embedding": embedding_stats or {
                    "model": EMBEDDING_MODEL_NAME,
                    "batch_size": EMBEDDING_BATCH_SIZE,
                    "input_count": len(embedding_texts),
                    "dimension": int(embeddings.shape[1]) if getattr(embeddings, "ndim", 0) == 2 else None,
                }
            },
        )

        update_job(job_id, "processing", 65, "DBSCAN 군집화 중...")
        labels = cluster_comments(embeddings)
        n_clusters = len(set(l for l in labels if l != -1))
        dbscan_stats = getattr(cluster_comments, "last_stats", {})
        t = log_step(f"군집화 완료 ({n_clusters}개)", t, {"dbscan": dbscan_stats})

        update_job(job_id, "processing", 75, f"군집 라벨 생성 중... ({n_clusters}개 군집, gemma4:e4b 호출)")
        clusters = label_clusters(texts, labels)
        unload_ollama_model()
        t = log_step("라벨링 완료", t)

        update_job(job_id, "processing", 88, "군집 정리 중...")
        clusters = merge_small_clusters(clusters, target=4)

        update_job(job_id, "processing", 93, "시간대별 분석 중...")
        timeline = build_timeline(meta, labels, clusters)

        public_clusters = _serialize_clusters(clusters)
        finished_at = _utc_now_iso()
        total_seconds = round(time.time() - pipeline_start, 3)
        result = {
            "video_id": video_id,
            "video_title": video_title,
            "total_comments": len(texts),
            "cluster_count": len([c for c in clusters if c["id"] not in ("noise", "others")]),
            "clusters": public_clusters,
            "timeline": timeline,
            "analyzed_at": finished_at,
            "cached": False,
            "pipeline_version": "2026-06-03-scratch-dbscan-profiled",
            "performance": {
                "started_at": started_at,
                "finished_at": finished_at,
                "total_seconds": total_seconds,
                "steps": profile_steps,
                "cleaning": clean_stats,
                "embedding": embedding_stats or {
                    "model": EMBEDDING_MODEL_NAME,
                    "batch_size": EMBEDDING_BATCH_SIZE,
                    "input_count": len(embedding_texts),
                    "dimension": int(embeddings.shape[1]) if getattr(embeddings, "ndim", 0) == 2 else None,
                },
                "dbscan": dbscan_stats,
            },
        }

        save_result_to_cache(video_id, result)
        log_step(f"전체 완료 (총 {time.time() - pipeline_start:.1f}초)")
        update_job(job_id, "done", 100, "분석 완료")

    except Exception as e:
        log_step(f"오류 발생: {str(e)}")
        update_job(job_id, "failed", 0, "분석 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")
        raise


# ─────────────────────────────────────────────────────────────
# 큐 시스템
# ─────────────────────────────────────────────────────────────

_job_queue = queue.Queue()
_worker_thread = None


def _worker():
    while True:
        job_id, video_id = _job_queue.get()
        try:
            logger.info("[Worker] 작업 처리 시작: job_id=%s, video_id=%s, queue_size=%s", job_id, video_id, _job_queue.qsize())
            _run_analysis_internal(job_id, video_id)
            logger.info("[Worker] 작업 처리 완료: job_id=%s, video_id=%s", job_id, video_id)
        except Exception:
            logger.exception("[Worker] 작업 처리 실패: job_id=%s, video_id=%s", job_id, video_id)
        finally:
            _job_queue.task_done()


def start_worker():
    global _worker_thread
    if _worker_thread is None or not _worker_thread.is_alive():
        _worker_thread = threading.Thread(target=_worker, daemon=True)
        _worker_thread.start()
        logger.info("[Worker] 백그라운드 워커 시작")


def run_analysis(job_id: str, video_id: str):
    """큐에 작업 추가 — 워커가 순서대로 처리"""
    _job_queue.put((job_id, video_id))
    logger.info("[Queue] 작업 추가: job_id=%s, video_id=%s, queue_size=%s", job_id, video_id, _job_queue.qsize())
