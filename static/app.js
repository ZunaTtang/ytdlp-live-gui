// ytarchive GUI 프론트엔드
const $ = (id) => document.getElementById(id);

const STATUS_LABEL = {
  starting: "준비 중",
  waiting: "대기 중",
  recording: "녹화 중",
  muxing: "병합 중",
  finished: "완료",
  error: "오류",
  stopped: "정지됨",
};

let setupBusy = false;
let logTarget = null;

// ---------- API ----------
async function api(path, method = "GET", body) {
  const opt = { method, headers: {} };
  if (body) {
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  const r = await fetch(path, opt);
  return r.json();
}

// ---------- 설치 ----------
async function pollStatus() {
  let s;
  try {
    s = await api("/api/status");
  } catch (e) {
    return;
  }
  const setup = $("setup");
  if (s.ready) {
    setup.classList.add("hidden");
    $("start").disabled = false;
  } else {
    setup.classList.remove("hidden");
    $("start").disabled = true;
    const st = s.setup || {};
    $("setupBar").style.width = (st.percent || 0) + "%";
    if (st.running) {
      $("setupTitle").textContent = "필수 도구 설치 중...";
      $("setupBtn").disabled = true;
      $("setupMsg").textContent = st.message || "";
      setupBusy = true;
    } else if (st.error) {
      $("setupTitle").textContent = "설치 실패";
      $("setupBtn").disabled = false;
      $("setupBtn").textContent = "다시 시도";
      $("setupMsg").textContent = st.message;
      setupBusy = false;
    } else {
      $("setupTitle").textContent = "필수 도구 설치가 필요합니다";
      $("setupMsg").textContent =
        "ffmpeg" + (s.ffmpeg ? " ✓" : " ✗") +
        " · ytarchive" + (s.ytarchive ? " ✓" : " ✗");
    }
  }
}

// ---------- 작업 목록 ----------
function fmtAgo(ts) {
  if (!ts) return "";
  const sec = Math.floor(Date.now() / 1000 - ts);
  if (sec < 60) return sec + "초";
  if (sec < 3600) return Math.floor(sec / 60) + "분 " + (sec % 60) + "초";
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60);
  return h + "시간 " + m + "분";
}

function jobCard(j) {
  const card = document.createElement("div");
  card.className = "job-card " + j.status;

  const label = STATUS_LABEL[j.status] || j.status;
  const active = ["starting", "waiting", "recording", "muxing"].includes(j.status);

  let elapsed = "";
  if (j.status === "recording" && j.started_recording) {
    elapsed = "녹화 " + fmtAgo(j.started_recording);
  } else if (active) {
    elapsed = fmtAgo(j.created) + " 경과";
  }

  let actions = "";
  if (active) {
    actions += `<button class="ghost-btn small" data-act="stop" data-id="${j.id}">■ 정지·저장</button>`;
  } else {
    actions += `<button class="ghost-btn small" data-act="remove" data-id="${j.id}">목록에서 제거</button>`;
  }
  actions += `<button class="ghost-btn small" data-act="log" data-id="${j.id}">로그</button>`;

  const finalLine = j.final_file
    ? `<div class="job-progress" style="color:var(--green)">📦 ${esc(j.final_file)}</div>` : "";

  card.innerHTML = `
    <div class="job-top">
      <span class="badge ${j.status}">${label}</span>
      <span class="job-url"><a href="${esc(j.url)}" target="_blank">${esc(j.url)}</a></span>
      <span class="job-q">${esc(j.quality)}</span>
    </div>
    <div class="job-progress">${esc(j.progress || "출력 대기 중...")}</div>
    ${finalLine}
    <div class="job-actions">
      ${actions}
      <span class="job-meta">${elapsed}</span>
    </div>`;
  return card;
}

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function pollJobs() {
  let data;
  try {
    data = await api("/api/jobs");
  } catch (e) {
    return;
  }
  const list = $("jobList");
  const jobs = data.jobs || [];
  $("jobCount").textContent = jobs.length;
  $("empty").classList.toggle("hidden", jobs.length > 0);

  list.innerHTML = "";
  for (const j of jobs) list.appendChild(jobCard(j));

  // 로그 모달 열려있으면 갱신
  if (logTarget) refreshLog();
}

// ---------- 액션 ----------
async function startJob() {
  const url = $("url").value.trim();
  if (!url) { $("url").focus(); return; }
  const opts = {
    url,
    quality: $("quality").value,
    wait: $("optWait").checked,
    thumbnail: $("optThumb").checked,
    metadata: $("optMeta").checked,
    threads: parseInt($("optThreads").value, 10) || 4,
    retry: parseInt($("optRetry").value, 10) || 30,
    output: $("optOutput").value.trim(),
    browser: $("optBrowser").value,
    cookies: $("optCookies").value.trim(),
  };
  const r = await api("/api/jobs", "POST", opts);
  if (r.error) { alert("시작 실패: " + r.error); return; }
  $("url").value = "";
  pollJobs();
}

