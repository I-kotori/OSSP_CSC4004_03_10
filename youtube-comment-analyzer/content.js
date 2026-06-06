let isPanelOpen = false;
let currentTab = "cluster";
let selectedTimeSlot = null;

let analysisData = null;
let analysisLoading = false;
let analysisError = null;

const API_BASE = "https://osspapi.butterflyjin.kr";

const tabs = [
  { id: "cluster", label: "여론 군집" },
  { id: "timeline", label: "시간대별 분석" },
  { id: "videos", label: "여론별 영상" },
  { id: "balance", label: "의견 균형 보기" },
];


const PIE = {
  cx: 160,
  cy: 160,
  r: 112,
};

function getVideoId() {
  const url = new URL(location.href);
  return url.searchParams.get("v");
}


function createSlices() {
  if (!analysisData?.clusters) return [];

  let currentDeg = 0;

  const colors = [
    {
      color: "#4ade80",
      colorBg: "rgba(74,222,128,0.13)",
      colorBorder: "rgba(74,222,128,0.4)",
    },
    {
      color: "#f87171",
      colorBg: "rgba(248,113,113,0.13)",
      colorBorder: "rgba(248,113,113,0.4)",
    },
    {
      color: "#818cf8",
      colorBg: "rgba(129,140,248,0.13)",
      colorBorder: "rgba(129,140,248,0.4)",
    },
    {
      color: "#94a3b8",
      colorBg: "rgba(148,163,184,0.13)",
      colorBorder: "rgba(148,163,184,0.4)",
    },
  ];

  return analysisData.clusters.map((cluster, index) => {
    const start = currentDeg;
    const sweep = (cluster.percent / 100) * 360;
    const end = start + sweep;

    currentDeg = end;

    const colorSet = colors[index % colors.length];

    return {
      id: cluster.id,
      label: cluster.label,
      percent: cluster.percent,
      count: `${cluster.comment_count.toLocaleString()}개 댓글`,
      tags: cluster.tags || [],
      topComment: cluster.top_comments?.[0] || "대표 댓글 없음",
      ...colorSet,
      start,
      end,
      lp: polarToXY(PIE.cx, PIE.cy, PIE.r + 30, (start + end) / 2),
    };
  });
}

function polarToXY(cx, cy, r, deg) {
  const rad = ((deg - 90) * Math.PI) / 180;

  return {
    x: cx + r * Math.cos(rad),
    y: cy + r * Math.sin(rad),
  };
}

//실제 분석 청
async function fetchAnalysis(videoId) {
  try {
    analysisLoading = true;
    analysisError = null;

    const url = `${API_BASE}/analyze/${videoId}`;

    console.log("API_BASE:", API_BASE);
    console.log("요청 URL:", url);

    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
    });

    console.log("응답 상태:", response.status);

    const text = await response.text();

    console.log("응답 내용:", text);

    const data = JSON.parse(text);

    console.log("파싱 결과:", data);

    if (data.result) {
      analysisData = data.result;

      await loadClusterVideos();

      analysisLoading = false;

      const panel = document.querySelector(".yt-comment-analysis-panel");

if (panel) {

  panel.innerHTML = renderPanel();

  panel.querySelector(".analysis-close")
    ?.addEventListener("click", () => {
      closePanel();
      isPanelOpen = false;
    });

  panel.querySelectorAll(".analysis-tabs button")
    .forEach((button) => {

      button.addEventListener("click", () => {

        currentTab = button.dataset.tab;

        panel.querySelectorAll(".analysis-tabs button")
          .forEach((tabButton) => {

            tabButton.classList.toggle(
              "active",
              tabButton.dataset.tab === currentTab
            );

          });

        panel.querySelector(".analysis-body").innerHTML =
          renderTabContent(currentTab);

          initCurrentTabEvents();

        });

      });

    initCurrentTabEvents();
  }

      return;
    }

    if (data.job_id) {
      rerenderPanel();
      await pollJob(data.job_id);
    }

  } catch (error) {
    console.error("FETCH ERROR:", error);

    analysisError = error.message;
    analysisLoading = false;

    rerenderPanel();
  }
}

