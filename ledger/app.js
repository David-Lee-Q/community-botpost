
const PAGE_SIZE = 10;
const ICON_EYE = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
const ICON_PEN = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg>';
const ICON_CLOCK = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>';
const ICON_SEND = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>';
let ARTICLES = [];
let REVIEWS = {};
let SCORE = null;
let HISTCOUNTS = {};
let POSTS = {};
let DATA_UPDATED = '';
let filtered = [];
let selectedIds = new Set();
let page = 1;
let charts = {};
let OPT_AID = null;
let selectedCates = [];
let DIST_TASKS = {};      // article_id -> 最近一次分发任务
const LOCAL_BRIDGE = 'http://127.0.0.1:8390';  // 本机桥直连（台账页与桥同机时可用）
let AUTH_MODE = 'ledger'; // 'local'=本机桥直连 | 'ledger'=经台账中转

function nowLocal() {
  const d = new Date(), p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
let PLATFORM_META = [];   // 17 平台元数据（名称/登录 URL）
const DIST_POLLERS = {};  // task_id -> 轮询定时器

function fmtNum(n) { return n == null ? 0 : Number(n).toLocaleString(); }

function dateStr(iso) {
  return iso ? iso.slice(0, 10) : '';
}
function weekOf(iso) {
  const d = new Date(iso.slice(0, 10) + 'T00:00:00');
  const day = (d.getDay() + 6) % 7;
  const monday = new Date(d); monday.setDate(d.getDate() - day);
  return monday;
}
function fillBuckets(buckets, from, to, stepFn) {
  const out = [];
  for (let cur = new Date(from); cur <= to; ) {
    const key = stepFn(cur);
    out.push({ key, count: buckets.get(key) || 0 });
    if (stepFn.name.includes('Week')) cur.setDate(cur.getDate() + 7);
    else if (stepFn.name.includes('Month')) cur.setMonth(cur.getMonth() + 1);
    else cur.setDate(cur.getDate() + 1);
  }
  return out;
}

function parseData(json) {
  ARTICLES = json.articles || [];
  const cats = json.categories || {};
  // 分类多选列表
  const cateList = document.getElementById('cateList');
  const used = [...new Set(ARTICLES.map(a => a.cateName || '未分类'))].sort();
  used.forEach(c => {
    const lab = document.createElement('label');
    const cb = document.createElement('input');
    cb.type = 'checkbox'; cb.className = 'cate-cb';
    cb.addEventListener('change', onCateChange);
    lab.appendChild(cb);
    lab.appendChild(document.createTextNode(' ' + c));
    cateList.appendChild(lab);
  });
  restoreState();
  renderAll();
}

function calcStats() {
  const now = new Date();
  const today = now.toISOString().slice(0, 10);
  const wd = (now.getDay() + 6) % 7;
  const weekStart = new Date(now); weekStart.setDate(now.getDate() - wd);
  const ws = weekStart.toISOString().slice(0, 10);
  const ms = now.toISOString().slice(0, 7);

  let m = 0, w = 0, t = 0;
  ARTICLES.forEach(a => {
    const d = dateStr(a.createTime);
    if (!d) return;
    if (d === today) t++;
    if (d >= ws && d <= today) w++;
    if (d.slice(0, 7) === ms) m++;
  });
  document.getElementById('statTotal').textContent = fmtNum(ARTICLES.length);
  document.getElementById('statMonth').textContent = fmtNum(m);
  document.getElementById('statWeek').textContent = fmtNum(w);
  document.getElementById('statToday').textContent = fmtNum(t);
  const months = [...new Set(ARTICLES.map(a => dateStr(a.createTime).slice(0, 7)).filter(Boolean))].sort();
  const latest = months[months.length - 1] || '-';
  document.getElementById('statMonthExtra').textContent = latest + ' 当月';
  document.getElementById('statTodayExtra').textContent = today;
}

function makeChart(id, cfg) {
  if (charts[id]) charts[id].destroy();
  charts[id] = new Chart(document.getElementById(id), cfg);
}

function fmtLocal(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}
function addDays(d, n) { const r = new Date(d); r.setDate(r.getDate() + n); return r; }
function addWeeks(d, n) { const r = new Date(d); r.setDate(r.getDate() + n * 7); return r; }
function addMonths(d, n) { const r = new Date(d.getFullYear(), d.getMonth() + n, 1); return r; }
function mondayOf(d) {
  const r = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  r.setDate(r.getDate() - ((r.getDay() + 6) % 7));
  return r;
}
function monthKey(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`; }

function renderCharts() {
  const now = new Date();
  const cateCount = new Map();
  const catTrend = new Map();
  const byDay = new Map();
  const byWeek = new Map();
  const byMonth = new Map();

  ARTICLES.forEach(a => {
    const iso = a.createTime;
    if (!iso) return;
    const d = dateStr(iso);
    if (!d) return;
    const dt = new Date(d + 'T00:00:00');
    byDay.set(d, (byDay.get(d) || 0) + 1);
    const mk = fmtLocal(mondayOf(dt));
    byWeek.set(mk, (byWeek.get(mk) || 0) + 1);
    const ym = d.slice(0, 7);
    byMonth.set(ym, (byMonth.get(ym) || 0) + 1);
    const cn = a.cateName || '未分类';
    cateCount.set(cn, (cateCount.get(cn) || 0) + 1);
    if (!catTrend.has(cn)) catTrend.set(cn, {});
    catTrend.get(cn)[ym] = (catTrend.get(cn)[ym] || 0) + 1;
  });

  // 近30天（自然日）
  const dayBuckets = [];
  for (let i = 29; i >= 0; i--) {
    const dt = addDays(now, -i);
    const key = fmtLocal(dt);
    dayBuckets.push({ key, label: key.slice(5), v: byDay.get(key) || 0 });
  }
  makeChart('chartDaily', {
    type: 'bar',
    data: { labels: dayBuckets.map(b => b.label), datasets: [{ label: '发布量', data: dayBuckets.map(b => b.v), backgroundColor: '#2f6fed', borderRadius: 3 }] },
    options: chartOpts('文章数')
  });

  // 近12周（自然周：周一~周日，横坐标标记自然周最后一天=周日）
  const thisMonday = mondayOf(now);
  const weekBuckets = [];
  for (let i = 11; i >= 0; i--) {
    const m = addWeeks(thisMonday, -i);
    const sunday = addDays(m, 6);
    weekBuckets.push({ key: fmtLocal(m), label: fmtLocal(sunday).slice(5), v: byWeek.get(fmtLocal(m)) || 0 });
  }
  makeChart('chartWeekly', {
    type: 'bar',
    data: { labels: weekBuckets.map(b => b.label), datasets: [{ label: '发布量', data: weekBuckets.map(b => b.v), backgroundColor: '#19be6b', borderRadius: 3 }] },
    options: chartOpts('文章数')
  });

  // 近12个月（自然月）
  const monthBuckets = [];
  for (let i = 11; i >= 0; i--) {
    const m = addMonths(now, -i);
    const key = monthKey(m);
    monthBuckets.push({ key, label: key, v: byMonth.get(key) || 0 });
  }
  makeChart('chartMonthly', {
    type: 'line',
    data: { labels: monthBuckets.map(b => b.label), datasets: [{ label: '发布量', data: monthBuckets.map(b => b.v), borderColor: '#f59e0b', backgroundColor: 'rgba(245,158,11,.15)', fill: true, tension: .35 }] },
    options: chartOpts('文章数')
  });

  // 分类分布（取前12大 + 其他）
  const sorted = [...cateCount.entries()].sort((a, b) => b[1] - a[1]);
  const top = sorted.slice(0, 12);
  const rest = sorted.slice(12).reduce((s, x) => s + x[1], 0);
  if (rest > 0) top.push(['其他', rest]);
  const palette = ['#2f6fed','#19be6b','#f59e0b','#f56c6c','#9b59b6','#00b5ad','#e67e22','#34495e','#1abc9c','#e84393','#16a085','#d35400','#7f8c8d'];
  makeChart('chartCate', {
    type: 'doughnut',
    data: { labels: top.map(d => d[0]), datasets: [{ data: top.map(d => d[1]), backgroundColor: palette.slice(0, top.length) }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'right' } } }
  });

  // 分类趋势（近6个月，top8）
  const mOrder6 = [];
  for (let i = 5; i >= 0; i--) mOrder6.push(monthKey(addMonths(now, -i)));
  const sumArr = obj => Object.values(obj).reduce((s, x) => s + x, 0);
  const trendSorted = [...catTrend.entries()].sort((a, b) => sumArr(b[1]) - sumArr(a[1])).slice(0, 8);
  makeChart('chartTrend', {
    type: 'line',
    data: {
      labels: mOrder6,
      datasets: trendSorted.map((c, i) => ({
        label: c[0], data: mOrder6.map(m => c[1][m] || 0),
        borderColor: palette[i % palette.length], tension: .35, pointRadius: 2,
      }))
    },
    options: chartOpts('文章数')
  });

  renderTrendChart('score');
  renderTrendChart('views');
  renderTrendChart('engage');
}

const PERIOD_COUNT = { day: 30, week: 12, month: 12 };
const PERIOD_OFFSET = { day: 365, week: 52, month: 12 };
const currentPeriod = { score: 'day', views: 'day', engage: 'day' };

function buildPeriodBuckets(period) {
  const n = PERIOD_COUNT[period];
  const off = PERIOD_OFFSET[period];
  const now = new Date();
  let anchor;
  if (period === 'month') anchor = new Date(now.getFullYear(), now.getMonth(), 1);
  else if (period === 'week') anchor = mondayOf(now);
  else anchor = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const total = n + off;
  const keys = [], labels = [], bIdx = {};
  for (let i = total - 1; i >= 0; i--) {
    let d = period === 'day' ? addDays(anchor, -i)
          : period === 'week' ? addWeeks(anchor, -i)
          : addMonths(anchor, -i);
    const wk = period === 'week' ? mondayOf(d) : d;
    const key = period === 'month' ? monthKey(d) : fmtLocal(wk);
    const pos = keys.length;
    keys.push(key);
    labels.push(period === 'week' ? fmtLocal(addDays(wk, 6)).slice(5)
              : period === 'month' ? key : key.slice(5));
    bIdx[key] = pos;
  }
  return { n, off, keys, labels, bIdx };
}

function buildRates(vals, off) {
  const yoy = vals.map((v, i) => {
    if (v == null || i - off < 0) return null;
    const p = vals[i - off];
    if (p == null || p === 0) return null;
    return +((v - p) / p * 100).toFixed(1);
  });
  const mom = vals.map((v, i) => {
    if (v == null || i < 1) return null;
    const p = vals[i - 1];
    if (p == null || p === 0) return null;
    return +((v - p) / p * 100).toFixed(1);
  });
  return { yoy, mom };
}

function renderTrendChart(kind) {
  const period = currentPeriod[kind];
  const { n, off, labels, bIdx } = buildPeriodBuckets(period);
  const total = n + off;
  const scoreSum = new Array(total).fill(0);
  const scoreCnt = new Array(total).fill(0);
  const viewSum = new Array(total).fill(0);
  const engageSum = new Array(total).fill(0);
  ARTICLES.forEach(a => {
    const iso = a.createTime;
    if (!iso) return;
    const d = dateStr(iso);
    if (!d) return;
    const dt = new Date(d + 'T00:00:00');
    const key = period === 'month' ? d.slice(0, 7)
              : period === 'week' ? fmtLocal(mondayOf(dt)) : d;
    const i = bIdx[key];
    if (i == null) return;
    viewSum[i] += (a.viewCount || 0);
    engageSum[i] += (a.commentCount || 0) + (a.favor || 0) + (a.collect || 0);
    const rv = REVIEWS[a.id];
    if (rv && typeof rv.score === 'number') { scoreSum[i] += rv.score; scoreCnt[i]++; }
  });
  const scoreVals = scoreSum.map((s, i) => scoreCnt[i] ? +(s / scoreCnt[i]).toFixed(1) : null);
  const base = kind === 'score' ? scoreVals : kind === 'views' ? viewSum : engageSum;
  const { yoy, mom } = buildRates(base, off);
  const show = base.slice(off);
  const showYoy = yoy.slice(off);
  const showMom = mom.slice(off);

  const mainLabel = kind === 'score' ? '平均评价分' : kind === 'views' ? '浏览次数' : '互动次数';
  const datasets = [{
    label: mainLabel,
    data: show, borderColor: '#2f6fed', backgroundColor: 'rgba(47,111,237,.12)',
    fill: true, tension: .35, pointRadius: 2, yAxisID: 'y'
  }];
  if (showYoy.some(v => v != null)) {
    datasets.push({ label: '同比', data: showYoy, borderColor: '#e84393', tension: .3, pointRadius: 0, borderDash: [5, 3], yAxisID: 'y2' });
  }
  if (showMom.some(v => v != null)) {
    datasets.push({ label: '环比', data: showMom, borderColor: '#00b5ad', tension: .3, pointRadius: 0, borderDash: [2, 2], yAxisID: 'y2' });
  }
  const canvasId = kind === 'score' ? 'chartScore' : kind === 'views' ? 'chartViews' : 'chartEngage';
  makeChart(canvasId, {
    type: 'line',
    data: { labels: labels.slice(off), datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { font: { size: 11 } } } },
      scales: {
        y: { beginAtZero: false, title: { display: true, text: mainLabel } },
        y2: { position: 'right', title: { display: true, text: '同比/环比 %' }, grid: { drawOnChartArea: false } },
        x: { ticks: { maxTicksLimit: 15, font: { size: 10 } } }
      }
    }
  });
}

function chartOpts(yLabel) {
  return {
    responsive: true, maintainAspectRatio: false,
    plugins: { legend: { display: true, labels: { font: { size: 11 } } } },
    scales: {
      y: { beginAtZero: true, ticks: { precision: 0 }, title: { display: true, text: yLabel } },
      x: { ticks: { maxTicksLimit: 15, font: { size: 10 } } }
    }
  };
}

function applyFilters() {
  const kw = document.getElementById('searchInput').value.trim().toLowerCase();
  const period = document.getElementById('periodFilter').value;
  const src = document.getElementById('srcFilter').value;
  const dFrom = calFrom;
  const dTo = calTo;
  const now = new Date();
  const today = now.toISOString().slice(0, 10);
  const wd = (now.getDay() + 6) % 7;
  const ws = new Date(now); ws.setDate(now.getDate() - wd);
  const wstart = ws.toISOString().slice(0, 10);
  const ms = now.toISOString().slice(0, 7);

  filtered = ARTICLES.filter(a => {
    if (a.status !== 1) return false;
    if (kw && !(a.title || '').toLowerCase().includes(kw)) return false;
    if (selectedCates.length && !selectedCates.includes(a.cateName || '未分类')) return false;
    const d = dateStr(a.createTime);
    if (dFrom || dTo) {
      if (dFrom && (!d || d < dFrom)) return false;
      if (dTo && (!d || d > dTo)) return false;
    } else {
      if (period === 'today' && d !== today) return false;
      if (period === 'week' && !(d >= wstart && d <= today)) return false;
      if (period === 'month' && d.slice(0, 7) !== ms) return false;
    }
    if (src) {
      const m = POSTS[String(a.id)];
      const t = m ? (m.source || '手动') : '—';
      if (t !== src) return false;
    }
    return true;
  });
  filtered.sort((a, b) => (b.createTime || '').localeCompare(a.createTime || ''));
  document.getElementById('countInfo').textContent = `共 ${filtered.length} 篇`;
  page = 1;
  renderTable();
  saveState();
}

function syncCateChecks() {
  document.querySelectorAll('#cateList label').forEach(lab => {
    const cb = lab.querySelector('.cate-cb');
    if (cb) cb.checked = selectedCates.includes(lab.textContent.trim());
  });
}

function saveState() {
  const s = {
    kw: document.getElementById('searchInput').value.trim(),
    cates: selectedCates,
    period: document.getElementById('periodFilter').value,
    from: calFrom, to: calTo,
    src: document.getElementById('srcFilter').value
  };
  const empty = !s.kw && !s.cates.length && !s.period && !s.from && !s.to && !s.src;
  if (empty && !location.hash) return;
  history.replaceState(null, '', '#' + encodeURIComponent(JSON.stringify(s)));
}

function restoreState() {
  let s = {};
  try { s = JSON.parse(decodeURIComponent(location.hash.slice(1)) || '{}'); } catch (e) { s = {}; }
  if (s.kw) document.getElementById('searchInput').value = s.kw;
  if (s.period) document.getElementById('periodFilter').value = s.period;
  if (s.src) document.getElementById('srcFilter').value = s.src;
  if (Array.isArray(s.cates) && s.cates.length) {
    const opts = [...new Set(ARTICLES.map(a => a.cateName || '未分类'))];
    selectedCates = s.cates.filter(c => opts.includes(c));
    syncCateChecks();
  }
  if (s.from) calFrom = s.from;
  if (s.to) calTo = s.to;
  if (calFrom || calTo) updateCalBtn();
}

function renderTable() {
  const tb = document.getElementById('tbody');
  tb.innerHTML = '';
  const start = (page - 1) * PAGE_SIZE;
  const rows = filtered.slice(start, start + PAGE_SIZE);
  document.getElementById('empty').style.display = rows.length ? 'none' : 'block';
  rows.forEach(a => {
    const status = a.status === 1 ? '已发布' : '其他';
    const tr = document.createElement('tr');
    const link = a.id ? `https://openlab.cosmoplat.com/article-detils?id=${a.id}&articleType=0` : '#';
    let evalCell = '<td><span class="tag">—</span></td>';
    const rv = REVIEWS[a.id];
    if (rv) {
      const cls = rv.grade === '优秀' ? 'tag g' : rv.grade === '不合格' ? 'tag s' : rv.grade === '合格' ? 'tag o' : 'tag';
      evalCell = `<td><span class="${cls}" title="${escapeHtml(rv.comment)}">${rv.grade} ${rv.score}分</span></td>`;
    }
    let opCell = '<td style="white-space:nowrap">';
    opCell += `<button class="opt-btn" title="查看详情" onclick="openDetail(${a.id})">${ICON_EYE}</button>`;
    if (status === '已发布') opCell += `<button class="opt-btn" title="AI 优化" onclick="openOpt(${a.id})">${ICON_PEN}</button>`;
    if (HISTCOUNTS[String(a.id)]) opCell += `<button class="opt-btn hist" title="评分历史" onclick="openHist(${a.id})">${ICON_CLOCK}</button>`;
    if (status === '已发布') opCell += `<button class="opt-btn dist${distBtnCls(a.id)}" title="一键分发到所有已登录平台（各平台生成草稿）" onclick="openDistribute(${a.id}, this)">${ICON_SEND}</button>`;
    opCell += '</td>';
    const srcMeta = POSTS[String(a.id)];
    const srcTxt = srcMeta ? (srcMeta.source || '手动') : '—';
    const srcCls = srcTxt === '定时发文' ? 'tag g' : srcTxt === '发一篇' ? 'tag o' : 'tag';
    const checked = selectedIds.has(String(a.id)) ? ' checked' : '';
    tr.innerHTML = `
      <td style="text-align:center"><input type="checkbox" class="row-sel" data-aid="${a.id}"${checked}></td>
      <td class="title"><a href="${link}" target="_blank">${escapeHtml(a.title || '')}</a></td>
      <td><span class="tag">${escapeHtml(a.cateName || '未分类')}</span></td>
      <td>${escapeHtml(a.createTime || '')}</td>
      <td><span class="${srcCls}">${escapeHtml(srcTxt)}</span></td>
      <td><span class="tag">${status}</span></td>
      ${evalCell}
      <td class="num">${fmtNum(a.viewCount)}</td>
      <td class="num">${fmtNum(a.commentCount)}</td>
      <td class="num">${fmtNum(a.favor)}</td>
      <td class="num">${fmtNum(a.collect)}</td>
      ${opCell}`;
    tb.appendChild(tr);
  });
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  document.getElementById('pageInfo').textContent = `${page} / ${totalPages}`;
  document.getElementById('prevBtn').disabled = page <= 1;
  document.getElementById('nextBtn').disabled = page >= totalPages;
  const jump = document.getElementById('jumpInput');
  jump.max = totalPages;
  jump.value = page;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function renderScore() {
  const tag = document.getElementById('scoreTag');
  if (!SCORE || SCORE.score == null) {
    tag.innerHTML = '<span class="st-label">综合评分</span><span class="st-num">—</span>';
    return;
  }
  tag.innerHTML = `<span class="st-label">综合评分</span><span class="st-num">${SCORE.score}分</span>`;
}

