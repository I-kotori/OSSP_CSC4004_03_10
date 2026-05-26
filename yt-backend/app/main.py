from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv  # 추가

load_dotenv()  # 추가
from app.routers import analyze, status, preload, youtube # 찬홍 수정
from app.database import init_db
from app.pipeline import start_worker

app = FastAPI(
    title="YouTube Comment Analyzer API",
    description="""
## YouTube 댓글 여론 분석 백엔드

크롬 익스텐션에서 호출하는 댓글 군집 분석 API입니다.

### 분석 흐름
1. `POST /analyze/{video_id}` 로 분석 요청
2. 캐시된 결과가 있으면 **즉시 반환** (`cached: true`)
3. 없으면 `job_id` 반환 후 백그라운드에서 분석 시작
4. `GET /status/{job_id}` 로 2초마다 폴링
5. `status: done` 이 되면 `result` 필드에 분석 데이터 포함

### 기술 스택
- **임베딩**: jhgan/ko-sroberta-multitask (CPU)
- **군집화**: DBSCAN (eps 자동 탐색)
- **라벨링**: gemma4:e4b via Ollama (로컬)
- **캐시**: SQLite (7일 자동 만료)
    """,
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(analyze.router, prefix="/analyze", tags=["분석 요청"])
app.include_router(status.router, prefix="/status", tags=["작업 상태"])
app.include_router(preload.router, prefix="/preload", tags=["사전 처리 (관리용)"])
# 찬홍 수정
app.include_router(youtube.router, prefix="/youtube", tags=["유튜브 추천"])

@app.on_event("startup")
def on_startup():
    init_db()
    start_worker()


@app.get("/", tags=["헬스체크"])
def health_check():
    return {"status": "ok", "message": "YouTube Comment Analyzer API is running"}