function getTimelineData() {
  if (!analysisData?.timeline || !analysisData?.clusters) {
    return [];
  }

  const clusterMap = {};

  analysisData.clusters.forEach((cluster) => {
    clusterMap[cluster.id] = cluster;
  });

  return analysisData.timeline.map((slot, index) => {
    const clusters = slot.clusters || {};

    const entries = Object.entries(clusters);

    return {
      label: slot.label,
      x: index * 128,

      values: entries.map(([clusterId, percent], idx) => {
        const cluster = clusterMap[clusterId];

        const colors = [
          "#4ade80",
          "#f87171",
          "#818cf8",
          "#a78bfa",
          "#94a3b8",
        ];

        return {
          id: clusterId,
          label: cluster?.label || clusterId,
          percent,
          color: colors[idx % colors.length],

          y: 260 - percent * 2,

          comments: cluster?.top_comments || [],
        };
      }),
    };
  });
}

//상태 폴링(2초마다)
async function pollJob(jobId) {
  console.log("폴링 시작:", jobId);

  const interval = setInterval(async () => {
    try {
      const response = await fetch(`${API_BASE}/status/${jobId}`);

      const data = await response.json();

      console.log("status polling:", data);

      if (data.status === "done") {
        clearInterval(interval);

        console.log("분석 완료");

        console.log("최종 result:", data.result);
        console.log("clusters:", data.result?.clusters);

        analysisData = data.result;

        await loadClusterVideos();

        analysisLoading = false;

        const panel = document.querySelector(".yt-comment-analysis-panel");

        if (panel) {

          panel.innerHTML = renderPanel();

          panel.querySelector(".analysis-close")
            ?.addEventListener("click", () => {
              closePanel();
              isPanelOpen = false;
            });

          panel.querySelectorAll(".analysis-tabs button")
            .forEach((button) => {

              button.addEventListener("click", () => {

                currentTab = button.dataset.tab;

                panel.querySelectorAll(".analysis-tabs button")
                  .forEach((tabButton) => {

                    tabButton.classList.toggle(
                      "active",
                      tabButton.dataset.tab === currentTab
                    );

                  });

                panel.querySelector(".analysis-body").innerHTML =
                  renderTabContent(currentTab);

                initCurrentTabEvents();

              });

            });

          initCurrentTabEvents();
        }
      }

      if (data.status === "failed") {
        clearInterval(interval);

        console.log("분석 실패");

        analysisError = "분석 실패";
        analysisLoading = false;

        rerenderPanel();
      }

    } catch (error) {
      clearInterval(interval);

      console.error("POLL ERROR:", error);

      analysisError = error.message;
      analysisLoading = false;

      rerenderPanel();
    }
  }, 2000);
}

//API 데이터를 그래프용 데이터로 환
function slicePath(cx, cy, r, startDeg, endDeg) {
  const p1 = polarToXY(cx, cy, r, startDeg);
  const p2 = polarToXY(cx, cy, r, endDeg);
  const large = endDeg - startDeg > 180 ? 1 : 0;

  return `M${cx},${cy} L${p1.x},${p1.y} A${r},${r} 0 ${large} 1 ${p2.x},${p2.y} Z`;
}

function renderTabContent(tab) {
  const renderers = {
    cluster: renderClusterTab,
    timeline: renderTimelineTab,
    videos: renderVideosTab,
    balance: renderBalanceTab,
  };

  return renderers[tab]?.() || "";
}

