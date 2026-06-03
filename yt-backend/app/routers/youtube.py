import logging
import json
import os
import re
import requests
from collections import Counter
from fastapi import APIRouter, HTTPException, Query
from app.database import get_conn
from app.models import YouTubeSearchResponse, VideoResponse

router = APIRouter()
logger = logging.getLogger(__name__)

# .env 파일에 등록된 YOUTUBE_API_KEY를 가져옵니다.
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:e4b")

GENERIC_QUERY_PHRASES = [
    "기타 의견",
    "기타의견",
    "기타 댓글",
    "기타댓글",
    "기타",
    "분류 안 됨",
    "분류안됨",
    "분류 불가",
    "분류불가",
    "others",
    "noise",
]

QUERY_STOPWORDS = {
    "그냥", "진짜", "정말", "너무", "계속", "이제", "이미", "아직",
    "하는", "해서", "하면", "하고", "없는", "있는", "같은", "이런", "저런", "그런",
    "입니다", "합니다", "하세요", "마세요", "됩니다", "때문", "댓글", "영상", "의견",
    "절대", "그대로", "만드는", "둬라", "네버네버", "높아지겠지", "돈빼다가",
    "넘길려고", "하지", "잘해라", "먹여", "살리기", "그놈의", "그만하자",
}

BROAD_SINGLE_TOKEN_QUERIES = {"공항", "정치", "정부", "뉴스", "댓글", "여론"}

CONTEXT_STOPWORDS = QUERY_STOPWORDS | {
    "계획", "반대", "찬성", "통합", "여론", "라벨", "요약", "감성", "태그",
    "대한", "대해", "관련", "제안", "구조", "목적", "발생", "표출", "의견",
    "강한", "막대한", "사유화", "불신", "우려", "정리", "분석", "영상",
    "입니다", "있습니다", "없습니다",
}


def _normalize_query_token(token: str) -> str:
    token = token.strip()
    for ending in ("했습니다", "합니다", "됩니다", "하라고", "하자는", "하자", "했다", "한다", "하는", "하면", "해서", "하지"):
        if token.endswith(ending) and len(token) > len(ending) + 1:
            token = token[: -len(ending)]
            break
    for particle in ("으로", "이나"):
        if token.endswith(particle) and len(token) > len(particle) + 1:
            token = token[: -len(particle)]
            break
    if len(token) > 3 and token[-1] == "나":
        token = token[:-1]
    if len(token) > 2 and token[-1] in "은는이가을를에의도만":
        token = token[:-1]
    return token


def _tokenize_search_text(text: str) -> list[str]:
    query = re.sub(r"[^가-힣a-zA-Z0-9\s]", " ", text or "")
    tokens = []
    seen = set()
    for token in re.split(r"\s+", query):
        token = _normalize_query_token(token)
        key = token.lower()
        if len(token) < 2 or len(token) > 12:
            continue
        if key in QUERY_STOPWORDS:
            continue
        if key in seen:
            continue
        seen.add(key)
        tokens.append(token)
    return tokens


def _clean_search_query(q: str, *, max_tokens: int = 4) -> str:
    query = re.sub(r"\s+", " ", q or "").strip()
    raw_has_generic = any(re.search(re.escape(phrase), query, flags=re.IGNORECASE) for phrase in GENERIC_QUERY_PHRASES)
    for phrase in GENERIC_QUERY_PHRASES:
        query = re.sub(re.escape(phrase), " ", query, flags=re.IGNORECASE)

    tokens = _tokenize_search_text(query)

    if raw_has_generic and len(tokens) < 2:
        return ""

    return " ".join(tokens[:max_tokens]).strip()


def _cluster_query_text(cluster: dict) -> str:
    parts = [
        cluster.get("label", ""),
        cluster.get("summary", ""),
        " ".join(cluster.get("tags") or []),
        " ".join(cluster.get("top_comments") or []),
    ]
    return " ".join(part for part in parts if part)