function openModal() {
  if (!SCORE || SCORE.score == null) return;
  document.getElementById('modalTitle').textContent = '下周优化建议';
  document.getElementById('modalMeta').textContent =
    `评分周期 ${SCORE.week} · 覆盖 ${SCORE.articleCount} 篇 bot 文章 · 更新于 ${SCORE.updatedAt}`;
  let html = `<div style="font-size:34px;font-weight:800;color:#f59e0b;margin:6px 0 14px">${SCORE.score}<span style="font-size:15px;color:var(--muted)"> / 100 分</span></div>`;
  if (SCORE.avgDimensions) {
    Object.entries(SCORE.avgDimensions).forEach(([name, val]) => {
      const isWeak = (SCORE.weakest || []).includes(name);
      const pct = Math.min(100, val / 20 * 100);
      html += `<div class="dim-bar">
        <span class="name ${isWeak ? 'weak' : ''}">${escapeHtml(name)}${isWeak ? ' ⚠' : ''}</span>
        <div class="track"><div class="fill" style="width:${pct}%"></div></div>
        <span class="val">${val}</span>
      </div>`;
    });
  }
  (SCORE.suggestions || []).forEach(s => { html += `<div class="sugg">${escapeHtml(s)}</div>`; });
  document.getElementById('modalBody').innerHTML = html;
  document.getElementById('modalMask').classList.add('show');
}

