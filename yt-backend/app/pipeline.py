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
import uuid
import requests
import numpy as np
from datetime import datetime, timezone
from collections import defaultdict

from app.database import get_conn

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL", "gemma4:e4b")


# ─────────────────────────────────────────────────────────────
# 1. 댓글 수집
# ─────────────────────────────────────────────────────────────

def fetch_comments(video_id: str) -> list:
    from googleapiclient.discovery import build
    api_key = os.getenv("YOUTUBE_API_KEY")
    youtube = build("youtube", "v3", developerKey=api_key)
    comments_data, page_token = [], None

    while True:
        response = youtube.commentThreads().list(
            part="snippet,replies",  # replies 추가
            videoId=video_id,
            maxResults=100,
            pageToken=page_token,
        ).execute()

        for item in response.get("items", []):
            # 최상위 댓글
            s = item["snippet"]["topLevelComment"]["snippet"]
            comments_data.append({
                "author":       s.get("authorDisplayName", ""),
                "text":         s.get("textOriginal", ""),
                "likes":        s.get("likeCount", 0),
                "published_at": s.get("publishedAt", ""),
            })

            # 대댓글 (replies가 있으면)
            reply_count = item["snippet"].get("totalReplyCount", 0)
            replies = item.get("replies", {}).get("comments", [])

            if replies:
                # replies에 포함된 대댓글 (최대 5개)
                for reply in replies:
                    r = reply["snippet"]
                    comments_data.append({
                        "author":       r.get("authorDisplayName", ""),
                        "text":         r.get("textOriginal", ""),
                        "likes":        r.get("likeCount", 0),
                        "published_at": r.get("publishedAt", ""),
                    })

            # 대댓글이 5개 초과면 별도 API 호출 필요
            if reply_count > 5:
                reply_page_token = None
                while True:
                    reply_response = youtube.comments().list(
                        part="snippet",
                        parentId=item["id"],
                        maxResults=100,
                        pageToken=reply_page_token,
                    ).execute()
                    for reply in reply_response.get("items", []):
                        r = reply["snippet"]
                        comments_data.append({
                            "author":       r.get("authorDisplayName", ""),
                            "text":         r.get("textOriginal", ""),
                            "likes":        r.get("likeCount", 0),
                            "published_at": r.get("publishedAt", ""),
                        })
                    reply_page_token = reply_response.get("nextPageToken")
                    if not reply_page_token:
                        break

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return comments_data


# ─────────────────────────────────────────────────────────────
# 2. 텍스트 정제
# ─────────────────────────────────────────────────────────────

def clean_comments(comments_data: list) -> tuple:
    texts, meta = [], []
    seen = set()  # 추가, 중복 확인
    for item in comments_data:
        raw = item.get("text", "")
        cleaned = re.sub(r'[^가-힣a-zA-Z0-9\s]', '', raw)
        cleaned = re.sub(r'[ㄱ-ㅎㅏ-ㅣ]+', '', cleaned).strip()
        if cleaned and cleaned not in seen:  # 중복 체크 추가
            seen.add(cleaned)
            texts.append(cleaned)
            meta.append(item)
    return texts, meta


# ─────────────────────────────────────────────────────────────
# 3. 임베딩
# ─────────────────────────────────────────────────────────────

def embed_comments(texts: list):
    """
    jhgan/ko-sroberta-multitask 모델로 텍스트 벡터화.
    CPU 환경에서 3000개 기준 약 10~15분 소요.
    """

    # TODO: 아래 주석 해제 후 사용
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer('jhgan/ko-sroberta-multitask')
    return model.encode(texts, show_progress_bar=True, batch_size=32)

    # Mock: 랜덤 벡터
    return np.random.rand(len(texts), 768)


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
            return max(0.1, min(0.25, eps))
        elif n < 2000:
            return max(0.1, min(0.30, eps))
        else:
            return max(0.1, min(0.35, eps))

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

    from sklearn.cluster import DBSCAN
    n = len(embeddings)

    if n < 100:
        min_samples = max(3, int(n * 0.05))
    elif n < 500:
        min_samples = max(5, int(n * 0.02))
    elif n < 2000:
        min_samples = max(10, int(n * 0.01))
    else:
        min_samples = max(15, min(30, int(n * 0.008)))

    eps = find_best_eps(embeddings, min_samples)
    print(f"[DBSCAN] n={n}, eps={eps:.3f}, min_samples={min_samples}")
    return DBSCAN(eps=eps, min_samples=min_samples, metric='cosine').fit_predict(embeddings).tolist()

    # Mock
    n = len(embeddings)
    return (
        [0] * int(n * 0.40) +
        [1] * int(n * 0.25) +
        [2] * int(n * 0.15) +
        [-1] * (n - int(n * 0.40) - int(n * 0.25) - int(n * 0.15))
    )


