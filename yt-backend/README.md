# YouTube Comment Analyzer — Backend

YouTube 댓글을 수집해 군집 분석하는 FastAPI 백엔드입니다.
크롬 익스텐션과 연동되며, Cloudflare Tunnel로 외부에 노출합니다.

---

## 기술 스택

| 역할 | 기술 |
|------|------|
| API 서버 | FastAPI |
| 임베딩 | jhgan/ko-sroberta-multitask (CPU) |
| 군집화 | DBSCAN (eps k-distance 자동 탐색) |
| 라벨링 | gemma4:e4b via Ollama (로컬) |
| 캐시/DB | SQLite (7일 자동 만료) |
| 외부 노출 | Cloudflare Tunnel |

---

## 파일 구조

```
yt-backend/
├── app/
│   ├── main.py          # FastAPI 앱 진입점, CORS 설정
│   ├── database.py      # SQLite 연결, 테이블 초기화, 7일 만료 처리
│   ├── models.py        # Pydantic 응답 모델 (Swagger 자동 생성 기반)
│   ├── pipeline.py      # 전체 분석 파이프라인 (ML 로직)
│   └── routers/
│       ├── analyze.py   # POST /analyze/{video_id}
│       ├── status.py    # GET  /status/{job_id}
│       ├── preload.py   # POST /preload
│       └── youtube.py   # GET  /youtube/search
├── requirements.txt
├── .env.example
└── README.md
```

---

## 1. 설치 및 실행

```bash
# 패키지 설치
pip install -r requirements.txt

# 환경변수 설정
cp .env.example .env
# .env 열어서 YOUTUBE_API_KEY, OLLAMA_MODEL 등 입력

# 서버 실행
uvicorn app.main:app --reload --port 8000
```

실행 후 → http://localhost:8000/docs 에서 Swagger UI 확인

---

## 2. 환경변수 (.env)

```env
# YouTube Data API v3 키 (Google Cloud Console에서 발급)
YOUTUBE_API_KEY=여기에_API_키_입력

# DB 경로 (기본값: 서버 실행 위치에 analyzer.db 생성)
DB_PATH=analyzer.db

# Ollama 설정
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=gemma4:e4b
```

---

## 3. API 엔드포인트 요약

| Method | URL | 설명 |
|--------|-----|------|
| GET | `/` | 헬스체크 |
| POST | `/analyze/{video_id}` | 댓글 분석 요청 |
| GET | `/status/{job_id}` | 작업 진행 상황 폴링 |
| POST | `/preload` | 영상 사전 분석 (관리용) |
| GET | `/youtube/search?q=검색어` | 군집 태그 기반 유튜브 영상 추천 |

---

## 4. 분석 흐름

```
익스텐션 → POST /analyze/{video_id}
  │
  ├─ 캐시 있음 → 결과 즉시 반환 (status: done, cached: true)
  │
  └─ 캐시 없음 → job_id 반환 (status: pending)
                      │
                      └─ 2초마다 GET /status/{job_id} 폴링
                              │
                              ├─ processing → progress(0~100) 표시
                              ├─ done       → result 필드에 최종 데이터
                              └─ failed     → message에 오류 내용
```

### 분석 파이프라인 단계

```
1. YouTube API     → 댓글 + 대댓글 수집
2. 텍스트 정제     → 특수문자 제거, 중복 제거
3. 임베딩          → jhgan/ko-sroberta-multitask (CPU)
4. eps 자동 탐색   → k-distance graph elbow 방법
5. DBSCAN 군집화   → 댓글 수에 따라 파라미터 자동 조정
6. LLM 라벨링      → gemma4:e4b via Ollama (군집별 label/summary/tags 생성)
7. 군집 후처리     → 4개 내외로 정리 (작은 군집 병합)
8. 시간대별 분석   → published_at 기준 8개 구간으로 분류
9. SQLite 캐시 저장
```

---

## 5. DBSCAN 파라미터 전략

댓글 수에 따라 `min_samples`와 `eps`를 자동 조정합니다.

### min_samples