async function refreshLog() {
  if (!logTarget) return;
  const r = await api(`/api/jobs/${logTarget}/log`);
  if (r.log) {
    const body = $("logBody");
    const atBottom = body.scrollTop + body.clientHeight >= body.scrollHeight - 30;
    body.textContent = r.log.join("\n") + (r.progress ? "\n" + r.progress : "");
    if (atBottom) body.scrollTop = body.scrollHeight;
  }
}

// ---------- 이벤트 ----------
$("start").addEventListener("click", startJob);
$("url").addEventListener("keydown", (e) => { if (e.key === "Enter") startJob(); });

$("advToggle").addEventListener("click", () => {
  const a = $("adv");
  a.classList.toggle("hidden");
  $("advToggle").textContent = a.classList.contains("hidden") ? "고급 옵션 ▾" : "고급 옵션 ▴";
});

$("setupBtn").addEventListener("click", async () => {
  $("setupBtn").disabled = true;
  await api("/api/setup", "POST");
});

$("openFolder").addEventListener("click", () => api("/api/open-folder", "POST"));

$("jobList").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const id = btn.dataset.id, act = btn.dataset.act;
  if (act === "stop") {
    if (confirm("정지하시겠어요?\n지금까지 녹화된 분량은 파일로 저장됩니다.")) {
      await api(`/api/jobs/${id}/stop`, "POST");
      pollJobs();
    }
  } else if (act === "remove") {
    await api(`/api/jobs/${id}/remove`, "POST");
    pollJobs();
  } else if (act === "log") {
    logTarget = id;
    $("logTitle").textContent = "로그 · " + id;
    $("logModal").classList.remove("hidden");
    refreshLog();
  }
});

$("logClose").addEventListener("click", () => {
  $("logModal").classList.add("hidden");
  logTarget = null;
});
$("logModal").addEventListener("click", (e) => {
  if (e.target === $("logModal")) { $("logModal").classList.add("hidden"); logTarget = null; }
});

// ---------- 저장된 파일 / 분석용 압축 ----------
let compressTasks = {};

async function pollCompress() {
  try {
    const data = await api("/api/compress");
    compressTasks = {};
    for (const t of (data.tasks || [])) compressTasks[t.file] = t;
  } catch (e) { /* ignore */ }
}

async function pollRecordings() {
  let data;
  try { data = await api("/api/recordings"); } catch (e) { return; }
  const files = (data.files || []).filter(f => !f.is_analysis);
  const analysis = new Set((data.files || []).filter(f => f.is_analysis)
    .map(f => f.name));
  $("fileCount").textContent = files.length;
  $("fileEmpty").classList.toggle("hidden", files.length > 0);

  const list = $("fileList");
  list.innerHTML = "";
  for (const f of files) {
    const row = document.createElement("div");
    const task = compressTasks[f.name];
    const stem = f.name.replace(/\.[^.]+$/, "");
    const hasAnalysis = analysis.has(stem + "_분석용.mp4");
    row.className = "file-row";

    const sizeCls = f.size_mb < 500 ? "under" : "over";
    let right = "";
    if (task && (task.status === "compressing" || task.status === "starting")) {
      right = `<span class="cmp-prog">${task.percent || 0}% · ${esc(task.detail || "")}</span>
               <div class="cmp-bar"><div style="width:${task.percent || 0}%"></div></div>`;
    } else if (task && task.status === "done") {
      right = `<span class="cmp-prog" style="color:var(--green)">✓ ${esc(task.detail || "완료")}</span>`;
    } else if (task && task.status === "error") {
      right = `<span class="cmp-prog" style="color:var(--accent-2)">✕ ${esc(task.detail || "실패")}</span>
               <button class="ghost-btn small" data-cmp="${esc(f.name)}">다시</button>`;
    } else if (hasAnalysis) {
      right = `<span class="cmp-prog" style="color:var(--green)">✓ 분석용 있음</span>
               <button class="ghost-btn small" data-cmp="${esc(f.name)}">다시 압축</button>`;
    } else {
      right = `<button class="ghost-btn small" data-cmp="${esc(f.name)}">📉 분석용(≤500MB)</button>`;
    }

    row.innerHTML = `
      <span class="file-name">${esc(f.name)}</span>
      <span class="file-size ${sizeCls}">${f.size_mb} MB</span>
      ${right}`;
    list.appendChild(row);
  }
}

$("fileList").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-cmp]");
  if (!btn) return;
  const file = btn.dataset.cmp;
  btn.disabled = true;
  const r = await api("/api/compress", "POST", { file });
  if (r.error) { alert("압축 시작 실패: " + r.error); btn.disabled = false; return; }
  await pollCompress();
  pollRecordings();
});

// ---------- 루프 ----------
pollStatus();
pollJobs();
pollCompress().then(pollRecordings);
setInterval(pollStatus, 1500);
setInterval(pollJobs, 1500);
setInterval(async () => { await pollCompress(); pollRecordings(); }, 1500);
