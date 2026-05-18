from fastapi import APIRouter, BackgroundTasks
from app.models import PreloadRequest
from app.pipeline import get_cached_result, create_job, run_analysis

router = APIRouter()


@router.post(
    "/",
    summary="영상 사전 분석 (관리용)",
    description="""
조회수가 많은 영상을 미리 분석해 캐시에 저장합니다.

서버 관리자가 직접 호출하는 용도입니다.
이미 캐시된 영상은 건너뜁니다.

**활용 예시**: 서버 시작 시 인기 영상 목록을 미리 처리해두면
익스텐션 사용자가 결과를 즉시 받을 수 있습니다.
    """,
)
def preload_videos(request: PreloadRequest, background_tasks: BackgroundTasks):
    results = []
    for video_id in request.video_ids:
        cached = get_cached_result(video_id)
        if cached:
            results.append({"video_id": video_id, "action": "skipped (already cached)"})
            continue

        job_id = create_job(video_id)
        background_tasks.add_task(run_analysis, job_id, video_id)
        results.append({"video_id": video_id, "action": "queued", "job_id": job_id})

    return {
        "queued_count": len([r for r in results if r["action"] == "queued"]),
        "detail": results,
    }