document.getElementById('scoreTag').addEventListener('click', openModal);
document.getElementById('modalClose').addEventListener('click', () => document.getElementById('modalMask').classList.remove('show'));
document.getElementById('modalMask').addEventListener('click', e => {
  if (e.target === e.currentTarget) document.getElementById('modalMask').classList.remove('show');
});

let searchTimer = null;
document.getElementById('searchInput').addEventListener('input', () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(applyFilters, 250);
});
document.getElementById('periodFilter').addEventListener('change', applyFilters);
document.getElementById('srcFilter').addEventListener('change', applyFilters);
document.getElementById('prevBtn').addEventListener('click', () => goPage(page - 1));
document.getElementById('nextBtn').addEventListener('click', () => goPage(page + 1));
document.getElementById('jumpBtn').addEventListener('click', () => {
  const v = parseInt(document.getElementById('jumpInput').value, 10);
  if (!isNaN(v)) goPage(v);
});

function refreshData() {
  const btn = document.getElementById('refreshBtn');
  btn.disabled = true;
  btn.textContent = '刷新中…';
  fetch('api/refresh', { method: 'POST' }).then(r => {
    if (r.status === 401) {
      const tok = prompt('该操作需要口令，请输入:');
      if (!tok) { btn.disabled = false; btn.textContent = '刷新数据'; throw new Error('未输入口令'); }
      sessionStorage.setItem('authToken', tok);
      return fetch('api/refresh', { method: 'POST', headers: { 'X-Auth-Token': tok } });
    }
    return r;
  })
    .then(r => r.json().catch(() => ({})))
    .then(d => {
      if (d.error) { throw new Error(d.error); }
      const poll = setInterval(() => {
        fetchJson('api/refresh-status', { refreshing: false }).then(st => {
          if (!st.refreshing) {
            clearInterval(poll);
            loadAll().then(ok => {
              btn.disabled = false;
              btn.textContent = '刷新数据';
              alert(ok ? '数据已刷新' : '数据刷新完成，但加载失败');
            });
          }
        });
      }, 5000);
    })
    .catch(e => {
      btn.disabled = false;
      btn.textContent = '刷新数据';
      alert('刷新失败: ' + e.message);
    });
}

function csvRowsFrom(list) {
  const rows = [['标题', '分类', '发布时间', '来源', '状态', '评价', '浏览', '评论', '点赞', '收藏']];
  list.forEach(a => {
    const rv = REVIEWS[a.id];
    const srcMeta = POSTS[String(a.id)];
    const src = srcMeta ? (srcMeta.source || '手动') : '—';
    rows.push([
      a.title || '', a.cateName || '', a.createTime || '', src,
      a.status === 1 ? '已发布' : '其他',
      rv ? `${rv.grade} ${rv.score}` : '',
      a.viewCount || 0, a.commentCount || 0, a.favor || 0, a.collect || 0
    ]);
  });
  return rows;
}

function downloadCsv(rows, name) {
  const csv = '\uFEFF' + rows.map(r => r.map(v => {
    const s = String(v ?? '');
    return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }).join(',')).join('\n');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const aEl = document.createElement('a');
  aEl.href = url;
  aEl.download = name;
  document.body.appendChild(aEl);
  aEl.click();
  document.body.removeChild(aEl);
  URL.revokeObjectURL(url);
}

function exportCsv() {
  downloadCsv(csvRowsFrom(filtered), `台账导出_${todayStr()}.csv`);
}

function exportSelected() {
  const sel = filtered.filter(a => selectedIds.has(String(a.id)));
  if (!sel.length) { alert('请先勾选要导出的文章'); return; }
  downloadCsv(csvRowsFrom(sel), `台账选中_${sel.length}篇_${todayStr()}.csv`);
}

function updateSelState() {
  const btn = document.getElementById('exportSelBtn');
  btn.disabled = !selectedIds.size;
  btn.textContent = `导出选中(${selectedIds.size})`;
  const all = document.getElementById('selAll');
  if (all) all.checked = filtered.length > 0 && filtered.every(a => selectedIds.has(String(a.id)));
}

document.getElementById('refreshBtn').addEventListener('click', refreshData);

document.getElementById('publishBatchBtn').addEventListener('click', async function () {
  const btn = this;
  if (btn.disabled) return;
  btn.disabled = true;
  const oldText = btn.textContent;
  btn.textContent = '发布中…';
  try {
    const r = await apiPost('/api/publish-batch', { count: 2 });
    const d = await r.json();
    if (r.status !== 200) {
      alert('请求失败：' + (d.error || r.status));
    } else if (!d.ok) {
      alert(d.reason || d.error || '无法发布');
    } else {
      const names = (d.tasks || []).map(t => '《' + t.title + '》').join('、');
      alert('已开始发布：' + names + '\n每篇完成后自动发布下一篇，稍后刷新台账查看结果。');
      if (!planPollTimer) planPollTimer = setInterval(loadTodayPlan, 5000);
      loadTodayPlan();
    }
  } catch (err) {
    alert('请求失败：' + err.message);
  } finally {
    btn.textContent = oldText;
    btn.disabled = false;
  }
});
document.getElementById('exportBtn').addEventListener('click', exportCsv);
document.getElementById('exportSelBtn').addEventListener('click', exportSelected);
document.getElementById('selAll').addEventListener('change', e => {
  const on = e.target.checked;
  filtered.forEach(a => on ? selectedIds.add(String(a.id)) : selectedIds.delete(String(a.id)));
  document.querySelectorAll('.row-sel').forEach(cb => { cb.checked = on; });
  updateSelState();
});
document.getElementById('tbody').addEventListener('change', e => {
  const cb = e.target.closest('.row-sel');
  if (!cb) return;
  const aid = String(cb.dataset.aid);
  if (cb.checked) selectedIds.add(aid); else selectedIds.delete(aid);
  updateSelState();
});
document.getElementById('jumpInput').addEventListener('keydown', e => {
  if (e.key === 'Enter') document.getElementById('jumpBtn').click();
});