// 편향 여부 감지 함수 (분류안됨 제외, 50% 이상이면 편향)
function detectBias() {
  if (!analysisData?.clusters) return null;

  const BIAS_THRESHOLD = 50;

  const all = analysisData.clusters;

  if (!all.length) return null;

  const total = all.reduce((sum, c) => sum + c.percent, 0);
  if (total === 0) return null;

  const withRelative = all.map((c) => ({
    ...c,
    relativePct: Math.round((c.percent / total) * 100),
  }));

  const dominant = withRelative.reduce((a, b) =>
    a.relativePct > b.relativePct ? a : b
  );

  if (dominant.relativePct >= BIAS_THRESHOLD) {
    return {
      label: dominant.label,
      relativePct: dominant.relativePct,
      absolutePct: dominant.percent,
    };
  }

  return null;
}

function renderBiasBadge() {
  const bias = detectBias();

  if (!bias) {
    return `
      <div class="bias-badge balanced">
        <span class="bias-icon">✅</span>
        <div class="bias-text">
          <div class="bias-title">균형 잡힌 여론</div>
          <div class="bias-desc">특정 군집이 두드러지지 않습니다</div>
        </div>
      </div>
    `;
  }

  return `
    <div class="bias-badge biased">
      <span class="bias-icon">⚠️</span>
      <div class="bias-text">
        <div class="bias-title">여론 편향 감지됨</div>
        <div class="bias-desc">
          <strong style="color:#fbbf24;">${bias.label}</strong> 군집이
          분류된 댓글의 <strong style="color:#fbbf24;">${bias.relativePct}%</strong>를 차지합니다
        </div>
      </div>
    </div>
  `;
}

//원형 차트 생성
function renderClusterTab() {
  if (analysisLoading) {
    return `
      <div class="analysis-box">
        <h3>댓글 분석 중...</h3>
        <p style="color:#aaa;">AI가 댓글을 군집화하고 있습니다.</p>
      </div>
    `;
  }

  if (analysisError) {
    return `
      <div class="analysis-box">
        <h3>오류</h3>
        <p style="color:#f87171;">${analysisError}</p>
      </div>
    `;
  }

  if (!analysisData) {
    return `
      <div class="analysis-box">
        <h3>분석 데이터 없음</h3>
      </div>
    `;
  }

  const slices = createSlices();

  return `
    <div class="cluster-tab-wrap">
      <div class="cluster-main-row">
        <div class="cluster-pie-wrap">
          <svg class="cluster-pie-svg" id="cluster-svg" viewBox="0 0 320 320" width="320" height="320">
            <circle cx="${PIE.cx}" cy="${PIE.cy}" r="${PIE.r + 5}" fill="none" stroke="#2e2e2e" stroke-width="1"/>

            ${renderPieSlices(slices)}

            ${renderPieLabels(slices)}

            <circle cx="${PIE.cx}" cy="${PIE.cy}" r="54" fill="#1b1b1b"/>

            <text
              x="${PIE.cx}"
              y="${PIE.cy - 9}"
              text-anchor="middle"
              fill="#f1f1f1"
              font-size="20"
              font-weight="900"
            >
              ${analysisData.total_comments.toLocaleString()}
            </text>

            <text
              x="${PIE.cx}"
              y="${PIE.cy + 13}"
              text-anchor="middle"
              fill="#9aa3b5"
              font-size="11"
            >
              총 댓글
            </text>
          </svg>

          ${renderClusterTooltips(slices)}
        </div>

        <div class="cluster-side-panel">
          ${renderBiasBadge()}
        </div>
      </div>

      <div class="cluster-legend">
        ${renderClusterLegend(slices)}
      </div>
    </div>
  `;
}

function renderPieSlices(slices) {
  return slices.map((slice, index) => `
    <path
      class="pie-slice"
      data-index="${index}"
      d="${slicePath(PIE.cx, PIE.cy, PIE.r, slice.start, slice.end)}"
      fill="${slice.color}"
      fill-opacity="0.50"
      stroke="#1b1b1b"
      stroke-width="2.5"
    />
  `).join("");
}

