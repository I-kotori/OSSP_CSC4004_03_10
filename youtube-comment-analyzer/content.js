let isPanelOpen = false;
let currentTab = "cluster";
let selectedTimeSlot = null;

const tabs = [
  { id: "cluster", label: "여론 군집" },
  { id: "timeline", label: "시간대별 분석" },
  { id: "videos", label: "여론별 영상" },
  { id: "balance", label: "의견 균형 보기" },
];

const CLUSTERS = [
  {
    id: "positive",
    label: "AI 발전 긍정적",
    percent: 38,
    color: "#4ade80",
    colorBg: "rgba(74,222,128,0.13)",
    colorBorder: "rgba(74,222,128,0.4)",
    dotClass: "green",
    tags: ["기술 혁신", "생산성 향상", "미래 낙관"],
    topComment: "AI가 반복 업무를 줄여 더 창의적인 일에 집중하게 해줄 것 같아요.",
    count: "3,204개 댓글",
  },
  {
    id: "danger",
    label: "AI 위험 우려",
    percent: 23,
    color: "#f87171",
    colorBg: "rgba(248,113,113,0.13)",
    colorBorder: "rgba(248,113,113,0.4)",
    dotClass: "red",
    tags: ["일자리 문제", "개인정보", "규제 필요"],
    topComment: "기술 발전은 좋지만 일자리·개인정보 문제는 함께 논의해야 합니다.",
    count: "1,939개 댓글",
  },
  {
    id: "realistic",
    label: "현실적 접근 필요",
    percent: 24,
    color: "#818cf8",
    colorBg: "rgba(129,140,248,0.13)",
    colorBorder: "rgba(129,140,248,0.4)",
    dotClass: "blue",
    tags: ["교육 준비", "제도 정비", "균형 시각"],
    topComment: "무조건 찬반보다 교육·제도 준비가 먼저라고 생각합니다.",
    count: "2,024개 댓글",
  },
  {
    id: "neutral",
    label: "중립적 / 기타",
    percent: 15,
    color: "#94a3b8",
    colorBg: "rgba(148,163,184,0.13)",
    colorBorder: "rgba(148,163,184,0.4)",
    dotClass: "gray",
    tags: ["관망", "판단 보류", "기타 의견"],
    topComment: "아직 판단하기 어렵지만 앞으로 어떤 변화가 올지 궁금합니다.",
    count: "1,265개 댓글",
  },
];

