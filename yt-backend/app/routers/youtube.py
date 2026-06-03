import logging
import json
import os
import re
import requests
from collections import Counter, defaultdict
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
    "대한", "강력한", "재정적", "안됨",
    "절대", "그대로", "만드는", "둬라", "네버네버", "높아지겠지", "돈빼다가",
    "넘길려고", "하지", "잘해라", "먹여", "살리기", "그놈의", "그만하자",
}

BROAD_SINGLE_TOKEN_QUERIES = {"공항", "정치", "정부", "뉴스", "댓글", "여론"}

CONTEXT_STOPWORDS = QUERY_STOPWORDS | {
    "계획", "반대", "찬성", "통합", "여론", "라벨", "요약", "감성", "태그",
    "대한", "대해", "관련", "제안", "구조", "목적", "발생", "표출", "의견",
    "강한", "막대한", "사유화", "불신", "우려", "정리", "분석", "영상",
    "제안된", "발생과", "목적이라", "특정", "사안", "댓글", "군집",
    "논리", "주장합니다", "비판하고", "있다", "있습니다", "통합반대",
    "다같", "다같이", "다들", "무슨", "얘기", "있나",
    "입니다", "있습니다", "없습니다",
    "오늘부터", "현실", "가입되는", "가입되", "완벽합니다", "통신",
}

DOMAIN_CONTEXT_KEYWORDS = [
    "통신3사", "통신사", "통합요금제", "요금제", "무제한", "5G",
    "스타링크", "모바일", "휴대폰", "다이렉트투셀",
    "아스날", "PSG", "이강인", "챔스", "UCL",
    "민영화", "민영화의혹", "적자", "재정", "재정위험",
    "하향평준화", "재분배", "사회주의",
    "대구경북", "대구", "경북", "공항", "TK",
    "김해", "김포", "인천", "제주",
]


def _normalize_query_token(token: str) -> str:
    token = token.strip()
    if re.fullmatch(r"\d+[분초시간일개월년]", token):
        return ""
    if token == "사회주":
        return "사회주의"
    if token.startswith("사회주의"):
        return "사회주의"
    if token.startswith("하향평준화"):
        return "하향평준화"
    for ending in ("했습니다", "합니다", "됩니다", "하라고", "하자는", "하자", "했다", "한다", "하는", "되는", "된다고", "쓰나", "쓰냐", "라고", "하면", "해서", "하지"):
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
    for particle in ("으로", "이나"):
        if token.endswith(particle) and len(token) > len(particle) + 1:
            token = token[: -len(particle)]
            break
    return token


def _tokenize_search_text(text: str, *, dedupe: bool = True) -> list[str]:
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
        if dedupe and key in seen:
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


def _weighted_cluster_query_text(video_title: str, cluster: dict) -> str:
    top_comments = " ".join(cluster.get("top_comments") or [])
    tags = " ".join(cluster.get("tags") or [])
    label = cluster.get("label", "")
    summary = cluster.get("summary", "")
    parts = []
    if video_title:
        parts.extend([video_title] * 5)
    if tags:
        parts.extend([tags] * 4)
    if top_comments:
        parts.extend([top_comments] * 3)
    if label:
        parts.extend([label] * 2)
    if summary:
        parts.append(summary)
    return " ".join(parts)


def _is_domain_context_token(token: str) -> bool:
    upper_token = token.upper()
    return any(keyword in token or keyword in upper_token for keyword in DOMAIN_CONTEXT_KEYWORDS)


def _domain_context_rank(token: str) -> int:
    upper_token = token.upper()
    for idx, keyword in enumerate(DOMAIN_CONTEXT_KEYWORDS):
        if keyword in token or keyword in upper_token:
            return idx
    return 999


def _find_cached_cluster_match(raw_query: str) -> dict | None:
    raw_tokens = set(_tokenize_search_text(raw_query))
    if not raw_tokens:
        return None

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
    best_match = None
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
            best_match = {
                "video_title": video_title,
                "cluster": cluster,
                "context": _weighted_cluster_query_text(video_title, cluster),
            }

    return best_match if best_score > 0 else None


def _find_cached_cluster_context(raw_query: str) -> str:
    match = _find_cached_cluster_match(raw_query)
    return match.get("context", "") if match else ""