function goPage(p) {
  const total = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  if (p < 1) p = 1;
  if (p > total) p = total;
  page = p;
  renderTable();
}

function toggleStd() {
  const p = document.getElementById('stdPanel');
  const arrow = document.querySelector('#stdToggle .std-arrow');
  const open = p.style.display !== 'none';
  p.style.display = open ? 'none' : 'block';
  arrow.style.transform = open ? '' : 'rotate(180deg)';
  if (!open) p.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}
document.getElementById('stdToggle').addEventListener('click', toggleStd);
document.getElementById('stdBtn').addEventListener('click', () => document.getElementById('stdMask').classList.add('show'));
document.getElementById('stdClose').addEventListener('click', () => document.getElementById('stdMask').classList.remove('show'));
document.getElementById('stdMask').addEventListener('click', e => {
  if (e.target === e.currentTarget) document.getElementById('stdMask').classList.remove('show');
});

let calFrom = '', calTo = '', calTab = 'from', calYear = 0, calMonth = 0;
let calFresh = { from: 0, to: 0 };
const CAL_WEEK = ['日', '一', '二', '三', '四', '五', '六'];

function fmtDate(y, m, d) {
  return `${y}-${String(m + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
}
function updateCalBtn() {
  document.getElementById('calBtn').textContent = (calFrom || calTo)
    ? (calFrom ? calFrom.slice(5) : '…') + '~' + (calTo ? calTo.slice(5) : '…')
    : '自定义';
}
function renderCal() {
  document.getElementById('calTitle').textContent = `${calYear}年${calMonth + 1}月`;
  document.querySelectorAll('.cal-tab').forEach(b => b.classList.toggle('active', b.dataset.tab === calTab));
  document.getElementById('calRange').textContent =
    (calFrom ? calFrom.slice(5) : '未选') + ' ~ ' + (calTo ? calTo.slice(5) : '未选');
  const grid = document.getElementById('calGrid');
  let html = CAL_WEEK.map(w => `<div class="cal-dow">${w}</div>`).join('');
  const startDow = new Date(calYear, calMonth, 1).getDay();
  const daysInMonth = new Date(calYear, calMonth + 1, 0).getDate();
  for (let i = 0; i < startDow; i++) html += '<div class="cal-day blank"></div>';
  for (let d = 1; d <= daysInMonth; d++) {
    const ds = fmtDate(calYear, calMonth, d);
    let cls = 'cal-day';
    if (ds === calFrom || ds === calTo) cls += ' sel';
    else if (calFrom && calTo && ds > calFrom && ds < calTo) cls += ' inrange';
    html += `<button type="button" class="${cls}" data-date="${ds}">${d}</button>`;
  }
  grid.innerHTML = html;
}
function navCal(dir) {
  if (dir === '-year') calYear--;
  else if (dir === '+year') calYear++;
  else if (dir === '-month') { calMonth--; if (calMonth < 0) { calMonth = 11; calYear--; } }
  else { calMonth++; if (calMonth > 11) { calMonth = 0; calYear++; } }
  renderCal();
}
function pickDate(ds) {
  if (calTab === 'from') { calFrom = ds; calFresh.from++; }
  else { calTo = ds; calFresh.to++; }
  if (calFrom && calTo && calFrom > calTo) { const t = calFrom; calFrom = calTo; calTo = t; }
  calTab = calTab === 'from' ? 'to' : 'from';
  updateCalBtn();
  applyFilters();
  renderCal();
  if (calFresh.from > 0 && calFresh.to > 0) {
    document.getElementById('calPop').style.display = 'none';
  }
}
document.getElementById('calBtn').addEventListener('click', e => {
  e.stopPropagation();
  const pop = document.getElementById('calPop');
  if (pop.style.display === 'block') { pop.style.display = 'none'; return; }
  calFresh = { from: 0, to: 0 };
  const ref = calFrom || calTo || '';
  const base = ref ? new Date(ref + 'T00:00:00') : new Date();
  calYear = base.getFullYear();
  calMonth = base.getMonth();
  renderCal();
  pop.style.display = 'block';
});
document.querySelectorAll('.cal-tab').forEach(b => {
  b.addEventListener('click', e => { e.stopPropagation(); calTab = b.dataset.tab; renderCal(); });
});
document.querySelectorAll('.cal-nav').forEach(b => {
  b.addEventListener('click', e => { e.stopPropagation(); navCal(b.dataset.nav); });
});
document.getElementById('calGrid').addEventListener('click', e => {
  const b = e.target.closest('.cal-day');
  if (!b || b.classList.contains('blank')) return;
  pickDate(b.dataset.date);
});
document.getElementById('calClear').addEventListener('click', e => {
  e.stopPropagation();
  calFrom = ''; calTo = '';
  calFresh = { from: 0, to: 0 };
  updateCalBtn();
  applyFilters();
  renderCal();
});
document.getElementById('calDone').addEventListener('click', e => {
  e.stopPropagation();
  document.getElementById('calPop').style.display = 'none';
});
document.addEventListener('click', e => {
  const wrap = document.getElementById('cateWrap');
  if (!wrap.contains(e.target)) document.getElementById('catePanel').style.display = 'none';
  const cw = document.getElementById('calWrap');
  if (!cw.contains(e.target)) document.getElementById('calPop').style.display = 'none';
});

function onCateChange(e) {
  const cb = e.target;
  const c = cb.parentNode.textContent.trim();
  if (cb.checked) selectedCates.push(c);
  else selectedCates = selectedCates.filter(x => x !== c);
  updateCateBtn();
  applyFilters();
}
function updateCateBtn() {
  document.getElementById('cateBtn').textContent =
    selectedCates.length ? `分类 (${selectedCates.length})` : '全部分类';
}
document.getElementById('cateBtn').addEventListener('click', e => {
  e.stopPropagation();
  const p = document.getElementById('catePanel');
  const show = p.style.display === 'none';
  p.style.display = show ? 'block' : 'none';
  document.getElementById('cateAll').checked = selectedCates.length === 0;
});
document.getElementById('cateAll').addEventListener('change', e => {
  const on = e.target.checked;
  document.querySelectorAll('.cate-cb').forEach(cb => { cb.checked = on; });
  selectedCates = on ? [...new Set(ARTICLES.map(a => a.cateName || '未分类'))] : [];
  updateCateBtn();
  applyFilters();
});
document.addEventListener('click', e => {
  const wrap = document.getElementById('cateWrap');
  if (!wrap.contains(e.target)) document.getElementById('catePanel').style.display = 'none';
});

document.querySelectorAll('.seg button').forEach(btn => {
  btn.addEventListener('click', () => {
    const kind = btn.dataset.kind;
    const p = btn.dataset.p;
    currentPeriod[kind] = p;
    document.querySelectorAll(`.seg button[data-kind="${kind}"]`).forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    renderTrendChart(kind);
  });
});

function renderAll() {
  calcStats();
  renderCharts();
  applyFilters();
  document.getElementById('sub').textContent = DATA_UPDATED ? `数据更新 ${DATA_UPDATED}` : '加载中...';
}

function fetchJson(url, fallback, retries = 1) {
  const withTimeout = (p, ms) => Promise.race([
    p, new Promise((_, rej) => setTimeout(() => rej(new Error('加载超时')), ms))
  ]);
  const attempt = (n) => fetch(url)
    .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .catch(err => (n > 0 ? attempt(n - 1) : Promise.reject(err)));
  return withTimeout(attempt(retries), 15000).catch(() => fallback);
}

function apiPost(url, body) {
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Auth-Token': sessionStorage.getItem('authToken') || '' },
    body: JSON.stringify(body || {})
  }).then(async r => {
    if (r.status === 401) {
      const tok = prompt('该操作需要口令，请输入:');
      if (!tok) throw new Error('未输入口令');
      sessionStorage.setItem('authToken', tok);
      return apiPost(url, body);
    }
    return r;
  });
}

function loadAll() {
  const tb = document.getElementById('tbody');
  tb.innerHTML = Array(6).fill('<tr>' + Array(11).fill('<td class="skel"></td>').join('') + '</tr>').join('');
  return fetchJson('api/summary', null)
    .then(summary => {
      if (!summary) throw new Error('聚合数据为空');
      const json = {
        updatedAt: summary.updatedAt, memberId: summary.memberId,
        total: summary.total, categories: summary.categories, articles: summary.articles
      };
      REVIEWS = summary.reviews || {};
      SCORE = summary.botScore || null;
      HISTCOUNTS = summary.histCounts || {};
      POSTS = summary.posts || {};
      parseData(json);
      renderScore();
      DATA_UPDATED = json.updatedAt || '';
      document.getElementById('sub').textContent = `数据更新 ${DATA_UPDATED}`;
      loadTodayPlan();
      return true;
    })
    .catch(e => {
      document.getElementById('sub').textContent = '数据加载失败: ' + e.message;
      document.getElementById('tbody').innerHTML = `<tr><td colspan="12">数据加载失败</td></tr>`;
      return false;
    });
}

loadAll();
Promise.all([loadDistTasks(), refreshAuthView()]).then(() => renderTable());

function todayStr() {
  const d = new Date();
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function planStatus(s) {
  if (s === 'pending') return '<span class="tag pending">待发布</span>';
  if (s === 'published') return '<span class="tag ok">已发布</span>';
  return '<span class="tag">' + escapeHtml(s || '未知') + '</span>';
}

let planPollTimer = null;

function planBtn(it) {
  const st = it.status || 'pending';
  if (st === 'pending') {
    return `<button type="button" class="plan-btn plan-icon-btn" data-tid="${escapeHtml(it.taskId || '')}" title="立即发布">${ICON_SEND}</button>`;
  }
  const txt = st === 'published' ? '已发布' : st === 'publishing' ? '发布中…' : '其他';
  return `<button type="button" class="plan-btn" data-tid="${escapeHtml(it.taskId || '')}" disabled>${txt}</button>`;
}

let PLAN_EDIT_TID = null;

function openPlanEdit(tid) {
  fetch('/api/plan')
    .then(r => r.json())
    .then(d => {
      const it = (d.schedule || []).find(x => String(x.taskId) === String(tid));
      if (!it) return;
      PLAN_EDIT_TID = tid;
      document.getElementById('peTitle').value = it.title || '';
      document.getElementById('peTime').value = it.time || '';
      document.getElementById('peCate').value = it.category || '';
      document.getElementById('peSummary').value = it.summary || '';
      document.getElementById('planEditMask').classList.add('show');
    })
    .catch(() => alert('计划加载失败'));
}

function savePlanEdit() {
  const body = {
    taskId: PLAN_EDIT_TID,
    title: document.getElementById('peTitle').value.trim(),
    time: document.getElementById('peTime').value.trim(),
    category: document.getElementById('peCate').value.trim(),
    summary: document.getElementById('peSummary').value.trim()
  };
  apiPost('/api/plan-update', body)
    .then(r => r.json())
    .then(d => {
      if (d.ok) {
        document.getElementById('planEditMask').classList.remove('show');
        loadTodayPlan();
      } else {
        alert(d.error || d.reason || '保存失败');
      }
    })
    .catch(e => alert('保存失败: ' + e.message));
}

document.getElementById('peCancel').addEventListener('click', () => document.getElementById('planEditMask').classList.remove('show'));
document.getElementById('peSave').addEventListener('click', savePlanEdit);
document.getElementById('planEditClose').addEventListener('click', () => document.getElementById('planEditMask').classList.remove('show'));
document.getElementById('planEditMask').addEventListener('click', e => { if (e.target === e.currentTarget) document.getElementById('planEditMask').classList.remove('show'); });

function loadTodayPlan() {
  const body = document.getElementById('planBody');
  const empty = document.getElementById('planEmpty');
  body.innerHTML = '';
  empty.style.display = 'none';
  const d = new Date();
  const w = ['日', '一', '二', '三', '四', '五', '六'][d.getDay()];
  document.getElementById('planDate').textContent = `${todayStr()} 星期${w}`;
  fetch('/api/plan')
    .then(r => r.json())
    .then(data => {
      const today = todayStr();
      const items = (data.schedule || [])
        .filter(it => it.time && it.time.slice(0, 10) === today)
        .sort((a, b) => (a.time > b.time ? 1 : -1));
      if (!items.length) { empty.style.display = 'block'; return; }
      const rows = items.map((it, i) => {
        const cls = it.status === 'published' ? 'done' : 'next';
        return `<tr class="plan-${cls}">
          <td class="num">${i + 1}</td>
          <td>${planStatus(it.status)}</td>
          <td class="nowrap">${escapeHtml((it.time || '').slice(11, 19))}</td>
          <td class="plan-title" title="${escapeHtml(it.title || '')}">${escapeHtml(it.title || '')}</td>
          <td class="nowrap">${escapeHtml(it.category || '')}</td>
          <td class="plan-sum" title="${escapeHtml(it.summary || '')}">${escapeHtml(it.summary || '')}</td>
          <td class="nowrap">${planBtn(it)}${it.status === 'pending' ? `<button type="button" class="plan-btn plan-icon-btn plan-edit" data-tid="${escapeHtml(it.taskId || '')}" title="编辑计划">${ICON_PEN}</button>` : ''}</td>
        </tr>`;
      });
      body.innerHTML = rows.join('');
      const busy = items.some(it => (it.status || '') === 'publishing');
      if (!busy && planPollTimer) { clearInterval(planPollTimer); planPollTimer = null; }
      if (busy && !planPollTimer) planPollTimer = setInterval(loadTodayPlan, 5000);
    })
    .catch(e => {
      body.innerHTML = `<tr><td colspan="7" style="color:var(--muted)">计划加载失败: ${e.message}</td></tr>`;
      if (planPollTimer) { clearInterval(planPollTimer); planPollTimer = null; }
    });
}

document.getElementById('planBody').addEventListener('click', async e => {
  const edit = e.target.closest('.plan-edit');
  if (edit) { openPlanEdit(edit.dataset.tid); return; }
  const b = e.target.closest('.plan-btn');
  if (!b || b.disabled) return;
  const tid = b.dataset.tid;
  if (!tid) return;
  b.disabled = true;
  b.textContent = '发布中…';
  try {
    const r = await apiPost('/api/publish-now', { taskId: tid });
    const d = await r.json();
    if (r.status !== 200) {
      alert('请求失败：' + (d.error || r.status));
      loadTodayPlan();
      return;
    }
    if (!d.ok) {
      alert(d.reason || d.error || '无法发布');
      loadTodayPlan();
      return;
    }
    if (!planPollTimer) planPollTimer = setInterval(loadTodayPlan, 5000);
    loadTodayPlan();
  } catch (err) {
    alert('请求失败：' + err.message);
    loadTodayPlan();
  }
});

(function () {
  const mask = document.getElementById('oneshotMask');
  const input = document.getElementById('oneshotInput');
  const goBtn = document.getElementById('oneshotGo');
  const statusEl = document.getElementById('oneshotStatus');
  let polling = null;

  function open() { mask.classList.add('show'); input.value = ''; statusEl.textContent = ''; statusEl.className = 'oneshot-status'; setTimeout(() => input.focus(), 50); }
  function close() { mask.classList.remove('show'); stopPoll(); }

  function stopPoll() {
    if (polling) { clearInterval(polling); polling = null; }
  }

  function setStatus(msg, cls) {
    statusEl.textContent = msg;
    statusEl.className = 'oneshot-status' + (cls ? ' ' + cls : '');
  }

  async function submit() {
    const prompt = input.value.trim();
    if (!prompt) { setStatus('请输入标题或关键词', 'err'); return; }
    goBtn.disabled = true;
    try {
      const ck = await fetch('/api/check', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: prompt }),
      });
      const cd = await ck.json();
      if (cd.hit) {
        setStatus('输入内容包含敏感词，禁止提交：' + cd.words.join('、'), 'err');
        goBtn.disabled = false;
        return;
      }
    } catch (e) { /* 校验接口异常时交由后端兜底 */ }
    setStatus('已受理，正在生成内容并发布，预计 1-3 分钟...');
    try {
      const r = await apiPost('/api/publish', { prompt });
      const d = await r.json();
      if (r.status !== 200) { setStatus('提交失败：' + (d.error || r.status), 'err'); goBtn.disabled = false; return; }
      startPoll();
    } catch (e) {
      setStatus('提交失败：' + e.message, 'err');
      goBtn.disabled = false;
    }
  }

  function startPoll() {
    stopPoll();
    polling = setInterval(async () => {
      try {
        const r = await fetch('/api/status');
        const d = await r.json();
        if (d.state === 'done') {
          stopPoll(); goBtn.disabled = false;
          const res = d.result || {};
          if (res.status === 'published') {
            const url = `https://openlab.cosmoplat.com/article-detils?id=${res.article_id}&articleType=0`;
            setStatus(`发布成功！文章《${d.title}》（ID ${res.article_id}）\n链接：${url}`, 'ok');
            setTimeout(() => { close(); location.reload(); }, 6000);
          } else if (res.status === 'exists') {
            setStatus(`文章已存在（ID ${res.article_id}），未重复发布`, 'ok');
            goBtn.disabled = false;
          } else {
            setStatus('发布失败：' + (res.error || '未知错误'), 'err'); goBtn.disabled = false;
          }
        } else if (d.state === 'failed') {
          stopPoll(); goBtn.disabled = false;
          setStatus('生成失败：' + (d.error || '未知错误'), 'err');
        } else if (d.state === 'processing') {
          setStatus('生成中，请稍候（AI 生成正文 + 封面 + 发布，约 1-3 分钟）...');
        } else {
          stopPoll(); goBtn.disabled = false;
          setStatus('状态丢失，请重试', 'err');
        }
      } catch (e) {
        setStatus('查询状态失败：' + e.message, 'err');
      }
    }, 3000);
  }

  document.getElementById('fabBtn').addEventListener('click', open);
  document.getElementById('oneshotClose').addEventListener('click', close);
  document.getElementById('oneshotCancel').addEventListener('click', close);
  document.getElementById('oneshotGo').addEventListener('click', submit);
  input.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); } });
  mask.addEventListener('click', e => { if (e.target === mask) close(); });
})();

