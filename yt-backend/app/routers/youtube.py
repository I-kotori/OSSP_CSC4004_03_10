# app/routers/youtube.py 새로 생성

import os
import requests
from fastapi import APIRouter, HTTPException, Query
from app.models import YouTubeSearchResponse, VideoResponse

router = APIRouter()

# .env 파일에 등록된 YOUTUBE_API_KEY를 가져옵니다.
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

@router.get(
    "/search",
    summary="여론 군집별 관련 유튜브 영상 검색 및 추천",
    description="클러스터 라벨과 태그가 조합된 쿼리를 받아 관련 영상 리스트를 반환합니다.",
    response_model=YouTubeSearchResponse
)
def search_youtube_videos(q: str = Query(..., description="검색 쿼리 스트링")):
    if not YOUTUBE_API_KEY:
        raise HTTPException(status_code=500, detail="서버에 YOUTUBE_API_KEY가 설정되지 않았습니다.")
        
    # 1. 1차 호출: 영상 검색 (search.list)
    search_url = "https://www.googleapis.com/youtube/v3/search"
    search_params = {
        "key": YOUTUBE_API_KEY,
        "q": q,
        "part": "snippet",
        "type": "video",
        "maxResults": 5,          # 익스텐션 UI에 보여줄 추천 영상 수
        "videoEmbeddable": "true" # 웹/앱에 퍼가기(임베드) 가능한 영상만 필터링
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
                views_str = f"조회수 {raw_views // 10000}만회"
            elif raw_views >= 1000:
                views_str = f"조회수 {raw_views // 1000}천회"
            else:
                views_str = f"조회수 {raw_views}회"
                
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
