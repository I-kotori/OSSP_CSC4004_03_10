# YouTube Comment Analyzer — Backend

YouTube 댓글을 수집해 군집 분석하는 FastAPI 백엔드입니다.
크롬 익스텐션과 연동되며, Cloudflare Tunnel로 외부에 노출합니다.

---

## 파일 구조

```
yt-backend/
├── app/
│   ├── main.py          # FastAPI 앱 진입점, CORS 설정
│   ├── database.py      # SQLite 연결, 테이블 초기화, 7일 만료 처리
│   ├── models.py        # Pydantic 응답 모델 (Swagger 자동 생성 기반)
│   ├── pipeline.py      # 전체 분석 파이프라인 (ML 로직 여기에 구현)
│   └── routers/
│       ├── analyze.py   # POST /analyze/{video_id}
│       ├── status.py    # GET  /status/{job_id}
│       └── preload.py   # POST /preload
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
# .env 파일 열어서 YOUTUBE_API_KEY 입력

# 서버 실행
uvicorn app.main:app --reload --port 8000
```

실행 후 http://localhost:8000/docs 에서 Swagger UI 확인 가능합니다.
이 페이지가 곧 API 명세서입니다. 별도 작성 불필요.

---

## 2. API 엔드포인트 요약

| Method | URL | 설명 |
|--------|-----|------|
| GET | `/` | 헬스체크 |
| POST | `/analyze/{video_id}` | 댓글 분석 요청 |
| GET | `/status/{job_id}` | 작업 진행 상황 폴링 |
| POST | `/preload` | 영상 사전 분석 (관리용) |

---

## 3. 분석 흐름

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

---

## 4. 크롬 익스텐션 연동 예시 (content.js)

```javascript
const API_BASE = "https://osspapi.butterflyjin.kr"; // Cloudflare Tunnel 도메인

async function analyzeVideo(videoId) {
  // 1. 분석 요청
  const res = await fetch(`${API_BASE}/analyze/${videoId}`, { method: "POST" });
  const data = await res.json();

  // 캐시 즉시 반환
  if (data.status === "done") return data.result;

  // 2. 폴링
  const jobId = data.job_id;
  while (true) {
    await new Promise(r => setTimeout(r, 2000));
    const poll = await fetch(`${API_BASE}/status/${jobId}`).then(r => r.json());

    // progress로 진행바 업데이트 가능
    updateProgressBar(poll.progress);

    if (poll.status === "done")    return poll.result;
    if (poll.status === "failed")  throw new Error(poll.message);
  }
}
```

---

## 5. pipeline.py — TODO 채우기

`pipeline.py` 안에 `# TODO` 주석이 달린 곳이 3군데 있습니다.
주석 해제만 하면 실제 동작합니다.

### TODO 1: 댓글 수집 (fetch_comments)

```python
from googleapiclient.discovery import build
api_key = os.getenv("YOUTUBE_API_KEY")
youtube = build("youtube", "v3", developerKey=api_key)
```

### TODO 2: 임베딩 (embed_comments)

```python
from sentence_transformers import SentenceTransformer
model = SentenceTransformer('jhgan/ko-sroberta-multitask')
return model.encode(texts, show_progress_bar=True, batch_size=32)
```

### TODO 3: DBSCAN (cluster_comments)

```python
from sklearn.cluster import DBSCAN
n = len(embeddings)
min_samples = max(5, min(30, int(n * 0.008)))
eps = find_best_eps(embeddings, min_samples)
return DBSCAN(eps=eps, min_samples=min_samples, metric='cosine').fit_predict(embeddings).tolist()
```

---

## 6. DBSCAN 파라미터 전략

### eps 자동 탐색 (k-distance graph)
k번째 이웃까지의 거리를 정렬했을 때 가장 급격히 꺾이는 지점(elbow)을 eps로 사용합니다.
데이터에 맞게 자동으로 결정되는 표준적인 방법입니다.

### min_samples 비율 조정
전체 댓글 수의 0.8%, 최소 5 최대 30으로 설정합니다.

### 군집 수 후처리
DBSCAN 결과 군집이 4개보다 많으면 작은 것들을 '기타 의견'으로 합칩니다.
UI에서 4개 내외로 깔끔하게 보여주기 위한 후처리입니다.

---

## 7. 군집 라벨링 (gemma4:e4b via Ollama)

군집별 대표 댓글 5개를 gemma4:e4b에 보내서 라벨/감성/태그를 생성합니다.
군집당 1회 호출이라 부담이 적습니다.

gemma4는 thinking 모드가 있지만,
system 프롬프트에 `<|think|>` 토큰을 넣지 않으면 비활성화되어 빠르게 응답합니다.

Ollama 호출 실패 시 TF-IDF 키워드 방식으로 자동 폴백합니다.

---

## 8. 캐시 전략

- 분석 결과는 SQLite(`analyzer.db`)에 저장
- **7일 후 자동 만료** (서버 시작 시 정리)
- 같은 영상 재요청 시 즉시 반환
- `POST /preload` 로 인기 영상 미리 분석 가능

---

## 9. Cloudflare Tunnel 설정 (아치 리눅스)

포트포워딩 없이 로컬 서버를 외부에 노출합니다.

### 설치

```bash
# yay 있으면
yay -S cloudflared

# 또는 바이너리 직접 설치
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o cloudflared
chmod +x cloudflared
sudo mv cloudflared /usr/local/bin/
```

### 터널 생성

```bash
# Cloudflare 로그인 (브라우저 열림 → butterflyjin.kr 선택)
cloudflared tunnel login

# 터널 생성 (UUID가 출력됨 — 복사해두기)
cloudflared tunnel create ossp-api

# DNS 연결
cloudflared tunnel route dns ossp-api osspapi.butterflyjin.kr
```

### 설정 파일 작성

```bash
mkdir -p ~/.cloudflared
nano ~/.cloudflared/config.yml
```

```yaml
tunnel: 여기에-tunnel-UUID
credentials-file: /home/유저명/.cloudflared/여기에-tunnel-UUID.json

ingress:
  - hostname: osspapi.butterflyjin.kr
    service: http://localhost:8000
  - service: http_status:404
```

### 실행 및 자동 시작 등록

```bash
# 테스트 실행
cloudflared tunnel run ossp-api

# systemd 등록 (서버 재시작 후에도 자동 실행)
sudo cloudflared service install
sudo systemctl enable cloudflared
sudo systemctl start cloudflared

# 상태 확인
sudo systemctl status cloudflared
```

완료 후 https://osspapi.butterflyjin.kr/docs 접근되면 성공입니다.
