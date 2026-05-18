from fastapi import APIRouter, BackgroundTasks
from app.models import JobResponse
from app.pipeline import get_cached_result, create_job, run_analysis

router = APIRouter()


@router.post(
    "/{video_id}",
    summary="댓글 분석 요청",
    description="""
영상 ID를 받아 댓글 군집 분석을 요청합니다.

**캐시 히트**: 이미 분석된 영상이면 결과를 즉시 반환합니다 (`cached: true`)

**캐시 미스**: 백그라운드 분석을 시작하고 `job_id`를 반환합니다.
`/status/{job_id}` 로 2초마다 폴링하세요.

> CPU 환경에서 3000개 댓글 기준 임베딩 약 10~15분 소요됩니다.
    """,
    response_model=JobResponse,
)
def request_analysis(video_id: str, background_tasks: BackgroundTasks):
    # 캐시 확인
    cached = get_cached_result(video_id)
    if cached:
        return JobResponse(
            job_id="cached",
            video_id=video_id,
            status="done",
            progress=100,
            message="캐시된 결과 반환",
            result=cached,
        )

    # 새 작업 생성 후 백그라운드 실행
    job_id = create_job(video_id)
    background_tasks.add_task(run_analysis, job_id, video_id)

    return JobResponse(
        job_id=job_id,
        video_id=video_id,
        status="pending",
        progress=0,
        message="분석 작업이 시작되었습니다. /status/{job_id} 로 진행 상황을 확인하세요.",
    )
