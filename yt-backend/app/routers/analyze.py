import logging
from fastapi import APIRouter, BackgroundTasks
from app.models import JobResponse
from app.pipeline import get_cached_result, get_or_create_job, run_analysis

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post(
    "/{video_id}",
    summary="댓글 분석 요청",
    response_model=JobResponse,
)
def request_analysis(video_id: str, background_tasks: BackgroundTasks):
    logger.info("[Analyze] 분석 요청 수신: video_id=%s", video_id)
    cached = get_cached_result(video_id)
    if cached:
        logger.info("[Analyze] 캐시 반환: video_id=%s", video_id)
        return JobResponse(
            job_id="cached", video_id=video_id, status="done",
            progress=100, message="캐시된 결과 반환", result=cached,
        )

    job, created = get_or_create_job(video_id)
    if not created:
        logger.info("[Analyze] 기존 작업 재사용: video_id=%s, job_id=%s, status=%s", video_id, job["job_id"], job["status"])
        return JobResponse(
            job_id=job["job_id"], video_id=video_id,
            status=job["status"], progress=job["progress"],
            message=job["message"] or "이미 진행 중인 분석 작업이 있어 해당 job을 재사용합니다.",
        )

    background_tasks.add_task(run_analysis, job["job_id"], video_id)
    logger.info("[Analyze] 신규 작업 큐 등록 예약: video_id=%s, job_id=%s", video_id, job["job_id"])
    return JobResponse(
        job_id=job["job_id"], video_id=video_id, status="pending",
        progress=0, message="분석 작업이 시작되었습니다.",
    )
