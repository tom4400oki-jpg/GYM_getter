const API = '';

// ──────────────────────────────────────────
// 状態
// ──────────────────────────────────────────
let knownFacilities = [];   // FACILITY_NAV から取得した既知の施設
let discoveredAreas = [];   // API から取得した発見済みエリア
let allSlots = [];          // 空き状況スロット
let _areaRefreshTimer = null;
let _logRefreshTimer  = null;

// ──────────────────────────────────────────
// 起動
// ──────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  setupTabs();
  setupModal();

  await Promise.all([
    loadKnownFacilities(),
    loadDiscoveredAreas(),
  ]);

  renderCenterCards();
  await loadAvailability();
  await loadLogs();

  document.getElementById('btn-discover-all').addEventListener('click', () => discoverFacility('all'));
  document.getElementById('btn-check-all').addEventListener('click', checkAllAreas);
  document.getElementById('btn-filter').addEventListener('click', loadAvailability);
  document.getElementById('btn-refresh-logs').addEventListener('click', loadLogs);
});

// ──────────────────────────────────────────
// タブ切り替え
// ──────────────────────────────────────────
function setupTabs() {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById(`tab-${btn.dataset.tab}`).classList.add('active');
      if (btn.dataset.tab === 'logs') { clearTimeout(_logRefreshTimer); loadLogs(); }
      if (btn.dataset.tab === 'centers') { loadDiscoveredAreas().then(renderCenterCards); }
    });
  });
}

// ──────────────────────────────────────────
// モーダル
// ──────────────────────────────────────────
function setupModal() {
  document.getElementById('modal-backdrop').addEventListener('click', closeModal);
  document.getElementById('modal-close').addEventListener('click', closeModal);
}
function openModal(src) {
  document.getElementById('modal-img').src = src;
  document.getElementById('screenshot-modal').classList.remove('hidden');
}
function closeModal() {
  document.getElementById('screenshot-modal').classList.add('hidden');
}

// ──────────────────────────────────────────
// 既知の施設（FACILITY_NAV）
// ──────────────────────────────────────────
async function loadKnownFacilities() {
  try {
    const res = await fetch(`${API}/api/known-facilities`);
    knownFacilities = await res.json();
    // フィルタのセレクトに追加
    const sel = document.getElementById('filter-facility');
    knownFacilities.forEach(f => {
      const opt = document.createElement('option');
      opt.value = f.id;
      opt.textContent = f.name;
      sel.appendChild(opt);
    });
  } catch(e) { console.error('known-facilities:', e); }
}

// ──────────────────────────────────────────
// 発見済みエリア
// ──────────────────────────────────────────
async function loadDiscoveredAreas() {
  try {
    const res = await fetch(`${API}/api/facility-areas`);
    discoveredAreas = await res.json();

    // エリアフィルタも更新
    const asel = document.getElementById('filter-area');
    asel.innerHTML = '<option value="">全エリア</option>';
    const names = [...new Set(discoveredAreas.map(a => a.area_name))];
    names.forEach(n => {
      const opt = document.createElement('option');
      opt.value = n; opt.textContent = n;
      asel.appendChild(opt);
    });
  } catch(e) { console.error('facility-areas:', e); }
}

// ──────────────────────────────────────────
// 体育館カード描画
// ──────────────────────────────────────────
function renderCenterCards() {
  clearTimeout(_areaRefreshTimer);
  const container = document.getElementById('centers-list');
  container.innerHTML = '';

  if (knownFacilities.length === 0) {
    container.innerHTML = '<div class="empty">施設データを取得できません</div>';
    return;
  }

  let hasRunning = false;

  knownFacilities.forEach(fac => {
    // この施設のエリア（予約/抽選）
    const facAreas = discoveredAreas.filter(a => a.facility_id === fac.id);
    const discovered = facAreas.length > 0;

    // チェック中エリア
    if (facAreas.some(a => a.check_status === 'running')) hasRunning = true;

    const card = document.createElement('div');
    card.className = 'center-card';
    card.id = `center-${fac.id}`;

    // ─── カードヘッダー ───
    const totalAvail = facAreas.reduce((s, a) => s + (a.slots_available || 0) + (a.slots_partial || 0) + (a.slots_open || 0), 0);
    const availBadge = totalAvail > 0
      ? `<span class="avail-badge">空き ${totalAvail}件</span>`
      : '';

    card.innerHTML = `
      <div class="center-header">
        <div class="center-title-area">
          <span class="center-region">${fac.region}</span>
          <h2 class="center-name">${fac.name}</h2>
          ${availBadge}
        </div>
        <div class="center-actions">
          <button class="btn btn-outline" onclick="discoverFacility('${fac.id}')" id="btn-discover-${fac.id}">
            📋 施設リスト化
          </button>
          <button class="btn btn-success btn-sm ${!discovered ? 'disabled-btn' : ''}"
            onclick="${discovered ? `checkFacility('${fac.id}')` : `alert('先に「施設リスト化」を実行してください')`}"
            id="btn-check-fac-${fac.id}">
            ✓ 空き確認
          </button>
        </div>
      </div>
      <div class="center-body" id="center-body-${fac.id}">
        ${renderFacilityAreas(fac.id, facAreas, fac.has_lottery)}
      </div>
    `;

    container.appendChild(card);
  });

  // 実行中があれば3秒後に再描画
  if (hasRunning) {
    _areaRefreshTimer = setTimeout(async () => {
      await loadDiscoveredAreas();
      renderCenterCards();
    }, 3000);
  }
}

