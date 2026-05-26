from fastapi import APIRouter, BackgroundTasks
from app.models import PreloadRequest
from app.pipeline import get_cached_result, get_or_create_job, run_analysis

router = APIRouter()

@router.post("/", summary="영상 사전 분석 (관리용)")
def preload_videos(request: PreloadRequest, background_tasks: BackgroundTasks):
    results = []
    for video_id in request.video_ids:
        cached = get_cached_result(video_id)
        if cached:
            results.append({"video_id": video_id, "action": "skipped (already cached)"})
            continue

        job, created = get_or_create_job(video_id)
        if created:
            background_tasks.add_task(run_analysis, job["job_id"], video_id)
            results.append({"video_id": video_id, "action": "queued", "job_id": job["job_id"]})
        else:
            results.append({"video_id": video_id, "action": "already queued", "job_id": job["job_id"]})

    return {"queued_count": len([r for r in results if r["action"] == "queued"]), "detail": results}