# 여론 편향 완화를 위한 댓글 시각화 시스템 - Backend

YouTube 댓글을 수집하고, 문장 임베딩과 DBSCAN 기반 군집화를 통해 댓글 여론을 요약하는 FastAPI 백엔드입니다.  
Chrome Extension/Frontend에서 분석을 요청하면 백엔드는 작업 큐에 분석을 등록하고, 진행 상태와 최종 분석 결과를 API로 반환합니다.

## Service URL

- API 문서: `https://osspapi.butterflyjin.kr/docs`
- 로컬 개발: `http://localhost:8000/docs`

## Tech Stack

| 역할 | 기술 |
| --- | --- |
| Language | Python |
| Framework | FastAPI |
| API Server | Uvicorn |
| YouTube Data | Google YouTube Data API v3 |
| Embedding | Sentence-Transformers `jhgan/ko-sroberta-multitask` |
| Clustering | Scratch NumPy DBSCAN, scikit-learn fallback/benchmark |
| Labeling | Ollama `gemma4:e4b` |
| DB/Cache | SQLite |
| Logging | Python logging + rotating file handler |
| Deploy/Expose | systemd + Cloudflare Tunnel |

## 주요 기능

- YouTube 영상 ID 기반 댓글 및 대댓글 수집
- 짧은 댓글, URL, 전화번호, 광고성 댓글, 중복 댓글 필터링
- 대댓글 분석 시 부모 댓글 문맥을 함께 임베딩
- Sentence-Transformers 기반 한국어 댓글 임베딩 생성
- 직접 구현한 Scratch NumPy DBSCAN으로 댓글 군집화
- scikit-learn DBSCAN과 성능/결과 비교 가능한 벤치마크 스크립트 제공
- Ollama 로컬 LLM을 사용한 군집 라벨, 요약, 감성, 태그 생성
- Ollama 실패 시 키워드/TF-IDF 기반 fallback 라벨 생성
- 분석 결과 캐시, 임베딩 캐시, 작업 상태 관리
- 분석 단계별 소요 시간과 DBSCAN 통계 기록
- 군집 키워드 기반 YouTube 추천 영상 검색 API 제공

## 폴더 구조

```txt
yt-backend/
  app/
    main.py              # FastAPI 앱 진입점, CORS, 라우터 등록
    database.py          # SQLite 연결, 테이블 초기화, 캐시 정리
    models.py            # Pydantic 응답 모델
    pipeline.py          # 댓글 수집, 전처리, 임베딩, 군집화, 라벨링 파이프라인
    dbscan_scratch.py    # Scratch NumPy cosine DBSCAN 구현
    logging_config.py    # 파일 로그/콘솔 로그 설정
    routers/
      analyze.py         # POST /analyze/{video_id}
      status.py          # GET /status/{job_id}
      preload.py         # POST /preload
      youtube.py         # GET /youtube/search
  scripts/
    benchmark_dbscan.py  # DBSCAN/PCA 벤치마크 스크립트
  requirements.txt
  README.md
```

## 시작 방법

### 1. 가상환경 및 패키지 설치

```bash
cd ~/OSSP_CSC4004_03_10/yt-backend

python -m venv venv
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Environment Values

`.env`는 커밋하지 않습니다. 필요한 값만 로컬에 생성해서 사용합니다.

```bash
cp .env.example .env
```

`.env.example`이 없다면 아래 형식으로 `.env`를 직접 생성합니다.

```env
YOUTUBE_API_KEY=your_youtube_data_api_key

DB_PATH=analyzer.db

OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=gemma4:e4b

EMBEDDING_MODEL_NAME=jhgan/ko-sroberta-multitask
EMBEDDING_BATCH_SIZE=32
EMBEDDING_CACHE_ENABLED=1

APP_LOG_PATH=server.log
APP_LOG_LEVEL=INFO
```

### 3. Ollama 준비

```bash
ollama serve
ollama pull gemma4:e4b
```

동작 확인:

```bash
curl -s http://localhost:11434/api/generate \
  -d '{"model":"gemma4:e4b","prompt":"한국어로 테스트라고만 답해줘","stream":false}'
```

### 4. 서버 실행

```bash
uvicorn app.main:app --reload --port 8000
```

실행 후 `http://localhost:8000/docs`에서 Swagger UI를 확인할 수 있습니다.

## API 요약

| Method | URL | 설명 |
| --- | --- | --- |
| `GET` | `/` | 헬스 체크 |
| `POST` | `/analyze/{video_id}` | 영상 댓글 분석 요청 |
| `GET` | `/status/{job_id}` | 분석 작업 진행 상태 조회 |
| `POST` | `/preload` | 여러 영상을 미리 분석 요청 |
| `GET` | `/youtube/search?q=검색어` | 군집 라벨/태그 기반 추천 영상 검색 |

## 분석 흐름

```txt
Frontend/Extension
  -> POST /analyze/{video_id}
      -> 분석 결과 캐시가 있으면 즉시 done 반환
      -> 캐시가 없으면 job_id 반환
          -> GET /status/{job_id} polling
              -> pending/processing: progress, message 반환
              -> done: result 반환
              -> failed: error message 반환
```

파이프라인 내부 흐름:

```txt
1. YouTube API로 댓글/대댓글 수집
2. 댓글 정제 및 저품질 댓글 필터링
3. 대댓글에 부모 댓글 문맥 추가
4. Sentence-Transformers 임베딩 생성
5. 임베딩 캐시 조회/저장
6. Scratch NumPy DBSCAN 군집화
7. Ollama LLM으로 군집 라벨/요약/감성/태그 생성
8. 작은 군집 정리 및 noise/others 처리
9. 시간대별 댓글 여론 분포 계산
10. SQLite에 분석 결과 캐시 저장
```