function renderFacilityAreas(facilityId, areas, hasLottery) {
  if (areas.length === 0) {
    return `<div class="center-undiscovered">
      「施設リスト化」ボタンを押すと予約・抽選できる施設が表示されます
    </div>`;
  }

  // 予約/抽選でグループ化
  const groups = {};
  areas.forEach(a => {
    groups[a.booking_type] = groups[a.booking_type] || [];
    groups[a.booking_type].push(a);
  });

  let html = '<div class="area-groups">';

  ['reservation', 'lottery'].forEach(bt => {
    if (!groups[bt]) return;
    const label = bt === 'reservation' ? '予約' : '抽選';
    const cls = bt === 'reservation' ? 'bt-reservation' : 'bt-lottery';
    html += `<div class="area-group-section">
      <div class="area-group-label">
        <span class="bt-badge ${cls}">${label}</span>
        <button class="btn btn-tiny" onclick="checkFacilityBt('${facilityId}','${bt}')">このグループをチェック</button>
      </div>
      <div class="area-table">`;

    groups[bt].forEach(a => {
      const statusClass = a.check_status === 'running' ? 'running'
        : a.check_status === 'success' ? 'success'
        : a.check_status === 'error' ? 'error' : 'none';

      const slotSummary = renderSlotSummary(a);
      const lastChecked = a.last_checked ? _shortDate(a.last_checked) : '';
      const statusLabel = a.check_status === 'running' ? '⏳ チェック中...'
        : a.check_status === 'error' ? '✗ エラー'
        : a.last_checked ? `✓ ${lastChecked}` : '未チェック';

      html += `
        <div class="area-row" id="area-row-${a.id}">
          <span class="area-idx">${a.area_index + 1}</span>
          <span class="area-name">${a.area_name}</span>
          <span class="area-slot-summary">${slotSummary}</span>
          <span class="area-status ${statusClass}">${statusLabel}</span>
          <button class="btn btn-tiny ${a.check_status === 'running' ? 'btn-disabled' : ''}"
            onclick="checkSingleArea(${a.id})"
            ${a.check_status === 'running' ? 'disabled' : ''}>
            チェック
          </button>
        </div>`;
    });

    html += '</div></div>';
  });

  html += '</div>';
  return html;
}

function renderSlotSummary(area) {
  if (!area.last_checked) return '<span style="color:var(--text-muted);font-size:11px">-</span>';
  const parts = [];
  if (area.slots_available > 0) parts.push(`<span class="slot-dot avail">○${area.slots_available}</span>`);
  if (area.slots_partial  > 0) parts.push(`<span class="slot-dot part">△${area.slots_partial}</span>`);
  if (area.slots_open     > 0) parts.push(`<span class="slot-dot open">◎${area.slots_open}</span>`);
  if (area.slots_occupied > 0) parts.push(`<span class="slot-dot occ">×${area.slots_occupied}</span>`);
  if (parts.length === 0 && area.slots_total > 0)
    parts.push(`<span style="color:var(--text-muted);font-size:11px">全${area.slots_total}日取得済</span>`);
  return parts.join(' ') || '<span style="color:var(--text-muted);font-size:11px">データなし</span>';
}

// ──────────────────────────────────────────
// アクション
// ──────────────────────────────────────────