# ─────────────────────────────────────────────────────────────
# 6-A. gemma4:e4b 라벨링 (Ollama)
# ─────────────────────────────────────────────────────────────

def label_cluster_with_llm(top_comments: list) -> dict:
    """
    gemma4:e4b에 군집 대표 댓글을 보내서 라벨/감성/태그 생성.

    /api/chat + system 프롬프트 방식 사용.
    system 프롬프트에 <|think|> 토큰을 넣지 않으면
    gemma4의 thinking 모드가 비활성화되어 빠르게 응답.

    Ollama 호출 실패 시 TF-IDF 폴백.
    """
    comments_str = "\n".join(f"- {c}" for c in top_comments[:5])

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
                            "요청받은 JSON 형식으로만 답하세요. "
                            "설명이나 부연은 절대 하지 마세요."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            "다음 유튜브 댓글 군집의 핵심 여론을 분석해주세요.\n\n"
                            f"댓글:\n{comments_str}\n\n"
                            "아래 JSON 형식으로만 답하세요:\n"
                            '{"label": "10자 이내 핵심 라벨", '
                            '"summary": "이 군집 여론을 한 문장으로 요약", '  # 추가
                            '"sentiment": "positive 또는 negative 또는 neutral 중 하나", '
                            '"tags": ["키워드1", "키워드2", "키워드3"]}'
                        ),
                    },
                ],
            },
            timeout=300,
        )
        res.raise_for_status()

        # /api/chat 응답 형식: {"message": {"content": "..."}}
        text = res.json().get("message", {}).get("content", "")

        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            result = json.loads(match.group())
            if all(k in result for k in ("label", "sentiment", "tags", "summary")):
                result["sentiment"] = result["sentiment"].strip().lower()
                if result["sentiment"] not in ("positive", "negative", "neutral"):
                    result["sentiment"] = "neutral"
                return result

    except Exception as e:
        print(f"[LLM 라벨링 실패 → TF-IDF 폴백] {e}")

    return _tfidf_label(top_comments)


# ─────────────────────────────────────────────────────────────
# 6-B. TF-IDF 폴백 라벨링
# ─────────────────────────────────────────────────────────────