## DBSCAN 정책

현재 백엔드는 cosine distance 기반 DBSCAN을 사용합니다. 기본 구현은 `app/dbscan_scratch.py`의 Scratch NumPy DBSCAN이며, 실패 시 scikit-learn DBSCAN으로 fallback할 수 있도록 구성되어 있습니다.

### min_samples

| 댓글 수 | min_samples |
| --- | --- |
| 100개 미만 | `max(3, n * 0.05)` |
| 500개 미만 | `max(5, n * 0.02)` |
| 2000개 미만 | `max(10, n * 0.01)` |
| 2000개 이상 | `max(15, min(20, n * 0.008))` |

### eps

`k-distance graph`의 elbow 지점을 기반으로 자동 추정합니다. 댓글 수가 많을수록 안전 범위를 넓게 잡되, 너무 큰 eps로 모든 댓글이 하나의 군집에 합쳐지는 상황을 막기 위해 범위를 제한합니다.

| 댓글 수 | eps 범위 |
| --- | --- |
| 500개 미만 | `0.15 ~ 0.25` |
| 2000개 미만 | `0.20 ~ 0.30` |
| 2000개 이상 | `0.25 ~ 0.35` |

## 캐시 구조

| 테이블 | 역할 |
| --- | --- |
| `analysis_cache` | 영상별 최종 분석 결과 JSON 캐시 |
| `jobs` | 비동기 분석 작업 상태 저장 |
| `embedding_cache` | `text_hash + model_name` 기준 임베딩 벡터 재사용 |

캐시 정책:

- `analysis_cache`는 같은 영상 재요청 시 즉시 결과를 반환합니다.
- `embedding_cache`는 분석 결과 캐시를 지워도 유지되어 같은 댓글의 임베딩을 재사용합니다.
- 서버 재시작 시 `pending`, `processing` 상태로 남은 작업은 `failed`로 정리합니다.
- 오래된 분석 결과 캐시는 초기화 시 정리합니다.

## 벤치마크

DBSCAN 성능과 PCA 차원 축소 영향을 비교하기 위한 스크립트를 제공합니다.

### 실제 영상 댓글 저장 및 벤치마크

```bash
python scripts/benchmark_dbscan.py \
  --video-id amXUKRnUueM \
  --save-comments benchmark_outputs/amX_comments.json \
  --save-embeddings benchmark_outputs/amX.npy \
  --json-out benchmark_outputs/amX_dbscan.json
```

### 저장된 댓글 JSON으로 재실험

```bash
python scripts/benchmark_dbscan.py \
  --comments-json benchmark_outputs/amX_comments.json \
  --save-embeddings benchmark_outputs/amX.npy \
  --json-out benchmark_outputs/amX_from_comments.json
```

### 저장된 임베딩으로 PCA 차원별 비교

```bash
python scripts/benchmark_dbscan.py \
  --embeddings benchmark_outputs/amX.npy \
  --pca-dims 64,128,256,384,768 \
  --json-out benchmark_outputs/amX_pca_dbscan.json
```

`benchmark_outputs/`는 로컬 실험 산출물이므로 Git에 커밋하지 않습니다.

## 운영 로그

기본 로그 파일은 `server.log`입니다. 환경변수로 위치와 보관 정책을 변경할 수 있습니다.

```env
APP_LOG_PATH=server.log
APP_LOG_LEVEL=INFO
APP_LOG_MAX_BYTES=10485760
APP_LOG_BACKUP_COUNT=5
```

로그 확인:

```bash
tail -f server.log
```

systemd 환경에서는 다음 명령으로도 확인할 수 있습니다.

```bash
journalctl -u yt-backend -f
```

## systemd 예시

```ini
# /etc/systemd/system/yt-backend.service
[Unit]
Description=YouTube Comment Analyzer FastAPI
After=network.target cloudflared.service

[Service]
Type=simple
User=nabi
WorkingDirectory=/home/nabi/OSSP_CSC4004_03_10/yt-backend
ExecStart=/home/nabi/OSSP_CSC4004_03_10/yt-backend/venv/bin/uvicorn app.main:app --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

적용:

```bash
sudo systemctl daemon-reload
sudo systemctl enable yt-backend
sudo systemctl restart yt-backend
```

## Cloudflare Tunnel 예시

```yaml
# ~/.cloudflared/config.yml
tunnel: your-tunnel-uuid
credentials-file: /home/nabi/.cloudflared/your-tunnel-uuid.json

ingress:
  - hostname: osspapi.butterflyjin.kr
    service: http://localhost:8000
  - service: http_status:404
```

```bash
sudo systemctl enable cloudflared
sudo systemctl restart cloudflared
```

## 협업 규칙

커밋 타입, 브랜치 네이밍, PR 규칙은 저장소 루트의 `README.md`에 있는 프로젝트 공통 규칙을 따릅니다.

백엔드 작업 시에는 다음 산출물이 커밋에 포함되지 않도록 확인합니다.

- `.env`
- `analyzer.db`
- `server.log`
- `benchmark_outputs/`
- `venv/`

## 보안

- `.env`, API Key, DB 파일, 로그 파일은 커밋하지 않습니다.
- 공유가 필요한 환경변수는 `.env.example`에 키 이름만 기록합니다.
- `analyzer.db`, `server.log`, `benchmark_outputs/`는 로컬 실행 산출물로 관리합니다.
