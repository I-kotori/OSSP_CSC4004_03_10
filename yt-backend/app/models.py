from pydantic import BaseModel, Field
from typing import List, Optional


class ClusterResult(BaseModel):
    id: str = Field(..., example="cluster_0", description="군집 식별자 (DBSCAN 번호, 'noise', 'others')")
    label: str = Field(..., example="기술 혁신 기대", description="gemma4:e4b 또는 TF-IDF가 생성한 라벨")
    summary: Optional[str] = Field(None, description="군집 여론 한 줄 요약")
    sentiment: str = Field(..., example="positive", description="positive | negative | neutral")
    percent: float = Field(..., example=38.0, description="전체 댓글 중 비율 (%)")
    comment_count: int = Field(..., example=1200, description="해당 군집 댓글 수")
    top_comments: List[str] = Field(..., description="대표 댓글 최대 5개")
    tags: List[str] = Field(..., example=["기술혁신", "생산성", "미래"], description="군집 키워드 태그")


class TimelinePoint(BaseModel):
    label: str = Field(..., example="0-2시간", description="시간 구간 라벨")
    clusters: dict = Field(
        ...,
        example={"cluster_0": 40.0, "cluster_1": 35.0, "noise": 25.0},
        description="군집별 비율. 군집 수가 가변이므로 딕셔너리로 처리"
    )


class AnalysisResult(BaseModel):
    video_id: str
    video_title: Optional[str] = None
    total_comments: int
    cluster_count: int = Field(..., description="실제 군집 수 (noise, others 제외)")
    clusters: List[ClusterResult]
    timeline: List[TimelinePoint]
    analyzed_at: str
    cached: bool = Field(..., description="캐시에서 반환된 결과인지 여부")


class JobResponse(BaseModel):
    job_id: str
    video_id: str
    status: str = Field(
        ...,
        example="pending",
        description="pending | processing | done | failed"
    )
    progress: int = Field(..., example=30, description="진행률 0~100")
    message: Optional[str] = None
    result: Optional[AnalysisResult] = Field(None, description="status=done 일 때 결과 포함")


class PreloadRequest(BaseModel):
    video_ids: List[str] = Field(
        ...,
        description="미리 분석할 영상 ID 목록",
        example=["ztI95gJPdlk", "YeDePe7hJ4Q"]
    )

# 찬홍 추가 - 비디오 부분 API

class VideoResponse(BaseModel):
    title: str = Field(..., description="유튜브 영상 제목")
    channel: str = Field(..., description="채널명")
    thumbnail: str = Field(..., description="영상 썸네일 URL")
    views: str = Field(..., description="가공된 조회수 문자열 (예: 12만회)")
    likes: str = Field(..., description="가공된 좋아요수 문자열 (예: 1.2만)")
    url: str = Field(..., description="유튜브 영상 상세 URL")

class YouTubeSearchResponse(BaseModel):
    videos: List[VideoResponse] = Field(..., description="추천 영상 리스트")