def _tfidf_label(texts: list) -> dict:
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        tfidf = TfidfVectorizer(max_features=50, min_df=1)
        tfidf.fit(texts)
        scores = dict(zip(tfidf.get_feature_names_out(), tfidf.idf_))
        tags = [w for w, _ in sorted(scores.items(), key=lambda x: x[1])[:5] if len(w) > 1]
        label = " · ".join(tags[:3]) if tags else "기타"
    except Exception:
        tags, label = [], "기타"
    return {"label": label, "summary": None, "sentiment": _estimate_sentiment(texts), "tags": tags}


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
        top_comments = sorted(
            [t for t in cluster_texts if len(t) > 10],  # 10자 이상만
            key=len
        )[:5]

        # 그래도 없으면 폴백
        if not top_comments:
            top_comments = sorted(cluster_texts, key=len, reverse=False)[:5]

        if is_noise:
            info = {"label": "분류 안 됨", "summary": None, "sentiment": "neutral", "tags": []}
        else:
            print(f"[라벨링] 군집 {label_id} ({count}개) gemma4:e4b 호출 중...")
            info = label_cluster_with_llm(top_comments)

        results.append({
            "id": "noise" if is_noise else f"cluster_{label_id}",
            "label": info["label"],
            "summary": info.get("summary", None),
            "sentiment": info["sentiment"],
            "percent": round(count / total * 100, 1),
            "comment_count": count,
            "top_comments": top_comments,
            "tags": info["tags"],
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

    merged = {
        "id": "others",
        "label": "기타 의견",
        "sentiment": "neutral",
        "percent": round(sum(c["percent"] for c in others), 1),
        "comment_count": sum(c["comment_count"] for c in others),
        "top_comments": [c for cl in others for c in cl["top_comments"]][:5],
        "tags": [],
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

def get_cached_result(video_id: str):
    conn = get_conn()
    row = conn.execute(
        "SELECT result_json FROM analysis_cache WHERE video_id = ?",
        (video_id,)
    ).fetchone()
    conn.close()
    if row:
        result = json.loads(row["result_json"])
        result["cached"] = True
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

def create_job(video_id: str) -> str:
    job_id = str(uuid.uuid4())
    conn = get_conn()
    conn.execute(
        "INSERT INTO jobs (job_id, video_id, status, progress) VALUES (?, ?, 'pending', 0)",
        (job_id, video_id),
    )
    conn.commit()
    conn.close()
    return job_id


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
        print("[Ollama] 모델 언로드 완료")
    except Exception as e:
        print(f"[Ollama] 언로드 실패: {e}")


# ─────────────────────────────────────────────────────────────
# 메인 분석 실행 (백그라운드 호출)
# ─────────────────────────────────────────────────────────────

def _run_analysis_internal(job_id: str, video_id: str):
    try:
        update_job(job_id, "processing", 10, "댓글 수집 중...")
        comments_data = fetch_comments(video_id)

        update_job(job_id, "processing", 20, "텍스트 정제 중...")
        texts, meta = clean_comments(comments_data)
        if not texts:
            update_job(job_id, "failed", 0, "정제 후 유효한 댓글이 없습니다.")
            return

        if len(texts) < 50:
            update_job(job_id, "failed", 0, "댓글이 너무 적어 분석이 어렵습니다. (최소 50개 필요)")
            return

        update_job(job_id, "processing", 35, f"임베딩 중... ({len(texts)}개, CPU라 시간이 걸려요)")
        embeddings = embed_comments(texts)

        update_job(job_id, "processing", 65, "DBSCAN 군집화 중...")
        labels = cluster_comments(embeddings)
        n_clusters = len(set(l for l in labels if l != -1))

        update_job(job_id, "processing", 75, f"군집 라벨 생성 중... ({n_clusters}개 군집, gemma4:e4b 호출)")
        clusters = label_clusters(texts, labels)
        unload_ollama_model()  # 라벨링 끝나면 즉시 언로드

        update_job(job_id, "processing", 88, "군집 정리 중...")
        clusters = merge_small_clusters(clusters, target=4)

        update_job(job_id, "processing", 93, "시간대별 분석 중...")
        timeline = build_timeline(meta, labels, clusters)

        result = {
            "video_id": video_id,
            "video_title": f"영상 ({video_id})",
            "total_comments": len(texts),
            "cluster_count": len([c for c in clusters if c["id"] not in ("noise", "others")]),
            "clusters": clusters,
            "timeline": timeline,
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "cached": False,
        }

        save_result_to_cache(video_id, result)
        update_job(job_id, "done", 100, "분석 완료")

    except Exception as e:
        update_job(job_id, "failed", 0, f"오류 발생: {str(e)}")
        raise


# ─────────────────────────────────────────────────────────────
# 큐 시스템
# ─────────────────────────────────────────────────────────────

import queue
import threading

_job_queue = queue.Queue()
_worker_thread = None


def _worker():
    while True:
        job_id, video_id = _job_queue.get()
        try:
            _run_analysis_internal(job_id, video_id)
        finally:
            _job_queue.task_done()


def start_worker():
    global _worker_thread
    if _worker_thread is None or not _worker_thread.is_alive():
        _worker_thread = threading.Thread(target=_worker, daemon=True)
        _worker_thread.start()
        print("[Worker] 백그라운드 워커 시작")


def run_analysis(job_id: str, video_id: str):
    """큐에 작업 추가 — 워커가 순서대로 처리"""
    _job_queue.put((job_id, video_id))
    print(f"[Queue] 작업 추가: {job_id} ({video_id}), 대기 중: {_job_queue.qsize()}개")