def _expand_query_with_cached_context(raw_query: str, search_query: str) -> str:
    context = _find_cached_cluster_context(raw_query)
    if not context:
        return search_query

    base_tokens = _tokenize_search_text(search_query)
    base_keys = {token.lower() for token in base_tokens}
    context_counts = Counter(_tokenize_search_text(context, dedupe=False))

    additions = []
    raw_has_redistribution_issue = any(
        issue in raw_query or issue in search_query
        for issue in ("하향평준화", "재분배", "사회주의")
    )
    candidates = sorted(
        context_counts.items(),
        key=lambda item: (
            0 if _is_domain_context_token(item[0]) else 1,
            _domain_context_rank(item[0]),
            -item[1],
            len(item[0]),
            item[0],
        ),
    )

    for token, _ in candidates:
        key = token.lower()
        if key in base_keys or key in CONTEXT_STOPWORDS:
            continue
        if raw_has_redistribution_issue and any(place in token.upper() for place in ("공항", "인천", "대구", "경북", "김해", "김포", "제주", "TK")):
            continue
        if any(token in existing or existing in token for existing in additions):
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


def _append_unique_query(queries: list[str], query: str):
    query = re.sub(r"\s+", " ", query or "").strip()
    if not query or _is_weak_search_query(query):
        return
    if query not in queries:
        queries.append(query)


def _join_query_tokens(tokens: list[str], limit: int = 6) -> str:
    result = []
    seen = set()
    for token in tokens:
        key = token.lower()
        if key in seen or key in CONTEXT_STOPWORDS:
            continue
        seen.add(key)
        result.append(token)
        if len(result) >= limit:
            break
    return " ".join(result)


def _collect_context_token_scores(video_title: str, cluster: dict) -> tuple[Counter, dict[str, set[str]]]:
    scores = Counter()
    sources = defaultdict(set)

    def add(text: str, weight: int, source: str):
        for token in _tokenize_search_text(text, dedupe=False):
            key = token.lower()
            if key in CONTEXT_STOPWORDS or token in CONTEXT_STOPWORDS:
                continue
            scores[token] += weight
            sources[token].add(source)

    add(video_title, 6, "title")
    add(" ".join(cluster.get("tags") or []), 7, "cluster")
    add(cluster.get("label", ""), 5, "cluster")
    add(cluster.get("summary", ""), 2, "cluster")
    add(" ".join(cluster.get("top_comments") or []), 2, "cluster")
    return scores, sources


def _rank_context_tokens(scores: Counter, sources: dict[str, set[str]]) -> list[str]:
    return sorted(
        scores,
        key=lambda token: (
            0 if _is_domain_context_token(token) else 1,
            _domain_context_rank(token),
            0 if "cluster" in sources[token] else 1,
            -scores[token],
            len(token),
            token,
        ),
    )


def _contextual_query_candidates(raw_query: str, search_query: str) -> list[str]:
    match = _find_cached_cluster_match(raw_query)
    if not match:
        return []

    video_title = match.get("video_title", "")
    cluster = match.get("cluster") or {}
    scores, sources = _collect_context_token_scores(video_title, cluster)
    ranked = _rank_context_tokens(scores, sources)
    if not ranked:
        return []

    title_tokens = [token for token in ranked if "title" in sources[token]]
    cluster_tokens = [token for token in ranked if "cluster" in sources[token]]

    queries = []
    _append_unique_query(queries, _join_query_tokens(cluster_tokens[:4], limit=4))
    _append_unique_query(queries, _join_query_tokens(title_tokens[:3] + cluster_tokens[:3]))
    _append_unique_query(queries, _join_query_tokens(title_tokens[:2] + _tokenize_search_text(search_query)[:3], limit=5))
    _append_unique_query(queries, _join_query_tokens(ranked[:5], limit=5))
    _append_unique_query(queries, _join_query_tokens(title_tokens[:4], limit=4))

    logger.info(
        "[YouTube] 캐시 기반 검색 후보 생성: raw_q=%s, video_title=%s, candidates=%s",
        raw_query,
        video_title,
        queries,
    )
    return queries