function renderPieLabels(slices) {
  return slices.map((slice) => `
    <text
      x="${slice.lp.x}"
      y="${slice.lp.y}"
      text-anchor="middle"
      dominant-baseline="middle"
      fill="${slice.color}"
      font-size="13"
      font-weight="700"
      font-family="Roboto,Arial,sans-serif"
      pointer-events="none"
    >${slice.percent}%</text>
  `).join("");
}

function renderClusterTooltips(slices) {
  return slices.map((slice, index) => `
    <div
      class="cluster-tooltip"
      id="ctip-${index}"
      style="border-color:${slice.colorBorder};background:${slice.colorBg};"
    >
      <div class="cluster-tooltip-label" style="color:${slice.color};">
        <span class="cluster-tooltip-label-dot" style="background:${slice.color};"></span>
        ${slice.label}
      </div>
      <div class="cluster-tooltip-pct">${slice.percent}%</div>
      <div class="cluster-tooltip-count">${slice.count}</div>
      <div class="cluster-tooltip-tags">
        ${slice.tags.map((tag) => `<span class="cluster-tooltip-tag">${tag}</span>`).join("")}
      </div>
      <div class="cluster-tooltip-comment">💬 ${slice.topComment}</div>
    </div>
  `).join("");
}

function renderClusterLegend(slices) {
  return slices.map((slice) => `
    <div class="cluster-legend-item">
      <span class="cluster-legend-dot" style="background:${slice.color};"></span>
      <span>${slice.label}</span>
      <span class="cluster-legend-pct" style="color:${slice.color};">${slice.percent}%</span>
    </div>
  `).join("");
}

function initPieEvents() {
  const svg = document.getElementById("cluster-svg");
  if (!svg) return;

  svg.querySelectorAll(".pie-slice").forEach((slice) => {
    const index = Number(slice.dataset.index);
    const tooltip = document.getElementById(`ctip-${index}`);
    if (!tooltip) return;

    slice.addEventListener("mouseenter", (event) => {
      slice.setAttribute("fill-opacity", "0.85");
      slice.style.transform = "scale(1.05)";
      tooltip.style.display = "block";
      moveTooltip(event, tooltip);
    });

    slice.addEventListener("mousemove", (event) => {
      moveTooltip(event, tooltip);
    });

    slice.addEventListener("mouseleave", () => {
      slice.setAttribute("fill-opacity", "0.50");
      slice.style.transform = "scale(1)";
      tooltip.style.display = "none";
    });
  });
}

function moveTooltip(event, tooltip) {
  const parentRect = tooltip.parentElement.getBoundingClientRect();

  let x = event.clientX - parentRect.left + 18;
  let y = event.clientY - parentRect.top + 18;

  const tooltipWidth = tooltip.offsetWidth || 240;
  const tooltipHeight = tooltip.offsetHeight || 180;

  if (x + tooltipWidth > parentRect.width) {
    x = event.clientX - parentRect.left - tooltipWidth - 12;
  }

  if (y + tooltipHeight > parentRect.height) {
    y = event.clientY - parentRect.top - tooltipHeight - 12;
  }

  tooltip.style.left = `${x}px`;
  tooltip.style.top = `${y}px`;
}

function rerenderPanel() {
  const body = document.querySelector(".analysis-body");

  if (!body) return;

  body.innerHTML = renderTabContent(currentTab);

  initCurrentTabEvents();
}