async function discoverFacility(facilityId) {
  const isAll = facilityId === 'all';
  const btnId = isAll ? 'btn-discover-all' : `btn-discover-${facilityId}`;
  const btn = document.getElementById(btnId);
  if (btn) { btn.disabled = true; btn.textContent = '発見中...'; }

  const url = isAll
    ? `${API}/api/facility-areas/discover`
    : `${API}/api/facility-areas/discover/${facilityId}`;

  try {
    await fetch(url, { method: 'POST' });
    setGlobalStatus('エリア発見中... ログタブで進捗確認できます');
    // ログタブへ
    document.querySelector('[data-tab="logs"]').click();
    // ポーリング（発見完了を待つ）
    _pollUntilDone(async () => {
      await loadDiscoveredAreas();
      renderCenterCards();
    });
  } catch(e) {
    alert('エラー: ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = isAll ? '▶ 全施設リスト化' : '📋 施設リスト化'; }
  }
}

async function checkFacility(facilityId) {
  const btn = document.getElementById(`btn-check-fac-${facilityId}`);
  if (btn) { btn.disabled = true; btn.textContent = '開始中...'; }
  try {
    const res = await fetch(`${API}/api/facility-areas/check-facility/${facilityId}`, { method: 'POST' });
    const data = await res.json();
    setGlobalStatus(`${data.message}`);
    _startAreaPolling();
  } catch(e) {
    alert('エラー: ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '✓ 空き確認'; }
  }
}

async function checkFacilityBt(facilityId, bookingType) {
  const areas = discoveredAreas.filter(a => a.facility_id === facilityId && a.booking_type === bookingType);
  for (const a of areas) {
    await _callCheckSingleArea(a.id);
  }
  _startAreaPolling();
}

async function checkSingleArea(areaId) {
  await _callCheckSingleArea(areaId);
  _startAreaPolling();
}

async function _callCheckSingleArea(areaId) {
  const row = document.getElementById(`area-row-${areaId}`);
  if (row) {
    const statusEl = row.querySelector('.area-status');
    if (statusEl) { statusEl.className = 'area-status running'; statusEl.textContent = '⏳ チェック中...'; }
    const btn = row.querySelector('.btn-tiny');
    if (btn) { btn.disabled = true; }
  }
  try {
    await fetch(`${API}/api/facility-areas/${areaId}/check`, { method: 'POST' });
  } catch(e) {
    console.error('checkSingleArea error:', e);
  }
}

async function checkAllAreas() {
  const btn = document.getElementById('btn-check-all');
  btn.disabled = true; btn.textContent = '開始中...';
  try {
    await fetch(`${API}/api/availability/check-all`, { method: 'POST' });
    setGlobalStatus('全エリアをチェック開始... 順番に実行されます');
    _startAreaPolling();
    document.querySelector('[data-tab="logs"]').click();
  } catch(e) {
    alert('エラー: ' + e.message);
  } finally {
    btn.disabled = false; btn.textContent = '✓ 全施設チェック';
  }
}

function _startAreaPolling() {
  clearTimeout(_areaRefreshTimer);
  _areaRefreshTimer = setTimeout(async () => {
    await loadDiscoveredAreas();
    renderCenterCards();
    await loadAvailability();
  }, 4000);
}

function _pollUntilDone(cb) {
  // ログが running でなくなるまでcbを繰り返す
  let attempts = 0;
  async function _poll() {
    attempts++;
    await cb();
    if (attempts < 30) {
      _areaRefreshTimer = setTimeout(_poll, 5000);
    }
  }
  setTimeout(_poll, 5000);
}

function setGlobalStatus(msg) {
  const el = document.getElementById('global-status');
  el.textContent = msg;
  setTimeout(() => { el.textContent = ''; }, 30000);
}

// ──────────────────────────────────────────
// 空き状況一覧
// ──────────────────────────────────────────
async function loadAvailability() {
  const grid = document.getElementById('availability-grid');
  grid.innerHTML = '<div class="loading">読み込み中...</div>';

  const facilityId  = document.getElementById('filter-facility').value;
  const areaName    = document.getElementById('filter-area').value;
  const bookingType = document.getElementById('filter-booking-type').value;
  const status      = document.getElementById('filter-status').value;
  const dateFrom    = document.getElementById('filter-date-from').value;
  const dateTo      = document.getElementById('filter-date-to').value;

  let url = `${API}/api/availability?`;
  if (facilityId)  url += `facility_id=${facilityId}&`;
  if (areaName)    url += `area_name=${encodeURIComponent(areaName)}&`;
  if (bookingType) url += `booking_type=${bookingType}&`;
  if (status)      url += `status=${status}&`;
  if (dateFrom)    url += `date_from=${dateFrom}&`;
  if (dateTo)      url += `date_to=${dateTo}&`;

  try {
    const res = await fetch(url);
    allSlots = await res.json();
    renderSummary(allSlots);

    if (allSlots.length === 0) {
      grid.innerHTML = '<div class="empty">データがありません。体育館タブから「空き確認」を実行してください。</div>';
      return;
    }

    // 日付 × 施設でグループ化
    const groups = {};
    allSlots.forEach(slot => {
      const key = `${slot.date}__${slot.facility_id}`;
      if (!groups[key]) groups[key] = { date: slot.date, facility_id: slot.facility_id, slots: [] };
      groups[key].slots.push(slot);
    });

    grid.innerHTML = '';
    Object.values(groups).sort((a, b) => a.date.localeCompare(b.date)).forEach(group => {
      const fac = knownFacilities.find(f => f.id === group.facility_id);
      grid.appendChild(createDayCard(group, fac));
    });

    document.getElementById('last-updated').textContent = `最終更新: ${new Date().toLocaleTimeString('ja-JP')}`;
  } catch(e) {
    grid.innerHTML = `<div class="empty">エラー: ${e.message}</div>`;
  }
}

function renderSummary(slots) {
  const resAvail = slots.filter(s => s.booking_type !== 'lottery' && ['available','partial','open'].includes(s.status)).length;
  const lotAvail = slots.filter(s => s.booking_type === 'lottery'  && ['available','partial','open'].includes(s.status)).length;
  const occupied = slots.filter(s => s.status === 'occupied').length;
  const total    = slots.length;
  document.getElementById('availability-summary').innerHTML = `
    <div class="summary-item"><span class="dot dot-available"></span> 予約空き <strong>${resAvail}</strong>件</div>
    <div class="summary-item"><span class="dot dot-lottery"></span> 抽選空き <strong>${lotAvail}</strong>件</div>
    <div class="summary-item"><span class="dot dot-occupied"></span> 予約あり <strong>${occupied}</strong>件</div>
    <div class="summary-item" style="margin-left:auto;color:var(--text-muted)">合計 <strong>${total}</strong>件</div>
  `;
}

function createDayCard(group, fac) {
  const dateObj = new Date(group.date + 'T00:00:00');
  const weekday = ['日','月','火','水','木','金','土'][dateObj.getDay()];
  const card = document.createElement('div');
  card.className = 'day-card';
  card.innerHTML = `
    <div class="day-card-header">
      <span>${group.date} (${weekday})</span>
      <span class="facility-name">${fac ? fac.name : group.facility_id}</span>
    </div>
    <div class="slot-list"></div>
  `;
  const list = card.querySelector('.slot-list');
  group.slots.forEach(slot => {
    const item = document.createElement('div');
    item.className = `slot-item ${slot.status}`;
    const timeText = slot.time_start ? `${slot.time_start}〜${slot.time_end || ''}` : '（日単位）';
    const areaText = slot.area_name ? `<small class="area-label">${slot.area_name}</small>` : '';
    const btBadge = slot.booking_type === 'lottery'
      ? `<span class="bt-badge bt-lottery">抽選</span>`
      : `<span class="bt-badge bt-reservation">予約</span>`;
    const statusLabels = {
      available: '○ 全て空き', partial: '△ 一部空き', open: '◎ 一般開放',
      occupied: '× 予約あり', closed: '休館', rain: '雨天',
      cancelling: '取消処理中', planned: '開放予定', out_of_period: '受付期間外', unknown: '不明'
    };
    const label = statusLabels[slot.status] || slot.status_raw || slot.status;
    item.innerHTML = `
      <div>${btBadge}<span class="slot-time">${timeText}</span>${areaText}</div>
      <span class="slot-status status-${slot.status}">${label}</span>
    `;
    list.appendChild(item);
  });
  return card;
}

// ──────────────────────────────────────────
// 実行ログ
// ──────────────────────────────────────────
function _elapsedStr(startedAt) {
  if (!startedAt) return '-';
  const sec = Math.floor((Date.now() - new Date(startedAt)) / 1000);
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.floor(sec/60)}m${sec%60}s`;
  return `${Math.floor(sec/3600)}h${Math.floor((sec%3600)/60)}m`;
}

function _shortDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return `${d.getMonth()+1}/${d.getDate()} ${d.getHours()}:${String(d.getMinutes()).padStart(2,'0')}`;
}

async function loadLogs() {
  const tbody = document.getElementById('log-tbody');
  const indicator = document.getElementById('log-auto-refresh-indicator');
  clearTimeout(_logRefreshTimer);

  try {
    const res = await fetch(`${API}/api/logs`);
    const logs = await res.json();

    if (logs.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" class="empty">ログがありません</td></tr>';
      return;
    }

    const taskLabels = {
      detect_flow: 'フロー検出',
      check_availability: '空き確認',
      check_single_area: '個別チェック',
      discover_areas: 'エリア発見',
    };

    const hasRunning = logs.some(l => l.status === 'running');
    if (hasRunning) {
      _logRefreshTimer = setTimeout(loadLogs, 3000);
      indicator.textContent = '⏳ 実行中 — 3秒ごと自動更新';
      indicator.style.color = 'var(--yellow)';
    } else {
      indicator.textContent = '待機中';
      indicator.style.color = 'var(--text-muted)';
    }

    tbody.innerHTML = '';
    logs.forEach(l => {
      const fac = knownFacilities.find(f => f.id === l.facility_id);
      const name = fac ? fac.name : (l.facility_id === 'all' ? '全施設' : (l.facility_id || '-'));
      const started = l.started_at ? new Date(l.started_at).toLocaleString('ja-JP') : '-';
      const elapsed = l.status === 'running'
        ? `<span style="color:var(--yellow)">${_elapsedStr(l.started_at)}</span>`
        : (l.completed_at ? `${_elapsedStr(l.started_at)}` : '-');

      // 進捗メッセージ（running中は error_message に入る）
      const progressMsg = l.status === 'running' && l.error_message
        ? `<div class="log-progress">${escHtml(l.error_message)}</div>` : '';

      const row = document.createElement('tr');
      row.innerHTML = `
        <td style="white-space:nowrap">${started}</td>
        <td style="font-size:11px">${elapsed}</td>
        <td>${name}</td>
        <td>${taskLabels[l.task_type] || l.task_type}</td>
        <td>
          <span class="status-pill status-${l.status}">${l.status}</span>
          ${progressMsg}
        </td>
        <td>${l.result_count ?? '-'}</td>
        <td><button class="btn btn-tiny" onclick="toggleLogDetail(${l.id}, this)">詳細 ▼</button></td>
      `;
      tbody.appendChild(row);

      // 詳細展開行
      const detailRow = document.createElement('tr');
      detailRow.className = 'log-detail-row hidden';
      detailRow.id = `log-detail-row-${l.id}`;
      detailRow.innerHTML = `<td colspan="7"><div class="log-detail-content" id="log-detail-${l.id}">読み込み中...</div></td>`;
      tbody.appendChild(detailRow);
    });

  } catch(e) {
    tbody.innerHTML = `<tr><td colspan="7" class="empty">エラー: ${e.message}</td></tr>`;
  }
}

async function toggleLogDetail(logId, btn) {
  const row = document.getElementById(`log-detail-row-${logId}`);
  if (!row) return;
  const isHidden = row.classList.toggle('hidden');
  btn.textContent = isHidden ? '詳細 ▼' : '閉じる ▲';
  if (!isHidden) await loadLogDetail(logId);
}

async function loadLogDetail(logId) {
  const el = document.getElementById(`log-detail-${logId}`);
  if (!el) return;
  try {
    const res = await fetch(`${API}/api/logs/${logId}`);
    const data = await res.json();
    const lines = data.detail_log || [];

    let html = '';
    if (data.error_message && data.status !== 'running') {
      html += `<div class="log-error-block">${escHtml(data.error_message)}</div>`;
    }
    if (lines.length === 0) {
      html += '<div style="color:var(--text-muted);font-size:11px">詳細ログなし</div>';
    } else {
      html += '<div class="log-lines">';
      lines.forEach(entry => {
        const t = entry.time ? entry.time.split('T')[1].split('.')[0] : '';
        html += `<div class="log-line"><span class="log-time">${t}</span>${escHtml(entry.msg)}</div>`;
      });
      html += '</div>';
    }
    el.innerHTML = html;
  } catch(e) {
    el.textContent = 'エラー: ' + e.message;
  }
}

function escHtml(str) {
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