| 댓글 수 | min_samples |
|--------|-------------|
| ~100개 | n × 5% (최소 3) |
| ~500개 | n × 2% (최소 5) |
| ~2000개 | n × 1% (최소 10) |
| 2000개+ | n × 0.8% (최소 15, 최대 20) |

### eps (k-distance graph elbow 방법)

k번째 이웃까지의 거리를 정렬했을 때 가장 급격히 꺾이는 지점(elbow)을 eps로 사용합니다.
데이터에 맞게 자동 결정되며, 댓글 수별로 안전 범위 내에 클램핑합니다.

| 댓글 수 | eps 범위 |
|--------|---------|
| ~500개 | 0.15 ~ 0.25 |
| ~2000개 | 0.20 ~ 0.30 |
| 2000개+ | 0.25 ~ 0.35 |

---

## 6. 군집 라벨링 (gemma4:e4b via Ollama)

군집별 대표 댓글 5개를 gemma4:e4b에 보내서 `label` / `summary` / `sentiment` / `tags`를 생성합니다.
노이즈 군집도 LLM으로 요약하며, 라벨만 "분류 안 됨"으로 고정됩니다.

gemma4는 thinking 모드가 있지만, system 프롬프트에 `<|think|>` 토큰을 넣지 않으면 비활성화되어 빠르게 응답합니다.
Ollama 호출 실패 시 TF-IDF 키워드 방식으로 자동 폴백합니다.

---

## 7. 캐시 전략

- 분석 결과는 SQLite(`analyzer.db`)에 저장
- **7일 후 자동 만료** (서버 시작 시 정리)
- 같은 영상 재요청 시 즉시 반환
- 같은 영상에 대한 중복 job 방지 (UNIQUE INDEX)
- `POST /preload` 로 인기 영상 미리 분석 가능

---

## 8. 크롬 익스텐션 연동 예시

```javascript
const API_BASE = "https://osspapi.butterflyjin.kr";

async function analyzeVideo(videoId) {
  const res = await fetch(`${API_BASE}/analyze/${videoId}`, { method: "POST" });
  const data = await res.json();

  // 캐시 즉시 반환
  if (data.status === "done") return data.result;

  // 폴링
  const jobId = data.job_id;
  while (true) {
    await new Promise(r => setTimeout(r, 2000));
    const poll = await fetch(`${API_BASE}/status/${jobId}`).then(r => r.json());

    updateProgressBar(poll.progress); // 진행바 업데이트

    if (poll.status === "done")   return poll.result;
    if (poll.status === "failed") throw new Error(poll.message);
  }
}
```

---

## 9. Cloudflare Tunnel 설정 (아치 리눅스)

포트포워딩 없이 로컬 서버를 외부에 노출합니다.

### 설치

```bash
# AUR (yay 있으면)
yay -S cloudflared

# 또는 바이너리 직접
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o cloudflared
chmod +x cloudflared
sudo mv cloudflared /usr/local/bin/
```

### 터널 설정

```bash
# Cloudflare 로그인
cloudflared tunnel login

# 터널 생성 (UUID 복사)
cloudflared tunnel create ossp-api

# DNS 연결
cloudflared tunnel route dns ossp-api osspapi.butterflyjin.kr
```

### 설정 파일

```yaml
# ~/.cloudflared/config.yml
tunnel: 여기에-tunnel-UUID
credentials-file: /home/유저명/.cloudflared/여기에-tunnel-UUID.json

ingress:
  - hostname: osspapi.butterflyjin.kr
    service: http://localhost:8000
  - service: http_status:404
```

### systemd 등록

```bash
sudo cloudflared service install
sudo systemctl enable cloudflared
sudo systemctl start cloudflared
```

---

## 10. FastAPI systemd 서비스 등록

서버 재시작 후에도 자동 실행되도록 등록합니다.

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
StandardOutput=append:/home/nabi/OSSP_CSC4004_03_10/yt-backend/server.log
StandardError=append:/home/nabi/OSSP_CSC4004_03_10/yt-backend/server.log

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable yt-backend
sudo systemctl start yt-backend

# 로그 확인
tail -f /home/nabi/OSSP_CSC4004_03_10/yt-backend/server.log
```

완료 후 https://osspapi.butterflyjin.kr/docs 접근되면 성공!