function renderTimelineTab() {
  const timelineData = getTimelineData();

  if (!timelineData.length) {
    return `
      <div class="analysis-box">
        <h3>시간대 데이터 없음</h3>
      </div>
    `;
  }

  return `
    <div class="analysis-box full">
      <h3>시간대별 여론 변화</h3>

      <div class="line-chart" id="timeline-chart">

        <!-- ✅ 범례를 위로 이동 -->
        <div class="chart-legend top">
          ${analysisData.clusters.map((cluster, index) => {
            const colors = [
              "#4ade80",
              "#f87171",
              "#818cf8",
              "#a78bfa",
              "#94a3b8",
            ];

            return `
              <div class="chart-legend-item">
                <span
                  class="chart-legend-dot"
                  style="background:${colors[index % colors.length]}"
                ></span>

                <span>${cluster.label}</span>
              </div>
            `;
          }).join("")}
        </div>

        <div class="chart-grid"></div>

        <svg viewBox="0 0 900 330" preserveAspectRatio="none">
          ${renderTimelineLines(timelineData)}
        </svg>

        <div class="x-labels">
          ${timelineData.map((slot, index) => `
            <button
              class="timeline-slot-label ${selectedTimeSlot === index ? "active" : ""}"
              data-slot="${index}"
            >
              ${slot.label}
            </button>
          `).join("")}
        </div>

        <div id="timeline-tooltip" class="timeline-tooltip"></div>
      </div>
    </div>

    <div id="timeline-comments" class="timeline-comments-section"></div>
  `;
}

function renderTimelineLines(timelineData) {
  const clusterIds = new Set();

  timelineData.forEach((slot) => {
    slot.values.forEach((v) => {
      clusterIds.add(v.id);
    });
  });

  return [...clusterIds].map((clusterId) => {
    const points = timelineData.map((slot) => {
      const found = slot.values.find((v) => v.id === clusterId);

      if (!found) return null;

      return `${slot.x},${found.y}`;
    }).filter(Boolean).join(" ");

    const color =
      timelineData
        .flatMap((s) => s.values)
        .find((v) => v.id === clusterId)?.color || "#999";

    return `
      <polyline
        points="${points}"
        fill="none"
        stroke="${color}"
        stroke-width="4"
        stroke-linecap="round"
        stroke-linejoin="round"
        opacity="0.9"
      />
    `;
  }).join("");
}


function initTimelineEvents() {
  const chart = document.getElementById("timeline-chart");

  if (!chart) return;

  chart.querySelectorAll(".timeline-slot-label").forEach((label) => {

    const slotIndex = Number(label.dataset.slot);

    label.addEventListener("click", () => {

      selectedTimeSlot = slotIndex;

      chart.querySelectorAll(".timeline-slot-label").forEach((el) => {
        el.classList.remove("active");
      });

      label.classList.add("active");

      showTimelineComments(slotIndex);
    });
  });

  // 처음 진입 시 첫 시간대 자동 선택
  if (selectedTimeSlot === null) {
    selectedTimeSlot = 0;

    const first = chart.querySelector(`.timeline-slot-label[data-slot="0"]`);

    if (first) {
      first.classList.add("active");
    }

    showTimelineComments(0);
  }
}


function showTimelineComments(slotIndex) {
  const timelineData = getTimelineData();

  const slot = timelineData[slotIndex];

  const container = document.getElementById("timeline-comments");

  if (!slot || !container) return;

  container.innerHTML = `
    <div class="timeline-comments-header">
      <h3>${slot.label} 대표 댓글</h3>
    </div>

    <div class="timeline-comments-grid">
      ${slot.values.map((value) => `
        <div class="comment-group">

          <h4 style="color:${value.color}">
            ${value.label}
            (${value.percent}%)
          </h4>

          <div class="comment-list">
            ${(value.comments || []).map((comment) => `
              <div class="comment-item">
                ${comment}
              </div>
            `).join("")}
          </div>

        </div>
      `).join("")}
    </div>
  `;

  container.style.display = "block";
}