const TIMELINE_DATA = [
  {
    label: "0-2시간",
    x: 0,
    yPositive: 132,
    yDanger: 158,
    yRealistic: 198,
    yNeutral: 224,
    positive: 40,
    danger: 19,
    realistic: 24,
    neutral: 17,
    comments: {
      positive: ["초반 반응이 매우 긍정적이네요! AI 발전 기대됩니다.", "와 이 기술 정말 혁신적인데요?"],
      danger: ["하지만 개인정보 보호는 어떻게 되나요?", "일자리 문제가 걱정되긴 합니다."],
      realistic: ["기술은 좋지만 제도가 따라가야 할 것 같아요.", "교육 시스템 개편이 필요할 듯."],
      neutral: ["일단 지켜봐야 할 것 같습니다.", "아직 판단하기 이릅니다."],
    },
  },
  {
    label: "2-4시간",
    x: 128,
    yPositive: 113,
    yDanger: 173,
    yRealistic: 191,
    yNeutral: 238,
    positive: 42,
    danger: 18,
    realistic: 25,
    neutral: 15,
    comments: {
      positive: ["시간이 지날수록 더 긍정적으로 보이네요.", "생산성 향상 효과가 클 것 같습니다."],
      danger: ["그래도 규제는 필요하지 않을까요?", "부작용에 대한 대비가 필요합니다."],
      realistic: ["단계적 도입이 현실적일 것 같아요.", "교육과 병행되어야 합니다."],
      neutral: ["좀 더 시간을 두고 봐야겠어요.", "아직 결론 내리기 이릅니다."],
    },
  },
  {
    label: "4-8시간",
    x: 256,
    yPositive: 99,
    yDanger: 188,
    yRealistic: 188,
    yNeutral: 245,
    positive: 43,
    danger: 17,
    realistic: 25,
    neutral: 15,
    comments: {
      positive: ["이 정도면 정말 획기적인 변화입니다.", "미래가 기대되는 기술이네요."],
      danger: ["AI 윤리 문제도 고려해야 합니다.", "일자리 대체 문제는 심각합니다."],
      realistic: ["정부의 체계적인 준비가 필요해요.", "사회 전반의 합의가 우선입니다."],
      neutral: ["아직 확신이 서지 않습니다.", "더 많은 사례를 봐야 할 것 같아요."],
    },
  },
  {
    label: "8-12시간",
    x: 384,
    yPositive: 92,
    yDanger: 195,
    yRealistic: 188,
    yNeutral: 245,
    positive: 44,
    danger: 16,
    realistic: 25,
    neutral: 15,
    comments: {
      positive: ["계속 지켜봤는데 정말 긍정적입니다.", "이런 혁신은 환영합니다."],
      danger: ["하지만 리스크 관리가 중요합니다.", "개인정보 유출 가능성은?"],
      realistic: ["법적 제도가 먼저 갖춰져야 해요.", "점진적 접근이 필요합니다."],
      neutral: ["여전히 관망 중입니다.", "판단 보류."],
    },
  },
  {
    label: "12-24시간",
    x: 512,
    yPositive: 92,
    yDanger: 195,
    yRealistic: 188,
    yNeutral: 245,
    positive: 44,
    danger: 16,
    realistic: 25,
    neutral: 15,
    comments: {
      positive: ["하루가 지나도 긍정적인 의견이 우세하네요.", "정말 기대되는 기술입니다."],
      danger: ["장기적 영향은 아직 모릅니다.", "부작용 연구가 필요합니다."],
      realistic: ["교육 시스템 개편이 시급합니다.", "사회적 합의가 먼저입니다."],
      neutral: ["좀 더 지켜봐야 할 것 같아요.", "아직 모르겠습니다."],
    },
  },
  {
    label: "1-2일",
    x: 640,
    yPositive: 85,
    yDanger: 202,
    yRealistic: 188,
    yNeutral: 245,
    positive: 45,
    danger: 15,
    realistic: 25,
    neutral: 15,
    comments: {
      positive: ["시간이 지날수록 확신이 듭니다.", "AI 시대가 곧 올 것 같아요."],
      danger: ["하지만 규제는 꼭 필요합니다.", "윤리적 문제 해결이 우선입니다."],
      realistic: ["준비 과정이 중요합니다.", "단계별 로드맵이 필요해요."],
      neutral: ["여전히 판단 보류입니다.", "더 지켜봐야겠습니다."],
    },
  },
  {
    label: "2-3일",
    x: 768,
    yPositive: 92,
    yDanger: 195,
    yRealistic: 188,
    yNeutral: 245,
    positive: 44,
    danger: 16,
    realistic: 25,
    neutral: 15,
    comments: {
      positive: ["긍정적인 측면이 더 많아 보입니다.", "발전적인 방향입니다."],
      danger: ["여전히 우려스러운 부분이 있습니다.", "리스크가 완전히 해소되진 않았어요."],
      realistic: ["균형잡힌 접근이 필요합니다.", "제도 정비가 시급합니다."],
      neutral: ["아직도 확신이 없습니다.", "좀 더 관망하겠습니다."],
    },
  },
  {
    label: "3일+",
    x: 896,
    yPositive: 92,
    yDanger: 195,
    yRealistic: 188,
    yNeutral: 245,
    positive: 44,
    danger: 16,
    realistic: 25,
    neutral: 15,
    comments: {
      positive: ["장기적으로도 긍정적인 것 같습니다.", "미래 지향적인 기술이에요."],
      danger: ["하지만 경계를 늦춰선 안 됩니다.", "지속적인 모니터링이 필요합니다."],
      realistic: ["사회 전체의 준비가 필요해요.", "교육과 제도가 함께 가야 합니다."],
      neutral: ["여전히 판단 중입니다.", "결론 내리기 어렵습니다."],
    },
  },
];