(function () {
  const mask = document.getElementById('commitMask');
  const list = document.getElementById('commitList');
  let COMMITS = [];

  function renderCommits() {
    const onlyFeat = document.getElementById('commitFeatOnly').checked;
    const items = COMMITS.filter(c => !onlyFeat || /^feat[(: ]/.test(c.message))
      .map(c =>
        `<div class="commit-item"><span class="cd">${c.date}</span><span class="ch">${c.hash}</span><span class="cm">${escapeHtml(c.message)}</span></div>`
      ).join('');
    list.innerHTML = items || '<div class="empty">暂无记录</div>';
  }

  document.getElementById('commitFeatOnly').addEventListener('change', renderCommits);
  document.getElementById('versionLink').addEventListener('click', async () => {
    mask.classList.add('show');
    list.innerHTML = '<div class="empty" style="padding:20px 0">加载中...</div>';
    try {
      const r = await fetch('/api/commits');
      const d = await r.json();
      COMMITS = d.commits || [];
      document.getElementById('commitMeta').textContent =
        `按时间倒序，共 ${d.total != null ? d.total : COMMITS.length} 条提交`;
      renderCommits();
    } catch (e) {
      list.innerHTML = '<div class="empty">加载失败</div>';
    }
  });
  document.getElementById('commitClose').addEventListener('click', () => mask.classList.remove('show'));
  mask.addEventListener('click', e => { if (e.target === mask) mask.classList.remove('show'); });
})();

function openOpt(aid) {
  OPT_AID = aid;
  const meta = document.getElementById('optMeta');
  meta.textContent = '加载中...';
  document.getElementById('optHint').textContent = '';
  document.getElementById('optStatus').innerHTML = '';
  document.getElementById('optTitle').value = '';
  document.getElementById('optSummary').value = '';
  document.getElementById('optContent').value = '';
  document.getElementById('optMask').classList.add('show');
  fetch(`/api/article?id=${aid}`).then(r => r.json()).then(d => {
    if (!d.ok) {
      document.getElementById('optStatus').innerHTML = '<span class="err">' + escapeHtml(d.error) + '</span>';
      return;
    }
    const a = d.article;
    meta.textContent = `文章ID ${a.id} · 浏览 ${a.viewCount} · 创建 ${a.createTime}`;
    document.getElementById('optTitle').value = a.title || '';
    document.getElementById('optSummary').value = a.summary || '';
    document.getElementById('optContent').value = a.content || '';
  }).catch(() => {
    document.getElementById('optStatus').innerHTML = '<span class="err">加载失败，请重试</span>';
  });
}

function closeOpt() {
  document.getElementById('optMask').classList.remove('show');
}

function aiOptimize() {
  const t = document.getElementById('optTitle').value.trim();
  const s = document.getElementById('optSummary').value.trim();
  const c = document.getElementById('optContent').value.trim();
  if (!c) { document.getElementById('optStatus').innerHTML = '<span class="err">正文不能为空</span>'; return; }
  const ai = document.getElementById('optAi');
  ai.disabled = true;
  document.getElementById('optHint').textContent = 'AI 优化生成中（约1-2分钟）...';
  document.getElementById('optStatus').innerHTML = '';
  apiPost('/api/optimize', { id: OPT_AID, title: t, summary: s, content: c }).then(r => r.json()).then(d => {
    ai.disabled = false;
    document.getElementById('optHint').textContent = '';
    if (!d.ok) {
      document.getElementById('optStatus').innerHTML = '<span class="err">' + escapeHtml(d.error) + '</span>';
      return;
    }
    document.getElementById('optTitle').value = d.result.title;
    document.getElementById('optSummary').value = d.result.summary;
    document.getElementById('optContent').value = d.result.body;
    document.getElementById('optStatus').innerHTML = '<span class="ok">已生成优化稿，请核对后提交更新</span>';
  }).catch(() => {
    ai.disabled = false;
    document.getElementById('optStatus').innerHTML = '<span class="err">AI优化请求失败，请重试</span>';
  });
}

function submitUpdate() {
  const t = document.getElementById('optTitle').value.trim();
  const s = document.getElementById('optSummary').value.trim();
  const c = document.getElementById('optContent').value.trim();
  if (!t || !c) { document.getElementById('optStatus').innerHTML = '<span class="err">标题与正文不能为空</span>'; return; }
  const sv = document.getElementById('optSave');
  sv.disabled = true;
  document.getElementById('optStatus').innerHTML = '<span>提交保存中，随后将自动重新评分...</span>';
  apiPost('/api/update', { id: OPT_AID, title: t, summary: s, content: c }).then(r => r.json()).then(d => {
    sv.disabled = false;
    if (!d.ok) {
      document.getElementById('optStatus').innerHTML = '<span class="err">' + escapeHtml(d.error) + '</span>';
      return;
    }
    const res = d.result;
    document.getElementById('optStatus').innerHTML = `<span class="ok">更新成功，重新评分：${res.score}分（${res.grade}），评分历史已记录</span>`;
    setTimeout(() => location.reload(), 1600);
  }).catch(() => {
    sv.disabled = false;
    document.getElementById('optStatus').innerHTML = '<span class="err">提交失败，请重试</span>';
  });
}

function openHist(aid) {
  const a = ARTICLES.find(x => x.id === aid);
  document.getElementById('histMeta').textContent = a ? (a.title || '') : ('文章 ' + aid);
  const tb = document.getElementById('histBody');
  tb.innerHTML = '';
  document.getElementById('histEmpty').style.display = 'none';
  document.getElementById('histMask').classList.add('show');
  fetch(`/api/scores?id=${aid}`).then(r => r.json()).then(d => {
    const h = d.ok ? (d.history || []) : [];
    document.getElementById('histEmpty').style.display = h.length ? 'none' : 'block';
    (h || []).forEach(e => {
      const tr = document.createElement('tr');
      const g = e.grade;
      const cls = g === '优秀' ? 'tag g' : g === '不合格' ? 'tag s' : g === '合格' ? 'tag o' : 'tag';
      tr.innerHTML = `<td>${escapeHtml(e.time || '')}</td>` +
        `<td class="num"><span class="${cls}">${e.score}分</span></td>` +
        `<td class="num">${escapeHtml(e.grade || '')}</td>` +
        `<td class="num">${escapeHtml(e.kind || '')}</td>` +
        `<td>${escapeHtml(e.comment || '').replace(/《社区文章质量评价标准》/g, '<a class="std-link" onclick="toggleStd()">《社区文章质量评价标准》</a>')}</td>`;
      tb.appendChild(tr);
    });
  }).catch(() => {
    document.getElementById('histEmpty').style.display = 'block';
    document.getElementById('histEmpty').textContent = '历史记录加载失败';
  });
}

document.getElementById('optClose').addEventListener('click', closeOpt);
document.getElementById('optCancel').addEventListener('click', closeOpt);
document.getElementById('optMask').addEventListener('click', e => { if (e.target === e.currentTarget) closeOpt(); });
document.getElementById('optAi').addEventListener('click', aiOptimize);
document.getElementById('optSave').addEventListener('click', submitUpdate);
document.getElementById('histClose').addEventListener('click', () => document.getElementById('histMask').classList.remove('show'));
document.getElementById('histMask').addEventListener('click', e => { if (e.target === e.currentTarget) document.getElementById('histMask').classList.remove('show'); });

const DIM_FULL = { '内容原创性': 20, '技术准确性': 20, '完整性结构': 15, '可读表达': 15, '规范合规': 15, '传播表现': 15 };
const DIM_ORDER = Object.keys(DIM_FULL);

function renderRadar(rv) {
  const key = 'radarChart';
  if (charts[key]) { charts[key].destroy(); delete charts[key]; }
  const bd = (rv && rv.breakdown) || {};
  const vals = DIM_ORDER.map(k => {
    const v = Number(bd[k]);
    return isNaN(v) ? 0 : Math.round(v / DIM_FULL[k] * 100);
  });
  charts[key] = new Chart(document.getElementById('radarChart'), {
    type: 'radar',
    data: {
      labels: DIM_ORDER.map(k => `${k}(${DIM_FULL[k]}分)`),
      datasets: [{
        label: '维度得分',
        data: vals,
        borderColor: '#2f6fed', backgroundColor: 'rgba(47,111,237,.18)',
        pointBackgroundColor: '#2f6fed', pointRadius: 3
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      scales: { r: { min: 0, max: 100, ticks: { stepSize: 20, backdropColor: 'transparent', font: { size: 10 } }, pointLabels: { font: { size: 11 } } } },
      plugins: { legend: { display: false } }
    }
  });
}

function openDetail(aid) {
  const a = ARTICLES.find(x => String(x.id) === String(aid));
  if (!a) return;
  const rv = REVIEWS[a.id];
  const srcMeta = POSTS[String(aid)];
  const src = srcMeta ? (srcMeta.source || '手动') : '—';
  document.getElementById('detailTitle').textContent = a.title || '';
  document.getElementById('detailMeta').innerHTML =
    `分类 ${escapeHtml(a.cateName || '未分类')} · ${escapeHtml(a.createTime || '')} · 来源 ${escapeHtml(src)}` +
    `<br>浏览 ${fmtNum(a.viewCount)} · 评论 ${fmtNum(a.commentCount)} · 点赞 ${fmtNum(a.favor)} · 收藏 ${fmtNum(a.collect)}` +
    `<br><a href="https://openlab.cosmoplat.com/article-detils?id=${aid}&articleType=0" target="_blank">打开原文 ↗</a>`;
  const gradeEl = document.getElementById('detailGrade');
  const scoreEl = document.getElementById('detailScore');
  const commentEl = document.getElementById('detailComment');
  if (rv && typeof rv.score === 'number') {
    const cls = rv.grade === '优秀' ? 'tag g' : rv.grade === '不合格' ? 'tag s' : rv.grade === '合格' ? 'tag o' : 'tag';
    gradeEl.className = cls;
    gradeEl.textContent = rv.grade || '';
    scoreEl.textContent = rv.score + ' 分';
    commentEl.textContent = rv.comment || '暂无评价说明';
  } else {
    gradeEl.className = 'tag';
    gradeEl.textContent = '未评价';
    scoreEl.textContent = '—';
    commentEl.textContent = '该文章暂无自动评价';
  }
  renderRadar(rv);
  document.getElementById('drawerMask').classList.add('show');
}

document.getElementById('drawerClose').addEventListener('click', () => document.getElementById('drawerMask').classList.remove('show'));
document.getElementById('drawerMask').addEventListener('click', e => { if (e.target === e.currentTarget) document.getElementById('drawerMask').classList.remove('show'); });

/* ============ 本机桥直连（优先路径，绕过公网网关） ============ */

function localBridgeStatus(ms = 2500) {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), ms);
  return fetch(LOCAL_BRIDGE + '/api/status', { signal: ac.signal, cache: 'no-store' })
    .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .catch(e => { clearTimeout(timer); throw e; });
}

function localBridgePost(path, body, ms = 0) {
  const ac = new AbortController();
  if (ms) setTimeout(() => ac.abort(), ms);
  return fetch(LOCAL_BRIDGE + path, {
    method: 'POST', signal: ac.signal,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {})
  }).then(r => r.json().catch(() => ({})).then(d => {
    if (!r.ok) throw new Error((d && d.error) || ('HTTP ' + r.status));
    return d;
  }));
}

