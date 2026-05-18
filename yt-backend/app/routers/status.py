from fastapi import APIRouter, HTTPException
from app.models import JobResponse
from app.pipeline import get_job, get_cached_result

router = APIRouter()


@router.get(
    "/{job_id}",
    summary="분석 작업 상태 조회",
    description="""
`job_id`로 분석 진행 상황을 조회합니다.

| status | 의미 |
|--------|------|
| `pending` | 대기 중 |
| `processing` | 분석 진행 중 (`progress` 로 0~100 확인) |
| `done` | 완료 — `result` 필드에 분석 데이터 포함 |
| `failed` | 실패 — `message` 에 오류 내용 |

**폴링 권장 주기**: 2초
    """,
    response_model=JobResponse,
)
def get_job_status(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"job_id '{job_id}' 를 찾을 수 없습니다.")

    result = None
    if job["status"] == "done":
        result = get_cached_result(job["video_id"])

    return JobResponse(
        job_id=job["job_id"],
        video_id=job["video_id"],
        status=job["status"],
        progress=job["progress"],
        message=job["message"],
        result=result,
    )
