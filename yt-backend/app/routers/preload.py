import logging
from fastapi import APIRouter, BackgroundTasks
from app.models import PreloadRequest
from app.pipeline import get_cached_result, get_or_create_job, run_analysis

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/", summary="영상 사전 분석 (관리용)")
def preload_videos(request: PreloadRequest, background_tasks: BackgroundTasks):
    logger.info("[Preload] 사전 분석 요청 수신: count=%s", len(request.video_ids))
    results = []
    for video_id in request.video_ids:
        video_id = video_id.strip()
        cached = get_cached_result(video_id)
        if cached:
            logger.info("[Preload] 캐시로 skip: video_id=%s", video_id)
            results.append({"video_id": video_id, "action": "skipped (already cached)"})
            continue

        job, created = get_or_create_job(video_id)
        if created:
            background_tasks.add_task(run_analysis, job["job_id"], video_id)
            logger.info("[Preload] 신규 작업 큐 등록 예약: video_id=%s, job_id=%s", video_id, job["job_id"])
            results.append({"video_id": video_id, "action": "queued", "job_id": job["job_id"]})
        else:
            logger.info("[Preload] 기존 작업 재사용: video_id=%s, job_id=%s", video_id, job["job_id"])
            results.append({"video_id": video_id, "action": "already queued", "job_id": job["job_id"]})

    return {"queued_count": len([r for r in results if r["action"] == "queued"]), "detail": results}