function localAuthPlatforms(st) {
  return Object.keys(st.platforms || {}).filter(p => (st.platforms[p] || {}).authenticated);
}

/* ============ 一键分发（本机桥直连优先） ============ */

function distBtnCls(aid) {
  const t = DIST_TASKS[String(aid)];
  if (!t) return '';
  if (t.status === 'done') return ' dist-ok';
  if (t.status === 'failed') return ' dist-err';
  return ' dist-busy';
}

function loadDistTasks() {
  return fetchJson('api/distribute-history', { tasks: [] }).then(d => {
    DIST_TASKS = {};
    (d.tasks || []).forEach(t => {
      const k = String(t.article_id);
      if (!DIST_TASKS[k] || (t.created_at || '') > (DIST_TASKS[k].created_at || '')) DIST_TASKS[k] = t;
    });
    return d;
  });
}

function openDistribute(aid, btn) {
  const a = ARTICLES.find(x => String(x.id) === String(aid));
  const prev = DIST_TASKS[String(aid)];
  const tip = prev && (prev.status === 'done' || prev.status === 'failed')
    ? '已有一次分发记录，确定要重新分发吗？'
    : '将同步到本机桥当前已登录的全部平台';
  if (!confirm(`一键分发《${a ? a.title : ''}》？\n\n${tip}\n各平台生成草稿，需到平台后台点发布。`)) return;
  if (btn) { btn.disabled = true; btn.classList.remove('dist-ok', 'dist-err'); btn.classList.add('dist-busy'); }
  localBridgeStatus().then(async st => {
    const targets = localAuthPlatforms(st);
    if (!targets.length) throw new Error('本机桥当前无已登录平台：请先在「平台登录态」里对目标平台点「一键登录」');
    // 台账侧留一条任务记录（失败不阻断本机直连分发）
    let task = null;
    try {
      const r = await apiPost('api/distribute', { article_id: aid }).then(x => x.json().catch(() => ({})));
      if (r.ok) task = { task_id: r.task_id, article_id: aid, title: a ? a.title : '', created_at: r.created_at || '' };
    } catch (e) { task = null; }
    const d = await (await fetch(`/api/article?id=${aid}`)).json();
    if (!d.ok) throw new Error(d.error || '文章正文加载失败');
    const art = d.article || {};
    const content = (art.content || '').trim();
    if (!content) throw new Error('文章正文为空');
    const mimg = content.match(/!\[[^\]]*\]\((https?:[^)\s]+)\)/);
    // 多平台同步可能需数分钟：先在结果弹窗展示进行中状态
    document.getElementById('distMeta').textContent =
      `${art.title || ''} · 分发中（已登录 ${targets.length} 个平台，可能需要数分钟）…`;
    document.getElementById('distBody').innerHTML =
      `<tr><td colspan="3">正在同步到：${targets.map(distName).join('、')}</td></tr>`;
    document.getElementById('distMask').classList.add('show');
    const res = await localBridgePost('/api/sync', {
      title: art.title || '', markdown: content,
      cover: mimg ? mimg[1] : '', tags: [art.cateName || ''].filter(Boolean),
      platforms: targets
    }, 600000);
    if (res.error) throw new Error(res.error);
    const platforms = res.platforms || (res.result && res.result.platforms) || {};
    const raw = res.raw ? JSON.stringify(res.raw).slice(0, 8000) : '';
    const t = {
      task_id: task ? task.task_id : 'local-' + Date.now(), article_id: aid,
      status: res.status || (Object.values(platforms).some(x => x && x.ok) ? 'done' : 'failed'),
      title: art.title || '', created_at: task ? task.created_at : nowLocal(),
      finished_at: nowLocal(), platforms, raw
    };
    if (task) apiPost('api/distribute-result', { task_id: task.task_id, status: t.status, platforms, raw }).catch(() => {});
    DIST_TASKS[String(aid)] = t;
    renderTable();
    showDistResult(t);
  }).catch(e => {
    document.getElementById('distMask').classList.remove('show');
    if (btn) { btn.disabled = false; btn.classList.remove('dist-busy'); }
    const net = e instanceof TypeError || (e && e.name === 'AbortError')
      || /fetch/i.test(String((e && e.message) || e));
    alert('分发失败: ' + ((e && e.message) || e) + (net
      ? '\n\n本机桥未运行或无响应：请在 multiplatform 目录的 cmd 窗口执行 python mcp_bridge.py serve，并保持该窗口打开。'
      : ''));
  });
}

