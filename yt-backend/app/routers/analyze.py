from fastapi import APIRouter, BackgroundTasks
from app.models import JobResponse
from app.pipeline import get_cached_result, get_or_create_job, run_analysis

router = APIRouter()

@router.post(
    "/{video_id}",
    summary="댓글 분석 요청",
    response_model=JobResponse,
)
def request_analysis(video_id: str, background_tasks: BackgroundTasks):
    cached = get_cached_result(video_id)
    if cached:
        return JobResponse(
            job_id="cached", video_id=video_id, status="done",
            progress=100, message="캐시된 결과 반환", result=cached,
        )

    job, created = get_or_create_job(video_id)
    if not created:
        return JobResponse(
            job_id=job["job_id"], video_id=video_id,
            status=job["status"], progress=job["progress"],
            message=job["message"] or "이미 진행 중인 분석 작업이 있어 해당 job을 재사용합니다.",
        )

    background_tasks.add_task(run_analysis, job["job_id"], video_id)
    return JobResponse(
        job_id=job["job_id"], video_id=video_id, status="pending",
        progress=0, message="분석 작업이 시작되었습니다.",
    )