const PIE = {
  cx: 160,
  cy: 160,
  r: 112,
};

const SLICES = createSlices();

function createSlices() {
  let currentDeg = 0;

  return CLUSTERS.map((cluster) => {
    const start = currentDeg;
    const sweep = (cluster.percent / 100) * 360;
    const end = start + sweep;

    currentDeg = end;

    return {
      ...cluster,
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

function renderClusterTab() {
  return `
    <div class="cluster-tab-wrap">
      <div class="cluster-pie-wrap">
        <svg class="cluster-pie-svg" id="cluster-svg" viewBox="0 0 320 320" width="320" height="320">
          <circle cx="${PIE.cx}" cy="${PIE.cy}" r="${PIE.r + 5}" fill="none" stroke="#2e2e2e" stroke-width="1"/>
          ${renderPieSlices()}
          ${renderPieLabels()}
          <circle cx="${PIE.cx}" cy="${PIE.cy}" r="54" fill="#1b1b1b"/>
          <text x="${PIE.cx}" y="${PIE.cy - 9}" text-anchor="middle" fill="#f1f1f1" font-size="20" font-weight="900" font-family="Roboto,Arial,sans-serif">8,432</text>
          <text x="${PIE.cx}" y="${PIE.cy + 13}" text-anchor="middle" fill="#9aa3b5" font-size="11" font-family="Roboto,Arial,sans-serif">총 댓글</text>
        </svg>
        ${renderClusterTooltips()}
      </div>
      <div class="cluster-legend">${renderClusterLegend()}</div>
    </div>
  `;
}

function renderPieSlices() {
  return SLICES.map((slice, index) => `
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

function renderPieLabels() {
  return SLICES.map((slice) => `
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

function renderClusterTooltips() {
  return SLICES.map((slice, index) => `
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

function renderClusterLegend() {
  return SLICES.map((slice) => `
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

function renderTimelineTab() {
  return `
    <div class="analysis-box full">
      <h3>시간대별 여론 변화</h3>
      <div class="line-chart" id="timeline-chart">
        <div class="chart-grid"></div>
        <svg viewBox="0 0 900 330" preserveAspectRatio="none">
          <polyline points="0,132 128,113 256,99 384,92 512,92 640,85 768,92 896,92" class="line green-line"/>
          <polyline points="0,158 128,173 256,188 384,195 512,195 640,202 768,195 896,195" class="line red-line"/>
          <polyline points="0,198 128,191 256,188 384,188 512,188 640,188 768,188 896,188" class="line blue-line"/>
          <polyline points="0,224 128,238 256,245 384,245 512,245 640,245 768,245 896,245" class="line purple-line"/>
        </svg>
        ${renderTimelinePoints()}
        <div class="x-labels">
          ${TIMELINE_DATA.map((data, index) => `<span data-slot="${index}">${data.label}</span>`).join("")}
        </div>
        <div class="chart-legend">
          <span class="green-text">AI 발전 긍정적</span>
          <span class="red-text">AI 위험 우려</span>
          <span class="blue-text">현실적 접근 필요</span>
          <span class="purple-text">중립적/기타</span>
        </div>
        <div id="timeline-tooltip" class="timeline-tooltip"></div>
      </div>
    </div>
    <div id="timeline-comments" class="timeline-comments-section"></div>
  `;
}

function renderTimelinePoints() {
  const points = TIMELINE_DATA.map((data, index) => `
    <circle class="timeline-point" data-slot="${index}" cx="${data.x}" cy="${data.yPositive}" r="6" fill="#4ade80"/>
    <circle class="timeline-point" data-slot="${index}" cx="${data.x}" cy="${data.yDanger}" r="6" fill="#f87171"/>
    <circle class="timeline-point" data-slot="${index}" cx="${data.x}" cy="${data.yRealistic}" r="6" fill="#818cf8"/>
    <circle class="timeline-point" data-slot="${index}" cx="${data.x}" cy="${data.yNeutral}" r="6" fill="#a267f5"/>
  `).join("");

  return `<svg class="timeline-points" viewBox="0 0 900 330" preserveAspectRatio="none">${points}</svg>`;
}

function initTimelineEvents() {
  const chart = document.getElementById("timeline-chart");
  if (!chart) return;

  const points = chart.querySelectorAll(".timeline-point");
  const labels = chart.querySelectorAll(".x-labels span");
  const tooltip = document.getElementById("timeline-tooltip");

  points.forEach((point) => {
    const slotIndex = Number(point.dataset.slot);
    const data = TIMELINE_DATA[slotIndex];

    point.addEventListener("mouseenter", (event) => {
      point.setAttribute("r", "8");
      showTimelineTooltip(event, data, tooltip, chart);
    });

    point.addEventListener("mousemove", (event) => {
      showTimelineTooltip(event, data, tooltip, chart);
    });

    point.addEventListener("mouseleave", () => {
      point.setAttribute("r", "6");
      tooltip.style.display = "none";
    });

    point.addEventListener("click", (event) => {
      event.stopPropagation();
      selectTimelineSlot(slotIndex, chart);
    });
  });

  labels.forEach((label) => {
    const slotIndex = Number(label.dataset.slot);

    label.style.cursor = "pointer";

    label.addEventListener("click", () => {
      selectTimelineSlot(slotIndex, chart);
    });
  });
}

function selectTimelineSlot(slotIndex, chart) {
  selectedTimeSlot = slotIndex;
  showTimelineComments(slotIndex);

  chart.querySelectorAll(".timeline-point").forEach((point) => {
    point.classList.remove("selected");
  });

  chart.querySelectorAll(`[data-slot="${slotIndex}"]`).forEach((element) => {
    if (element.classList.contains("timeline-point")) {
      element.classList.add("selected");
    }
  });
}

function showTimelineTooltip(event, data, tooltip, chart) {
  tooltip.innerHTML = `
    <div class="timeline-tooltip-label">${data.label}</div>
    ${renderTimelineTooltipRow("green", "AI 발전 긍정적", data.positive)}
    ${renderTimelineTooltipRow("red", "AI 위험 우려", data.danger)}
    ${renderTimelineTooltipRow("blue", "현실적 접근 필요", data.realistic)}
    ${renderTimelineTooltipRow("purple", "중립적/기타", data.neutral)}
    <div class="timeline-tooltip-hint">클릭하면 댓글을 볼 수 있습니다</div>
  `;

  const rect = chart.getBoundingClientRect();

  let x = event.clientX - rect.left + 15;
  let y = event.clientY - rect.top + 15;

  const tooltipWidth = 220;
  const tooltipHeight = 180;

  if (x + tooltipWidth > rect.width) {
    x = event.clientX - rect.left - tooltipWidth - 15;
  }

  if (y + tooltipHeight > rect.height) {
    y = event.clientY - rect.top - tooltipHeight - 15;
  }

  tooltip.style.left = `${x}px`;
  tooltip.style.top = `${y}px`;
  tooltip.style.display = "block";
}

function renderTimelineTooltipRow(colorClass, label, percent) {
  return `
    <div class="timeline-tooltip-row">
      <span class="dot ${colorClass}"></span>
      <span>${label}</span>
      <strong>${percent}%</strong>
    </div>
  `;
}

function showTimelineComments(slotIndex) {
  const data = TIMELINE_DATA[slotIndex];
  const container = document.getElementById("timeline-comments");
  if (!container) return;

  container.innerHTML = `
    <div class="timeline-comments-header">
      <h3>📊 ${data.label} 여론별 댓글</h3>
      <button class="timeline-comments-close">×</button>
    </div>
    <div class="timeline-comments-grid">
      ${renderCommentGroup("AI 발전 긍정적", data.comments.positive, "#4ade80", "green")}
      ${renderCommentGroup("AI 위험 우려", data.comments.danger, "#f87171", "red")}
      ${renderCommentGroup("현실적 접근 필요", data.comments.realistic, "#818cf8", "blue")}
      ${renderCommentGroup("중립적/기타", data.comments.neutral, "#a267f5", "purple")}
    </div>
  `;

  container.style.display = "block";

  container.querySelector(".timeline-comments-close").addEventListener("click", () => {
    container.style.display = "none";
    selectedTimeSlot = null;

    document.querySelectorAll(".timeline-point").forEach((point) => {
      point.classList.remove("selected");
    });
  });
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

function renderVideosTab() {
  return `
    ${videoSection("green", "AI 발전 긍정적", "2개 영상", [
      ["AI가 만드는 미래 사회 - 인류의 새로운 도약", "미래기술연구소", "1.2M", "52K"],
      ["생산성 혁명: AI로 달라지는 업무 환경", "비즈니스 인사이트", "856K", "38K"],
    ])}
    ${videoSection("red", "AI 위험 우려", "2개 영상", [
      ["AI 기술의 어두운 면 - 우리가 간과한 위험들", "테크 비평", "890K", "32K"],
      ["AI 발전이 가져올 일자리 문제의 진실", "경제 전문가TV", "423K", "19K"],
    ])}
    ${videoSection("blue", "현실적 접근 필요", "2개 영상", [
      ["AI 시대를 준비하는 현실적인 방법", "에듀 테크", "642K", "27K"],
      ["정책과 교육이 함께 가야 하는 이유", "시사 연구소", "318K", "12K"],
    ])}
  `;
}

function renderBalanceTab() {
  return `
    <div class="balance-grid">
      ${balanceCard("green", "AI 발전 긍정적", "AI가 반복 업무를 줄이고 더 창의적인 일에 집중하게 해줄 것 같아요.", "공감 1.8K")}
      ${balanceCard("red", "AI 위험 우려", "기술 발전은 좋지만 개인정보와 일자리 문제는 반드시 같이 논의해야 합니다.", "공감 1.2K")}
      ${balanceCard("blue", "현실적 접근 필요", "무조건 찬반으로 나누기보다 교육과 제도 준비가 먼저라고 생각합니다.", "공감 980")}
      ${balanceCard("gray", "중립적/기타", "아직 판단하기 어렵지만 앞으로 어떤 변화가 생길지 궁금합니다.", "공감 640")}
    </div>
  `;
}

function videoSection(color, title, count, videos) {
  return `
    <div class="video-section ${color}">
      <div class="video-section-header">
        <h3>${title}</h3>
        <span>${count}</span>
      </div>
      <div class="video-list">
        ${videos.map((video) => `
          <div class="video-card">
            <div class="video-thumb"></div>
            <div class="video-info">
              <strong>${video[0]}</strong>
              <p>${video[1]}</p>
              <div>
                <span>◉ ${video[2]}</span>
                <span>♡ ${video[3]}</span>
              </div>
            </div>
            <button>↗ 열기</button>
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

function balanceCard(color, title, comment, reaction) {
  return `
    <div class="balance-card ${color}">
      <h3><span class="dot ${color}"></span>${title}</h3>
      <p>${comment}</p>
      <span>${reaction}</span>
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
  initCurrentTabEvents();
}

function renderPanel() {
  return `
    <div class="analysis-header">
      <div class="analysis-title-wrap">
        <div class="analysis-logo">✦</div>
        <div>
          <div class="analysis-title">댓글 여론 분석</div>
          <div class="analysis-subtitle">8,432개 댓글을 벡터화하여 군집 분석 완료</div>
        </div>
      </div>
      <button class="analysis-close">×</button>
    </div>

    <div class="analysis-summary">
      <div class="summary-card"><p>총 댓글</p><strong>8,432</strong></div>
      <div class="summary-card positive"><p>긍정적</p><strong>62%</strong></div>
      <div class="summary-card negative"><p>부정적</p><strong>23%</strong></div>
      <div class="summary-card neutral"><p>중립적</p><strong>15%</strong></div>
    </div>

    <div class="analysis-tabs">
      ${tabs.map((tab) => `
        <button class="${currentTab === tab.id ? "active" : ""}" data-tab="${tab.id}">
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