function pollDistTask(taskId, aid, btn) {
  if (DIST_POLLERS[taskId]) return;
  const start = Date.now();
  const timer = setInterval(() => {
    fetchJson('api/distribute-task?id=' + encodeURIComponent(taskId), { ok: false, task: null })
      .then(d => {
        const t = d.task;
        if (!t) return;
        if (t.status === 'pending' || t.status === 'running') {
          if (Date.now() - start > 600000) {
            clearInterval(timer);
            delete DIST_POLLERS[taskId];
            if (btn) { btn.disabled = false; btn.classList.remove('dist-busy'); }
            alert('仍在等待桥领取任务。请确认本机桥已运行、扩展已连接，且 Chrome 有激活的网页标签页。');
          }
          return;
        }
        clearInterval(timer);
        delete DIST_POLLERS[taskId];
        const cur = DIST_TASKS[String(aid)];
        if (!cur || (cur.created_at || '') <= (t.created_at || '')) DIST_TASKS[String(aid)] = t;
        renderTable();
        showDistResult(t);
      })
      .catch(() => {});
  }, 3000);
  DIST_POLLERS[taskId] = timer;
}

function distName(pid) {
  const m = (PLATFORM_META || []).find(x => x.id === pid);
  return m ? m.name : pid;
}

let DIST_OK_URLS = [];
let DIST_HAS_DRAFT_URL = false;