def _find_cached_cluster_context(raw_query: str) -> str:
    raw_tokens = set(_tokenize_search_text(raw_query))
    if not raw_tokens:
        return ""

    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT result_json
            FROM analysis_cache
            ORDER BY analyzed_at DESC
            LIMIT 20
        """).fetchall()
    finally:
        conn.close()

    best_score = 0
    best_context = ""
    for row in rows:
        try:
            result = json.loads(row["result_json"])
        except Exception:
            continue

        video_title = result.get("video_title") or ""
        if re.fullmatch(r"영상\s*\([^)]+\)", video_title):
            video_title = ""

        for cluster in result.get("clusters", []):
            if cluster.get("id") == "noise":
                continue
            label_text = " ".join([
                cluster.get("label", ""),
                " ".join(cluster.get("tags") or []),
            ])
            cluster_tokens = set(_tokenize_search_text(label_text))
            score = len(raw_tokens & cluster_tokens)
            if score <= best_score:
                continue

            best_score = score
            best_context = " ".join([
                video_title,
                _cluster_query_text(cluster),
            ]).strip()

    return best_context if best_score > 0 else ""


def _expand_query_with_cached_context(raw_query: str, search_query: str) -> str:
    context = _find_cached_cluster_context(raw_query)
    if not context:
        return search_query

    base_tokens = _tokenize_search_text(search_query)
    base_keys = {token.lower() for token in base_tokens}
    context_counts = Counter(_tokenize_search_text(context))

    additions = []
    for token, _ in context_counts.most_common():
        key = token.lower()
        if key in base_keys or key in CONTEXT_STOPWORDS:
            continue
        additions.append(token)
        if len(additions) >= 3:
            break

    if not additions:
        return search_query

    expanded_tokens = (additions + base_tokens)[:6]
    expanded = " ".join(expanded_tokens).strip()
    logger.info(
        "[YouTube] 캐시 문맥 기반 검색어 확장: raw_q=%s, base=%s, expanded=%s",
        raw_query,
        search_query,
        expanded,
    )
    return expanded


def _is_weak_search_query(q: str) -> bool:
    compact = re.sub(r"\s+", "", q or "")
    if len(compact) < 2:
        return True
    tokens = q.split()
    if len(tokens) == 1 and tokens[0] in BROAD_SINGLE_TOKEN_QUERIES:
        return True
    return not re.search(r"[가-힣a-zA-Z0-9]", compact)


def _refine_query_with_llm(q: str) -> str:
    """
    프론트는 q 하나만 넘기므로, 여기서는 검색어 문장만 짧게 다듬는다.
    댓글 문맥 기반 보정은 pipeline.py에서 cluster label/tags 생성 시 수행한다.
    """
    try:
        res = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "stream": False,
                "messages": [
                    {
                        "role": "system",
                        "content": "당신은 유튜브 검색어를 다듬는 도우미입니다. 설명 없이 검색어만 답하세요.",
                    },
                    {
                        "role": "user",
                        "content": (
                            "다음 검색어를 유튜브 검색에 적합하게 2~6개 핵심 단어로 다듬어주세요. "
                            "기타, 분류 안 됨, 의견 같은 일반 단어는 제거하세요.\n"
                            f"검색어: {q}"
                        ),
                    },
                ],
            },
            timeout=20,
        )
        res.raise_for_status()
        refined = res.json().get("message", {}).get("content", "").strip()
        refined = re.sub(r"^[\"'`]+|[\"'`]+$", "", refined)
        refined = re.sub(r"\s+", " ", refined).strip()
        return refined[:80]
    except Exception:
        logger.exception("[YouTube] 검색어 LLM 보정 실패")
        return q

@router.get(
    "/search",
    summary="여론 군집별 관련 유튜브 영상 검색 및 추천",
    description="클러스터 라벨과 태그가 조합된 쿼리를 받아 관련 영상 리스트를 반환합니다.",
    response_model=YouTubeSearchResponse
)
def search_youtube_videos(q: str = Query(..., description="검색 쿼리 스트링")):
    if not YOUTUBE_API_KEY:
        raise HTTPException(status_code=500, detail="서버에 YOUTUBE_API_KEY가 설정되지 않았습니다.")

    raw_query = q
    if re.search(r"분류\s*안\s*됨|noise", raw_query, flags=re.IGNORECASE):
        logger.info("[YouTube] 분류 안 됨/noise 군집 추천 생략: raw_q=%s", raw_query)
        return {"videos": []}

    search_query = _expand_query_with_cached_context(
        raw_query,
        _clean_search_query(raw_query),
    )
    if _is_weak_search_query(search_query):
        refined_query = _clean_search_query(_refine_query_with_llm(raw_query))
        search_query = _expand_query_with_cached_context(raw_query, refined_query)

    if _is_weak_search_query(search_query):
        logger.info("[YouTube] 검색어가 너무 일반적이라 추천 생략: raw_q=%s", raw_query)
        return {"videos": []}

    logger.info("[YouTube] 검색 요청: raw_q=%s, search_q=%s", raw_query, search_query)

    # 1. 1차 호출: 영상 검색 (search.list)
    search_url = "https://www.googleapis.com/youtube/v3/search"
    search_params = {
        "key": YOUTUBE_API_KEY,
        "q": search_query,
        "part": "snippet",
        "type": "video",
        "maxResults": 5,          # 익스텐션 UI에 보여줄 추천 영상 수
        "videoEmbeddable": "true", # 웹/앱에 퍼가기(임베드) 가능한 영상만 필터링
        "relevanceLanguage": "ko",
        "regionCode": "KR",
    }
    
    try:
        search_response = requests.get(search_url, params=search_params)
        if search_response.status_code != 200:
            raise HTTPException(status_code=search_response.status_code, detail="YouTube Search API 호출에 실패했습니다.")
            
        search_data = search_response.json()
        items = search_data.get("items", [])
        
        # 검색 결과가 없으면 빈 리스트 리턴
        if not items:
            return {"videos": []}
            
        video_ids = [item["id"]["videoId"] for item in items]
        
        # 2. 2차 호출: 메타데이터(조회수, 좋아요수) 수집 (videos.list)
        stats_url = "https://www.googleapis.com/youtube/v3/videos"
        stats_params = {
            "key": YOUTUBE_API_KEY,
            "id": ",".join(video_ids),
            "part": "statistics,snippet"
        }
        stats_response = requests.get(stats_url, params=stats_params)
        stats_data = stats_response.json().get("items", [])
        stats_map = {item["id"]: item for item in stats_data}
        
        # 3. 프론트엔드(content.js) 렌더링 스펙에 맞춰 데이터 가공
        videos = []
        for v_id in video_ids:
            stat_item = stats_map.get(v_id, {})
            snippet = stat_item.get("snippet", {})
            statistics = stat_item.get("statistics", {})
            
            # 조회수 가공 (ex. 125000 -> 12만회)
            raw_views = int(statistics.get("viewCount", 0))
            if raw_views >= 10000:
                views_str = f"{raw_views // 10000}만회"
            elif raw_views >= 1000:
                views_str = f"{raw_views // 1000}천회"
            else:
                views_str = f"{raw_views}회"
                
            # 좋아요수 가공 (ex. 12500 -> 1.2만)
            raw_likes = int(statistics.get("likeCount", 0))
            if raw_likes >= 10000:
                likes_str = f"{raw_likes / 10000:.1f}만"
            else:
                likes_str = f"{raw_likes:,}"

            videos.append(
                VideoResponse(
                    title=snippet.get("title", "제목 없음"),
                    channel=snippet.get("channelTitle", "알 수 없는 채널"),
                    thumbnail=snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
                    views=views_str,
                    likes=likes_str,
                    url=f"https://www.youtube.com/watch?v={v_id}"
                )
            )
            
        return {"videos": videos}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")
