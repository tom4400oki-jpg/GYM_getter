const API = '';  // 同一オリジンなので空文字でOK

// ──────────────────────────────────────────
// 状態
// ──────────────────────────────────────────
let facilities = [];
let selectedFlowFacility = null;

// ──────────────────────────────────────────
// 初期化
// ──────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  setupTabs();
  setupModal();
  await loadFacilities();
  await loadAvailability();
  await loadLogs();

  document.getElementById('btn-check-all').addEventListener('click', checkAll);
  document.getElementById('btn-detect-all').addEventListener('click', detectAll);
  document.getElementById('btn-filter').addEventListener('click', loadAvailability);
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

      if (btn.dataset.tab === 'logs') loadLogs();
    });
  });
}

// ──────────────────────────────────────────
// モーダル（スクリーンショット表示）
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
// 施設データ読み込み
// ──────────────────────────────────────────
async function loadFacilities() {
  try {
    const res = await fetch(`${API}/api/facilities`);
    facilities = await res.json();

    // フィルタセレクトに追加
    const sel = document.getElementById('filter-facility');
    facilities.forEach(f => {
      const opt = document.createElement('option');
      opt.value = f.id;
      opt.textContent = f.name;
      sel.appendChild(opt);
    });

    // フロータブのボタングループ
    const btnGroup = document.getElementById('facility-buttons');
    facilities.forEach(f => {
      const btn = document.createElement('button');
      btn.className = 'btn btn-secondary';
      btn.textContent = f.name;
      btn.addEventListener('click', () => {
        btnGroup.querySelectorAll('.btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        loadFlowSteps(f.id);
      });
      btnGroup.appendChild(btn);
    });

    // 個別チェックボタン（空きタブには不要なので割愛、フロータブにDetectボタンは別で）
  } catch (e) {
    console.error('施設データ読み込みエラー:', e);
  }
}

// ──────────────────────────────────────────
// 空き状況
// ──────────────────────────────────────────
async function loadAvailability() {
  const grid = document.getElementById('availability-grid');
  grid.innerHTML = '<div class="loading">読み込み中...</div>';

  const facilityId = document.getElementById('filter-facility').value;
  const status     = document.getElementById('filter-status').value;
  const dateFrom   = document.getElementById('filter-date-from').value;
  const dateTo     = document.getElementById('filter-date-to').value;

  let url = `${API}/api/availability?`;
  if (facilityId) url += `facility_id=${facilityId}&`;
  if (status)     url += `status=${status}&`;
  if (dateFrom)   url += `date_from=${dateFrom}&`;
  if (dateTo)     url += `date_to=${dateTo}&`;

  try {
    const res = await fetch(url);
    const slots = await res.json();

    // サマリー更新
    renderSummary(slots);

    if (slots.length === 0) {
      grid.innerHTML = '<div class="empty">データがありません。「全施設チェック」を実行してください。</div>';
      return;
    }

    // 日付 × 施設でグループ化
    const groups = {};
    slots.forEach(slot => {
      const key = `${slot.date}__${slot.facility_id}`;
      if (!groups[key]) groups[key] = { date: slot.date, facility_id: slot.facility_id, slots: [] };
      groups[key].slots.push(slot);
    });

    grid.innerHTML = '';
    Object.values(groups).sort((a, b) => a.date.localeCompare(b.date)).forEach(group => {
      const facility = facilities.find(f => f.id === group.facility_id);
      grid.appendChild(createDayCard(group, facility));
    });

    document.getElementById('last-updated').textContent = `最終更新: ${new Date().toLocaleTimeString('ja-JP')}`;
  } catch (e) {
    grid.innerHTML = `<div class="empty">エラー: ${e.message}</div>`;
  }
}

function renderSummary(slots) {
  const available = slots.filter(s => s.status === 'available').length;
  const lottery   = slots.filter(s => s.status === 'lottery').length;
  const occupied  = slots.filter(s => s.status === 'occupied').length;

  const bar = document.getElementById('availability-summary');
  bar.innerHTML = `
    <div class="summary-item"><span class="dot dot-available"></span> 空き <strong>${available}</strong>件</div>
    <div class="summary-item"><span class="dot dot-lottery"></span> 抽選 <strong>${lottery}</strong>件</div>
    <div class="summary-item"><span class="dot dot-occupied"></span> 使用中 <strong>${occupied}</strong>件</div>
  `;
}

function createDayCard(group, facility) {
  const dateObj = new Date(group.date + 'T00:00:00');
  const weekday = ['日', '月', '火', '水', '木', '金', '土'][dateObj.getDay()];
  const dateLabel = `${group.date} (${weekday})`;

  const card = document.createElement('div');
  card.className = 'day-card';
  card.innerHTML = `
    <div class="day-card-header">
      <span>${dateLabel}</span>
      <span class="facility-name">${facility ? facility.name : group.facility_id}</span>
    </div>
    <div class="slot-list"></div>
  `;

  const list = card.querySelector('.slot-list');
  group.slots.forEach(slot => {
    const item = document.createElement('div');
    item.className = `slot-item ${slot.status}`;
    const timeText = slot.time_start ? `${slot.time_start}〜${slot.time_end || ''}` : '時間不明';
    const areaText = slot.area_name ? `<small style="color:var(--text-muted)">${slot.area_name}</small>` : '';
    const statusLabel = { available: '○ 空き', occupied: '× 使用中', lottery: '△ 抽選', closed: '休館', unknown: '不明' };
    item.innerHTML = `
      <div>
        <span class="slot-time">${timeText}</span>
        ${areaText}
      </div>
      <span class="slot-status status-${slot.status}">${statusLabel[slot.status] || slot.status}</span>
    `;
    list.appendChild(item);
  });

  return card;
}

// ──────────────────────────────────────────
// 予約フロー
// ──────────────────────────────────────────
async function loadFlowSteps(facilityId) {
  selectedFlowFacility = facilityId;
  const container = document.getElementById('flow-steps');
  container.innerHTML = '<div class="loading">読み込み中...</div>';

  try {
    const res = await fetch(`${API}/api/booking-flows/${facilityId}`);
    const steps = await res.json();

    if (steps.length === 0) {
      container.innerHTML = `
        <div class="empty">
          予約フローデータがありません。<br>
          <button class="btn btn-detect" onclick="detectFlow('${facilityId}')" style="margin-top:12px">
            フロー検出を実行
          </button>
        </div>`;
      return;
    }

    container.innerHTML = '';

    // フロー検出ボタン追加
    const detectBtn = document.createElement('div');
    detectBtn.style.marginBottom = '12px';
    detectBtn.innerHTML = `<button class="btn btn-detect" onclick="detectFlow('${facilityId}')">🔍 フロー再検出</button>`;
    container.appendChild(detectBtn);

    steps.forEach(step => renderFlowStep(step, container));
  } catch (e) {
    container.innerHTML = `<div class="empty">エラー: ${e.message}</div>`;
  }
}

function renderFlowStep(step, container) {
  const stepEl = document.createElement('div');
  stepEl.className = 'flow-step';

  const loginBadge = step.requires_login ? '<span class="login-badge">要ログイン</span>' : '';
  const fields = step.form_fields || [];
  const links  = step.navigation_links || [];
  const screenshotFile = step.screenshot_path ? step.screenshot_path.split('/').pop().split('\\').pop() : '';

  stepEl.innerHTML = `
    <div class="flow-step-header">
      <div class="step-number ${step.requires_login ? 'login-required' : ''}">${step.step_number}</div>
      <div class="step-info">
        <div class="step-name">${step.step_name}${loginBadge}</div>
        <div class="step-url">${step.url || ''}</div>
      </div>
      <span class="step-toggle">›</span>
    </div>
    <div class="flow-step-body">
      ${step.notes ? `<div class="step-notes">${step.notes}</div>` : ''}

      <div class="form-fields">
        <h4>フォーム要素 (${fields.length}件)</h4>
        <div class="field-list">
          ${fields.slice(0, 20).map(f => `
            <div class="field-item">
              <span class="field-tag">${f.tag}</span>
              <span class="field-name">${f.name || f.id || '(名称なし)'}</span>
              ${f.type ? `<span style="color:var(--text-muted)">[${f.type}]</span>` : ''}
              ${f.required ? `<span style="color:var(--yellow);font-size:10px">必須</span>` : ''}
              ${f.options && f.options.length > 0
                ? `<span class="field-opts">選択肢: ${f.options.slice(0,5).map(o => o.text).join(', ')}${f.options.length > 5 ? '...' : ''}</span>`
                : ''}
            </div>
          `).join('')}
          ${fields.length > 20 ? `<div style="color:var(--text-muted);font-size:11px;padding:4px">...他 ${fields.length - 20}件</div>` : ''}
        </div>
      </div>

      ${links.length > 0 ? `
      <div class="nav-links">
        <h4>ナビゲーションリンク (${links.length}件)</h4>
        <div class="field-list">
          ${links.slice(0, 10).map(l => `
            <div class="field-item">
              <span class="field-name">${l.text}</span>
              <span class="field-opts">${l.href}</span>
            </div>
          `).join('')}
        </div>
      </div>` : ''}

      ${screenshotFile ? `
        <div class="screenshot-thumb" onclick="openModal('/screenshots/${screenshotFile}')">
          <img src="/screenshots/${screenshotFile}" alt="Step ${step.step_number}" loading="lazy">
          <small style="color:var(--text-muted);font-size:10px">クリックで拡大</small>
        </div>` : ''}

      <div style="color:var(--text-muted);font-size:10px;margin-top:8px">
        検出日時: ${step.detected_at ? new Date(step.detected_at).toLocaleString('ja-JP') : '-'}
      </div>
    </div>
  `;

  // アコーディオン
  const header = stepEl.querySelector('.flow-step-header');
  const body   = stepEl.querySelector('.flow-step-body');
  const toggle = stepEl.querySelector('.step-toggle');
  header.addEventListener('click', () => {
    const isOpen = body.classList.toggle('open');
    toggle.classList.toggle('open', isOpen);
  });

  container.appendChild(stepEl);
}

// ──────────────────────────────────────────
// アクション
// ──────────────────────────────────────────
async function checkAll() {
  const btn = document.getElementById('btn-check-all');
  btn.disabled = true;
  btn.textContent = '確認中...';
  try {
    const res = await fetch(`${API}/api/availability/check-all`, { method: 'POST' });
    const data = await res.json();
    alert(data.message);
    setTimeout(loadAvailability, 3000);  // 少し待ってから再読み込み
  } catch (e) {
    alert('エラー: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = '全施設チェック';
  }
}

async function detectAll() {
  const btn = document.getElementById('btn-detect-all');
  btn.disabled = true;
  btn.textContent = '検出中...';
  try {
    const res = await fetch(`${API}/api/booking-flows/detect-all`, { method: 'POST' });
    const data = await res.json();
    alert(data.message + '\nバックグラウンドで実行中です。しばらく後に「予約フロー」タブを確認してください。');
  } catch (e) {
    alert('エラー: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'フロー検出';
  }
}

async function detectFlow(facilityId) {
  try {
    const res = await fetch(`${API}/api/booking-flows/${facilityId}/detect`, { method: 'POST' });
    const data = await res.json();
    alert(data.message + '\nバックグラウンドで実行中です。');
    setTimeout(() => loadFlowSteps(facilityId), 5000);
  } catch (e) {
    alert('エラー: ' + e.message);
  }
}

// ──────────────────────────────────────────
// ログ
// ──────────────────────────────────────────
async function loadLogs() {
  const tbody = document.getElementById('log-tbody');
  tbody.innerHTML = '<tr><td colspan="6" class="loading">読み込み中...</td></tr>';
  try {
    const res  = await fetch(`${API}/api/logs`);
    const logs = await res.json();

    if (logs.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty">ログがありません</td></tr>';
      return;
    }

    const taskLabels = { detect_flow: 'フロー検出', check_availability: '空き確認' };

    tbody.innerHTML = logs.map(l => {
      const facility = facilities.find(f => f.id === l.facility_id);
      const name = facility ? facility.name : (l.facility_id || '-');
      const started = l.started_at ? new Date(l.started_at).toLocaleString('ja-JP') : '-';
      return `
        <tr>
          <td>${started}</td>
          <td>${name}</td>
          <td>${taskLabels[l.task_type] || l.task_type}</td>
          <td><span class="status-pill status-${l.status}">${l.status}</span></td>
          <td>${l.result_count ?? '-'}</td>
          <td style="color:var(--red);font-size:11px">${l.error_message ? l.error_message.slice(0, 80) : ''}</td>
        </tr>
      `;
    }).join('');
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="6" class="empty">エラー: ${e.message}</td></tr>`;
  }
}