function showDistResult(t) {
  const plats = t.platforms || {};
  DIST_OK_URLS = [];
  DIST_HAS_DRAFT_URL = false;
  let rows = Object.keys(plats).map(pid => {
    const e = plats[pid] || {};
    const metaP = ((PLATFORM_META || []).find(m => m.id === pid) || {});
    const home = metaP.draftUrl || metaP.url || '';
    if (e.ok) {
      if (e.url) DIST_HAS_DRAFT_URL = true;
      const u = e.url || home;
      if (u) DIST_OK_URLS.push(u);
    }
    const st = e.ok ? '<span class="tag g">草稿已创建</span>' : '<span class="tag s">失败</span>';
    let detail;
    if (e.url) detail = `<a href="${escapeHtml(e.url)}" target="_blank" rel="noopener" title="${escapeHtml(e.url)}">打开草稿发布</a>`;
    else if (e.ok && home) detail = `<a href="${escapeHtml(home)}" target="_blank" rel="noopener">打开平台去发布</a>`;
    else detail = escapeHtml(e.error || '—');
    return `<tr><td>${escapeHtml(distName(pid))}</td><td>${st}</td><td>${detail}</td></tr>`;
  }).join('');
  if (!rows) rows = `<tr><td colspan="3">无平台返回结果${t.error ? '：' + escapeHtml(t.error) : ''}</td></tr>`;
  const rawBlock = t.raw
    ? `<details style="margin-top:10px"><summary style="cursor:pointer;font-size:12px;color:var(--muted)">查看扩展原始返回（排查用）</summary><pre style="font-size:11px;background:#f5f7fa;border:1px solid var(--border);border-radius:6px;padding:8px;max-height:220px;overflow:auto;white-space:pre-wrap;word-break:break-all">${escapeHtml(t.raw)}</pre></details>`
    : '';
  document.getElementById('distMeta').textContent =
    `${t.title || ''} · 创建 ${t.created_at || '—'} · 完成 ${t.finished_at || '—'}`;
  document.getElementById('distBody').innerHTML = rows;
  const rawBox = document.getElementById('distRaw');
  if (rawBox) rawBox.innerHTML = rawBlock;
  const btn = document.getElementById('distOpenBtn');
  if (btn) {
    btn.style.display = DIST_OK_URLS.length ? '' : 'none';
    btn.textContent = DIST_HAS_DRAFT_URL
      ? `一键打开 ${DIST_OK_URLS.length} 个发布页`
      : `一键打开 ${DIST_OK_URLS.length} 个平台（去发布）`;
    btn.disabled = false;
  }
  document.getElementById('distMask').classList.add('show');
}

function openAllDrafts() {
  if (!DIST_OK_URLS.length) return;
  const btn = document.getElementById('distOpenBtn');
  if (btn) { btn.disabled = true; btn.textContent = '正在打开…'; }
  localBridgePost('/api/open-drafts', { urls: DIST_OK_URLS }, 60000)
    .then(r => {
      if (btn) btn.textContent = `已打开 ${r.opened || 0} 个页面，请在各页点「发布」`;
    })
    .catch(e => {
      if (btn) {
        btn.disabled = false;
        btn.textContent = '打开失败：' + (e && e.message || e);
      }
    });
}

/* ============ 平台登录态 ============ */

function openAuthModal() {
  document.getElementById('authMask').classList.add('show');
  refreshAuthView();
}

function renderAuthRows(plats, has) {
  const list = (PLATFORM_META.length ? PLATFORM_META
    : Object.keys(plats || {}).map(id => ({ id, name: id })));
  document.getElementById('authBody').innerHTML = list.map(m => {
    const p = (plats || {})[m.id] || {};
    const st = !has ? '<span class="tag">未监测</span>'
      : (p.authenticated ? '<span class="tag g">已登录</span>' : '<span class="tag s">未登录</span>');
    const op = (!p.authenticated && m.url)
      ? `<button class="fab-btn ghost" style="padding:3px 10px;font-size:12px" onclick="window.open('${m.url}','_blank')">一键登录</button>`
      : '<span style="color:var(--muted)">—</span>';
    return `<tr><td>${escapeHtml(m.name || m.id)}</td><td>${st}</td><td>${escapeHtml(p.error || '')}</td><td class="num">${op}</td></tr>`;
  }).join('');
}

function refreshAuthView() {
  return localBridgeStatus().then(s => {
    AUTH_MODE = 'local';
    const plats = s.platforms || {};
    const has = !!s.platforms_ts;
    document.getElementById('authMeta').textContent =
      `桥在线（本机直连 127.0.0.1:8390）· 扩展${s.connected ? '已连接' : '未连接'} · ` +
      (has ? `最近检查 ${s.platforms_ts}` : '尚未检查登录态（可点「刷新登录态」）') +
      ' · 同步产出为草稿，需到各平台后台发布';
    renderAuthRows(plats, has);
    if (!PLATFORM_META.length) fetchJson('api/platform-auth', { platforms: {}, meta: [] })
      .then(d => { if (d && Array.isArray(d.meta) && d.meta.length) { PLATFORM_META = d.meta; renderAuthRows(plats, has); } });
    return { _local: true, platforms: plats, connected: s.connected };
  }).catch(() => {
    AUTH_MODE = 'ledger';
    return fetchJson('api/platform-auth', { platforms: {}, meta: [] }).then(d => {
      if (d && Array.isArray(d.meta) && d.meta.length) PLATFORM_META = d.meta;
      renderAuthTable(d || { platforms: {}, meta: [] });
      return d;
    });
  });
}

function bridgeOnline(d) {
  const ts = Number(d && d.last_poll_ts) || 0;
  return ts > 0 && (Date.now() / 1000 - ts) < 90;
}

function renderAuthTable(d) {
  const has = !!(d.platforms_ts);
  const online = bridgeOnline(d);
  let meta = has
    ? `最近检查 ${d.platforms_ts} · 扩展${d.connected ? '已连接' : '未连接'} · 同步产出为草稿，需到各平台后台发布`
    : '暂无登录态数据（桥连上扩展后会自动推送）';
  meta += online
    ? ` · 桥在线（最近轮询 ${d.last_poll_at}）`
    : ` · 桥离线（最近轮询 ${d.last_poll_at || '无记录'}）：请在本机更新并运行桥——解压新版 multiplatform.zip 覆盖后执行 python mcp_bridge.py serve`;
  document.getElementById('authMeta').textContent = meta;
  renderAuthRows(d.platforms || {}, has);
}

async function authRecheck() {
  const btn = document.getElementById('authRefresh');
  const hint = document.getElementById('authHint');
  btn.disabled = true;
  // 本机桥直连模式：直接调桥的 check-auth，无需经台账中转
  if (AUTH_MODE === 'local') {
    hint.textContent = '正在通知本机桥重新检查登录态…';
    try {
      const d = await localBridgePost('/api/check-auth', {}, 180000);
      btn.disabled = false;
      hint.textContent = d.ok ? '登录态已更新' : ('刷新失败: ' + (d.error || '未知错误'));
      refreshAuthView();
    } catch (e) {
      btn.disabled = false;
      hint.textContent = '本机桥无响应: ' + (e && e.message || e);
    }
    return;
  }
  hint.textContent = '通知桥重新检查中…';
  let ok = false;
  try {
    const r = await apiPost('api/platform-auth-recheck', {});
    const d = await r.json().catch(() => ({}));
    ok = !!d.ok;
  } catch (e) { ok = false; }
  if (!ok) {
    btn.disabled = false;
    hint.textContent = '通知桥失败，请检查网络';
    return;
  }
  hint.textContent = '已通知桥重新检查，等待中…';
  const d0 = await refreshAuthView();
  const oldTs = (d0 && d0.platforms_ts) || '';
  let n = 0;
  let last = d0;
  const t = setInterval(() => {
    n += 1;
    refreshAuthView().then(d => {
      last = d || last;
      if (last && last.platforms_ts && last.platforms_ts !== oldTs) {
        clearInterval(t);
        btn.disabled = false;
        hint.textContent = '登录态已更新';
      } else if (n >= 20) {
        clearInterval(t);
        btn.disabled = false;
        hint.textContent = bridgeOnline(last)
          ? '桥在线，但登录态暂未更新（可稍后再试）'
          : '桥未响应：请确认本机桥进程在运行（新版代码，config.json 已配 ledger_url）';
      }
    });
  }, 3000);
}

document.getElementById('authEntryBtn').addEventListener('click', openAuthModal);
document.getElementById('authClose').addEventListener('click', () => document.getElementById('authMask').classList.remove('show'));
document.getElementById('authMask').addEventListener('click', e => { if (e.target === e.currentTarget) document.getElementById('authMask').classList.remove('show'); });
document.getElementById('distClose').addEventListener('click', () => document.getElementById('distMask').classList.remove('show'));
document.getElementById('distMask').addEventListener('click', e => { if (e.target === e.currentTarget) document.getElementById('distMask').classList.remove('show'); });
document.getElementById('authRefresh').addEventListener('click', authRecheck);

function attachColResize() {
  document.querySelectorAll('.plan-table, .ledger-table').forEach(tbl => {
    tbl.querySelectorAll('th').forEach((th, i) => {
      if (th.querySelector('.col-resizer')) return;
      const rz = document.createElement('div');
      rz.className = 'col-resizer';
      th.appendChild(rz);
      const key = 'colw_' + (tbl.className || 'tbl') + '_' + i + '_' + (th.textContent.trim().slice(0, 8) || 'c' + i);
      const saved = localStorage.getItem(key);
      if (saved) th.style.width = saved + 'px';
      rz.addEventListener('mousedown', e => {
        e.preventDefault();
        const startX = e.clientX;
        const startW = th.getBoundingClientRect().width;
        const move = ev => {
          th.style.width = Math.max(40, startW + (ev.clientX - startX)) + 'px';
        };
        const up = () => {
          document.removeEventListener('mousemove', move);
          document.removeEventListener('mouseup', up);
          localStorage.setItem(key, th.getBoundingClientRect().width.toFixed(0));
        };
        document.addEventListener('mousemove', move);
        document.addEventListener('mouseup', up);
      });
    });
  });
}
attachColResize();