function renderCommentGroup(title, comments, color, colorClass) {
  return `
    <div class="comment-group ${colorClass}">
      <h4><span class="dot ${colorClass}"></span>${title}</h4>
      <div class="comment-list">
        ${comments.map((comment) => `
          <div class="comment-item">
            <div class="comment-avatar" style="background:${color}20;color:${color};">👤</div>
            <div class="comment-text">${comment}</div>
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

//군집별 유튜브 검색
async function fetchClusterVideos(cluster) {

  try {

    const query = `
      ${cluster.label}
      ${(cluster.tags || []).join(" ")}
    `.trim();

    const response = await fetch(
      `${API_BASE}/youtube/search?q=${encodeURIComponent(query)}`
    );

    const data = await response.json();

    return data.videos || [];

  } catch (error) {

    console.error("영상 추천 실패:", error);

    return [];
  }
}

async function loadClusterVideos() {

  if (!analysisData?.clusters) return;

  await Promise.all(

    analysisData.clusters.map(async (cluster) => {

      const videos = await fetchClusterVideos(cluster);

      cluster.videos = videos;

    })

  );

}

function renderVideosTab() {

  if (!analysisData?.clusters?.length) {

    return `
      <div class="analysis-box">
        <h3>추천 영상 없음</h3>
      </div>
    `;
  }

  const colors = [
    "green",
    "red",
    "blue",
    "purple",
    "gray",
  ];

  return analysisData.clusters.map((cluster, index) => {

    const color = colors[index % colors.length];

    return videoSection(
      color,
      cluster,
      cluster.videos || []
    );

  }).join("");
}

function renderBalanceTab() {

  if (!analysisData?.clusters?.length) {

    return `
      <div class="analysis-box">
        <h3>의견 데이터 없음</h3>
      </div>
    `;
  }

  const colors = [
    "green",
    "red",
    "blue",
    "purple",
    "gray",
  ];

  return `
    <div class="balance-grid">

      ${analysisData.clusters.map((cluster, index) => {

        const color = colors[index % colors.length];

        return balanceCard(
          color,
          cluster.label,
          cluster.summary || "요약 데이터 없음",
          `${cluster.percent}% · ${cluster.comment_count.toLocaleString()}개 댓글`
        );

      }).join("")}

    </div>
  `;
}

function videoSection(color, cluster, videos) {

  return `
    <div class="video-section ${color}">

      <div class="video-section-header">

        <div>

          <h3>${cluster.label}</h3>

          <div class="video-section-desc">
            ${cluster.summary || ""}
          </div>

        </div>

        <span>${videos.length}개 영상</span>

      </div>

      <div class="video-list">

        ${videos.length
          ? videos.map((video) => `

            <div class="video-card">

              <img
                class="video-thumb"
                src="${video.thumbnail}"
                alt="${video.title}"
              />

              <div class="video-info">

                <strong>${video.title}</strong>

                <p>${video.channel}</p>

                <div class="video-meta">
                  <span>조회수 ${video.views}</span>
                  <span>👍 ${video.likes}</span>
                </div>

              </div>

              <button
                class="video-open-btn"
                data-url="${video.url}"
              >
                ↗ 열기
              </button>

            </div>

          `).join("")

          : `
            <div class="video-empty">
              추천 영상이 없습니다.
            </div>
          `
        }

      </div>

    </div>
  `;
}

function initVideoEvents() {

  document.querySelectorAll(".video-open-btn")
    .forEach((button) => {

      button.addEventListener("click", () => {

        const url = button.dataset.url;

        if (url) {
          window.open(url, "_blank");
        }

      });

    });

}

function balanceCard(color, title, comment, reaction) {
  return `
    <div class="balance-card ${color}">

      <div class="balance-card-header">

      <h3>
        <span class="dot ${color}"></span>
        ${title}
      </h3>

      <div class="balance-percent ${color}">
        ${reaction}
      </div>

    </div>

      <p>${comment}</p>

    </div>
  `;
}

function findCommentsArea() {
  return document.querySelector("ytd-comments") || document.querySelector("#comments");
}

function findCommentsHeader() {
  return (
    document.querySelector("ytd-comments-header-renderer") ||
    document.querySelector("#comments #header") ||
    findCommentsArea()
  );
}

function injectButton() {
  if (document.querySelector(".yt-analysis-button")) return;

  const commentsArea = findCommentsArea();
  const header = findCommentsHeader();

  if (!commentsArea || !header) return;

  const button = document.createElement("button");
  button.className = "yt-analysis-button";
  button.innerHTML = `<span>✦</span><span>댓글 여론 분석</span>`;

  button.addEventListener("click", () => {
    if (isPanelOpen) {
      closePanel();
    } else {
      openPanel(commentsArea);
    }

    isPanelOpen = !isPanelOpen;
  });

  header.appendChild(button);
}

function openPanel(commentsArea) {
  if (document.querySelector(".yt-comment-analysis-panel")) return;

  const panel = document.createElement("div");
  panel.className = "yt-comment-analysis-panel";
  panel.innerHTML = renderPanel();

  panel.querySelector(".analysis-close").addEventListener("click", () => {
    closePanel();
    isPanelOpen = false;
  });

  panel.querySelectorAll(".analysis-tabs button").forEach((button) => {
    button.addEventListener("click", () => {
      currentTab = button.dataset.tab;

      panel.querySelectorAll(".analysis-tabs button").forEach((tabButton) => {
        tabButton.classList.toggle("active", tabButton.dataset.tab === currentTab);
      });

      panel.querySelector(".analysis-body").innerHTML = renderTabContent(currentTab);
      initCurrentTabEvents();
    });
  });

  commentsArea.prepend(panel);

  const videoId = getVideoId();

  if (videoId) {
    fetchAnalysis(videoId).then(() => {
      rerenderPanel();
    });
  }

  initCurrentTabEvents();

}

function renderPanel() {

  const totalComments =
    analysisData?.total_comments?.toLocaleString() || "-";

  const clusters = analysisData?.clusters || [];

  // 긍정 / 부정 / 중립 추출
  const positive =
    clusters.find((c) =>
      c.label.includes("긍정")
    )?.percent || 0;

  const negative =
    clusters.find((c) =>
      c.label.includes("부정")
    )?.percent || 0;

  const neutral =
    clusters.find((c) =>
      c.label.includes("중립")
    )?.percent || 0;

  return `
    <div class="analysis-header">
      <div class="analysis-title-wrap">
        <div class="analysis-logo">✦</div>

        <div>
          <div class="analysis-title">
            댓글 여론 분석
          </div>

          <div class="analysis-subtitle">
            ${totalComments}개 댓글을 벡터화하여 군집 분석 완료
          </div>
        </div>
      </div>

      <button class="analysis-close">×</button>
      </div>

      <div class="analysis-summary">

    <div class="summary-card total-card">
      <p>총 댓글</p>
      <strong>${totalComments}</strong>
    </div>

    ${(analysisData?.clusters || []).map((cluster, index) => {

      const colors = [
        "positive",
        "negative",
        "neutral",
        "purple",
        "gray",
      ];

      return `
        <div class="summary-card ${colors[index % colors.length]}">
          <p>${cluster.label}</p>
          <strong>${cluster.percent}%</strong>
        </div>
      `;

    }).join("")}

  </div>

    <div class="analysis-tabs">
      ${tabs.map((tab) => `
        <button
          class="${currentTab === tab.id ? "active" : ""}"
          data-tab="${tab.id}"
        >
          ${tab.label}
        </button>
      `).join("")}
    </div>

    <div class="analysis-body">
      ${renderTabContent(currentTab)}
    </div>
  `;
}

function initCurrentTabEvents() {
  if (currentTab === "cluster") {
    setTimeout(initPieEvents, 50);
  }

  if (currentTab === "timeline") {
    setTimeout(initTimelineEvents, 50);
  }

  if (currentTab === "videos") {
    setTimeout(initVideoEvents, 50);
  }


}

function closePanel() {
  document.querySelector(".yt-comment-analysis-panel")?.remove();
}

let lastUrl = location.href;

new MutationObserver(() => {
  if (location.href === lastUrl) return;

  lastUrl = location.href;
  isPanelOpen = false;

  closePanel();
  document.querySelector(".yt-analysis-button")?.remove();

  setTimeout(injectButton, 1500);
}).observe(document.body, {
  childList: true,
  subtree: true,
});

setInterval(injectButton, 1000);