def _search_query_candidates(raw_query: str, search_query: str) -> list[str]:
    queries = []
    for query in _contextual_query_candidates(raw_query, search_query):
        _append_unique_query(queries, query)

    _append_unique_query(queries, search_query)

    tokens = _tokenize_search_text(search_query)
    domain_tokens = [
        token for token in tokens
        if _is_domain_context_token(token) and token.lower() not in CONTEXT_STOPWORDS
    ]
    sentiment_tokens = [
        token for token in tokens
        if any(word in token for word in ("반대", "찬성", "우려", "비판", "의혹"))
    ]
    sentiment = sentiment_tokens[0] if sentiment_tokens else ""

    topic = ""
    for preferred in ("하향평준화", "재분배", "사회주의", "민영화", "적자", "공항"):
        if any(preferred in token for token in tokens):
            topic = preferred
            break
    if not topic and domain_tokens:
        topic = domain_tokens[0]

    has_airport = any("공항" in token for token in tokens)
    has_integration = "통합" in tokens
    if has_airport and has_integration and sentiment:
        _append_unique_query(queries, f"공항 통합 {sentiment}")
        _append_unique_query(queries, f"공항 {sentiment}")

    if topic and sentiment:
        if has_integration and topic in ("공항", "민영화", "적자"):
            _append_unique_query(queries, f"{topic} 통합 {sentiment}")
        _append_unique_query(queries, f"{topic} {sentiment}")
    if len(domain_tokens) >= 2:
        _append_unique_query(queries, " ".join(domain_tokens[:2] + ([sentiment] if sentiment else [])))
    if topic and has_integration and topic in ("공항", "민영화", "적자"):
        _append_unique_query(queries, f"{topic} 통합")

    cleaned_raw = _clean_search_query(raw_query, max_tokens=5)
    _append_unique_query(queries, cleaned_raw)

    return queries[:4]


def _is_weak_search_query(q: str) -> bool:
    compact = re.sub(r"\s+", "", q or "")
    if len(compact) < 2:
        return True
    tokens = q.split()
    if len(tokens) == 1 and tokens[0] in BROAD_SINGLE_TOKEN_QUERIES:
        return True
    return not re.search(r"[가-힣a-zA-Z0-9]", compact)


def _filter_relevant_search_items(items: list[dict], query: str) -> list[dict]:
    query_tokens = {
        token.lower()
        for token in _tokenize_search_text(query)
        if token.lower() not in CONTEXT_STOPWORDS
    }
    if not query_tokens:
        return items

    relevant_items = []
    for item in items:
        snippet = item.get("snippet", {})
        target_text = " ".join([
            snippet.get("title", ""),
            snippet.get("channelTitle", ""),
            snippet.get("description", ""),
        ])
        target_tokens = {token.lower() for token in _tokenize_search_text(target_text)}
        if query_tokens & target_tokens:
            relevant_items.append(item)

    return relevant_items


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

    search_queries = _search_query_candidates(raw_query, search_query)
    logger.info("[YouTube] 검색 요청: raw_q=%s, candidates=%s", raw_query, search_queries)

    # 1. 1차 호출: 영상 검색 (search.list)
    search_url = "https://www.googleapis.com/youtube/v3/search"
    search_params = {
        "key": YOUTUBE_API_KEY,
        "part": "snippet",
        "type": "video",
        "maxResults": 5,          # 익스텐션 UI에 보여줄 추천 영상 수
        "videoEmbeddable": "true", # 웹/앱에 퍼가기(임베드) 가능한 영상만 필터링
        "relevanceLanguage": "ko",
        "regionCode": "KR",
    }
    
    try:
        items = []
        selected_query = ""
        for candidate in search_queries:
            params = dict(search_params)
            params["q"] = candidate
            search_response = requests.get(search_url, params=params)
            if search_response.status_code != 200:
                raise HTTPException(status_code=search_response.status_code, detail="YouTube Search API 호출에 실패했습니다.")

            search_data = search_response.json()
            raw_items = search_data.get("items", [])
            items = _filter_relevant_search_items(raw_items, candidate)
            logger.info(
                "[YouTube] 검색 후보 결과: q=%s, raw_count=%s, relevant_count=%s",
                candidate,
                len(raw_items),
                len(items),
            )
            if items:
                selected_query = candidate
                break

        if not items:
            return {"videos": []}

        logger.info("[YouTube] 검색 후보 선택: raw_q=%s, selected_q=%s", raw_query, selected_query)
            
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
