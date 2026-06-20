// ==================== ESTADO GLOBAL ====================
let cachedUsers = [];
let displayedUsers = [];
let currentPage = 1;
let rowsPerPage = 10;
let sortCol = 'last_seen';
let sortAsc = false;

let selectedIds = new Set();
let pendingAudios = [];
let isSpyPaused = true; // Por defecto ahora siempre está pausado en UI porque el backend lo maneja

let modalMode = 'create';


// Inspector
let currentInspectorDNI = null;
let isProcessingInsp = false;
let audioContext = null;
let recorderNode = null;
let wavBlob = null;
let mediaStream = null;


// ==================== HELPERS (Compatibilidad) ====================
function _numOr(val, fallback) {
  const n = Number(val);
  return Number.isFinite(n) ? n : fallback;
}

function _boolOr(val, fallback) {
  if (typeof val === 'boolean') return val;
  if (val === 'true') return true;
  if (val === 'false') return false;
  return fallback;
}

function _firstDefined(...vals) {
  for (const v of vals) {
    if (v !== undefined && v !== null) return v;
  }
  return undefined;
}

function _makeTraceId() {
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

// ==================== API COMPAT ====================
// Intenta varias rutas (snake/camel/legacy) y se queda con la primera que responda OK.
async function _fetchJsonAny(urls, options = {}) {
  let lastErr = null;
  for (const url of urls) {
    try {
      const res = await fetch(url, options);
      if (!res.ok) {
        lastErr = new Error(`HTTP ${res.status} en ${url}`);
        continue;
      }
      return await res.json();
    } catch (e) {
      lastErr = e;
    }
  }
  throw lastErr || new Error('Fetch failed');
}


// ==================== FUNCIONES TABLE & LOGIC ====================
async function cargarCRM() {
  try {
    const data = await _fetchJsonAny(['/userslist', '/users_list']);

    if (Array.isArray(data)) {
      cachedUsers = data;
    } else if (data && data.usuarios) {
      cachedUsers = data.usuarios;
    } else {
      cachedUsers = [];
    }
    applyFiltersAndSort();
  } catch (e) {
    console.error("Error cargando CRM", e);
    document.getElementById('crm-body').innerHTML =
      `<tr><td colspan="7" style="text-align:center; padding:24px; color:red;">Error de conexión.</td></tr>`;
  }
}

function applyFiltersAndSort() {
  const term = (document.getElementById('user-search')?.value || '').toLowerCase();

  // 1. Filtrar
  displayedUsers = (cachedUsers || []).filter(u => {
    const nombre = String(u.nombre || '').toLowerCase();
    const dni = String(u.dni || '').toLowerCase();
    return nombre.includes(term) || dni.includes(term);
  });

  // 2. Ordenar
  displayedUsers.sort((a, b) => {
    let valA = a?.[sortCol];
    let valB = b?.[sortCol];

    if (valA === undefined && sortCol === 'last_seen') valA = a.lastseen;
    if (valB === undefined && sortCol === 'last_seen') valB = b.lastseen;

    if (valA === undefined || valA === null || valA === '') valA = sortAsc ? 'zzzz' : '';
    if (valB === undefined || valB === null || valB === '') valB = sortAsc ? 'zzzz' : '';

    if (typeof valA === 'string') valA = valA.toLowerCase();
    if (typeof valB === 'string') valB = valB.toLowerCase();

    if (valA < valB) return sortAsc ? -1 : 1;
    if (valA > valB) return sortAsc ? 1 : -1;
    return 0;
  });

  // 3. Resetear página si es necesario
  const totalPages = Math.ceil(displayedUsers.length / rowsPerPage) || 1;
  if (currentPage > totalPages) currentPage = 1;
  if (currentPage < 1) currentPage = 1;

  updateMainCheckboxState();
  renderTableUI();
}

function renderTableUI() {
  const tbody = document.getElementById('crm-body');
  if (!tbody) return;

  const start = (currentPage - 1) * rowsPerPage;
  const end = start + rowsPerPage;
  const pageItems = displayedUsers.slice(start, end);

  // Pagination UI
  const pageInfo = document.getElementById('page-info');
  if (pageInfo) {
    pageInfo.innerText = `Mostrando ${displayedUsers.length === 0 ? 0 : start + 1}-${Math.min(end, displayedUsers.length)} de ${displayedUsers.length}`;
  }
  const btnPrev = document.getElementById('btn-prev');
  const btnNext = document.getElementById('btn-next');
  if (btnPrev) btnPrev.disabled = currentPage === 1;
  if (btnNext) btnNext.disabled = end >= displayedUsers.length;

  // Sort Icons
  document.querySelectorAll('.sort-icon').forEach(el => el.innerText = '');
  const icon = sortAsc ? 'arrow_upward' : 'arrow_downward';
  const activeIcon = document.getElementById(`sort-${sortCol}`);
  if (activeIcon) {
    activeIcon.classList.add('material-icons-outlined');
    activeIcon.innerText = icon;
    activeIcon.style.fontSize = '14px';
    activeIcon.style.verticalAlign = 'middle';
  }

  if (pageItems.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:24px;">No hay datos.</td></tr>`;
    return;
  }

  tbody.innerHTML = pageItems.map(u => {
    const score = _numOr(_firstDefined(u.auto_check, u.autocheck, 0), 0);
    const pct = (score * 100).toFixed(1);

    let qColor = '#10B981';
    if (score < 0.7) qColor = '#F59E0B';
    if (score < 0.5) qColor = '#EF4444';

    let lastSeenTxt = 'Nunca';
    const lastSeen = _firstDefined(u.last_seen, u.lastseen);
    if (lastSeen) {
      const d = new Date(lastSeen);
      const diffMin = Math.floor((new Date() - d) / 60000);
      if (diffMin < 60) lastSeenTxt = `Hace ${diffMin} min`;
      else if (diffMin < 1440) lastSeenTxt = `Hace ${Math.floor(diffMin / 60)} h`;
      else lastSeenTxt = d.toLocaleDateString();
    }

    const fraudes = _numOr(_firstDefined(u.intentos_fraude, u.intentosfraude, 0), 0);

    let riskBadge = `<button class="chip genuino btn-risk" onclick="openHistoryModal('${u.dni}')">Historial</button>`;
    if (fraudes > 0 && fraudes < 3) riskBadge = `<button class="chip warning btn-risk" onclick="openHistoryModal('${u.dni}')">${fraudes} Alertas</button>`;
    if (fraudes >= 3) riskBadge = `<button class="chip fraude btn-risk" onclick="openHistoryModal('${u.dni}')">ALTO (${fraudes})</button>`;

    const isChecked = selectedIds.has(u.dni) ? 'checked' : '';

    return `
      <tr class="${isChecked ? 'selected-row' : ''}">
        <td style="text-align:center;">
          <input type="checkbox" class="row-checkbox" ${isChecked} onchange="toggleSelect('${u.dni}')">
        </td>
        <td style="font-weight:600;">${u.dni}</td>
        <td>${u.nombre || ''}</td>
        <td>
          <div class="quality-bar-bg">
            <div class="quality-bar-fill" style="width:${pct}%; background:${qColor};"></div>
          </div>
          <span style="font-size:0.8rem;">${pct}%</span>
        </td>
        <td><span style="font-size:0.85rem; color:var(--text-muted);">${lastSeenTxt}</span></td>
        <td>${riskBadge}</td>
        <td style="white-space:nowrap;">
          <button class="btn-icon" title="Ver Ficha" onclick="openInspector('${u.dni}')">
            <span class="material-icons-outlined" style="font-size:18px;">visibility</span>
          </button>
          <button class="btn-icon" title="Editar" onclick="openUserModal('edit','${u.dni}','${(u.nombre || '').replace(/"/g, '&quot;')}')">
            <span class="material-icons-outlined" style="font-size:18px;">edit</span>
          </button>
          <button class="btn-icon danger" title="Eliminar" onclick="deleteUser('${u.dni}')">
            <span class="material-icons-outlined" style="font-size:18px;">delete</span>
          </button>
        </td>
      </tr>
    `;
  }).join('');

  updateBulkActionsUI();
}

function handleSearch() {
  currentPage = 1;
  applyFiltersAndSort();
}

function changePage(delta) {
  currentPage += delta;
  applyFiltersAndSort();
}

function sortBy(column) {
  if (sortCol === column) sortAsc = !sortAsc;
  else {
    sortCol = column;
    sortAsc = true;
  }
  applyFiltersAndSort();
}

function toggleSelect(dni) {
  if (selectedIds.has(dni)) selectedIds.delete(dni);
  else selectedIds.add(dni);
  updateMainCheckboxState();
  renderTableUI();
}

function toggleSelectAll() {
  const mainCheck = document.getElementById('check-all');
  const isChecked = !!mainCheck?.checked;
  displayedUsers.forEach(u => {
    if (isChecked) selectedIds.add(u.dni);
    else selectedIds.delete(u.dni);
  });
  renderTableUI();
}

function updateMainCheckboxState() {
  const mainCheck = document.getElementById('check-all');
  if (!mainCheck) return;
  if (displayedUsers.length === 0) {
    mainCheck.checked = false;
    return;
  }
  const allSelected = displayedUsers.every(u => selectedIds.has(u.dni));
  mainCheck.checked = allSelected;
}

function updateBulkActionsUI() {
  const bar = document.getElementById('bulk-actions');
  const count = document.getElementById('bulk-count');
  if (!bar || !count) return;

  if (selectedIds.size > 0) {
    bar.classList.remove('hidden');
    count.innerText = selectedIds.size;
  } else {
    bar.classList.add('hidden');
  }
}

async function bulkDelete() {
  if (!confirm(`Eliminar ${selectedIds.size} usuarios seleccionados?`)) return;
  const promises = Array.from(selectedIds).map(dni => fetch(`/users/${dni}`, { method: 'DELETE' }));
  await Promise.all(promises);
  selectedIds.clear();
  cargarCRM();
  alert('Usuarios eliminados.');
}


// ==================== SPY / STATUS (Mantenido visualmente para UI) ====================
async function toggleSpy(forceState = null) {
  // El backend siempre devolverá paused: true, esto mantiene el botón "apagado" visualmente.
  try {
    const data = await _fetchJsonAny(
      ['/togglespy', '/toggle_spy'],
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paused: true })
      }
    );
    isSpyPaused = !!data.paused;
    updateSpyBtn();
  } catch (e) {
    console.error('toggleSpy error', e);
  }
}

function updateSpyBtn() {
  const btn = document.getElementById('btn-toggle-spy');
  if (!btn) return;

  if (isSpyPaused) {
    btn.innerHTML = `<span class="material-icons-outlined">mic_off</span> Reposo (VAD Listo)`;
    btn.classList.remove('active');
    btn.style.color = 'var(--text-muted)';
  } else {
    btn.innerHTML = `<span class="material-icons-outlined">mic</span> Escuchando`;
    btn.classList.add('active');
    btn.style.color = '';
  }
}

async function checkStatus() {
  try {
    const data = await _fetchJsonAny(['/health']);

    const dot = document.getElementById('spy-dot');
    const txt = document.getElementById('spy-text');

    isSpyPaused = true;
    updateSpyBtn();

    if (data.status === 'ok') {
      if (dot) dot.classList.add('active');
      if (txt) {
        txt.textContent = `Online (${_numOr(data.usuarios, 0)})`;
        txt.style.color = 'var(--success)';
      }
    } else {
      if (dot) dot.classList.remove('active');
      if (txt) txt.textContent = 'Offline';
    }
  } catch (e) {
    const dot = document.getElementById('spy-dot');
    const txt = document.getElementById('spy-text');
    if (dot) dot.classList.remove('active');
    if (txt) txt.textContent = 'Offline';
  }
}


// ==================== TABS ====================
function showTab(tabId) {
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));

  const tab = document.getElementById(tabId);
  if (tab) tab.classList.add('active');

  const btns = document.querySelectorAll('.tab-btn');
  if (tabId === 'tab-dashboard' && btns[0]) btns[0].classList.add('active');
  if (tabId === 'tab-crm' && btns[1]) btns[1].classList.add('active');
  if (tabId === 'tab-historial' && btns[2]) btns[2].classList.add('active');
  if (tabId === 'tab-config' && btns[3]) btns[3].classList.add('active');

  if (tabId === 'tab-crm') cargarCRM();
  if (tabId === 'tab-historial') cargarHistorialGlobal();
  if (tabId === 'tab-config') loadConfig();
}


// ==================== TELEMETRÍA AUTOMÁTICA (Monitor) ====================
function _renderEstadoChip(estadoRaw) {
  const estado = String(estadoRaw || '').toUpperCase();

  let chipClass = 'insuficiente';
  let label = estado || '...';
  let icon = '';

  if (estado === 'GENUINO') {
    chipClass = 'genuino';
  } else if (estado === 'FRAUDE') {
    chipClass = 'fraude';
  } else if (estado === 'DEEPFAKE') {
    chipClass = 'deepfake';
    icon = `<span class="material-icons-outlined" style="font-size:16px; line-height:1; vertical-align:middle; margin-right:6px;">warning</span>`;
  } else if (estado === 'AUDIO_INSUFICIENTE') {
    chipClass = 'insuficiente';
  } else {
    chipClass = 'insuficiente';
  }

  return `<span class="chip ${chipClass}">${icon}${label}</span>`;
}

async function updateMonitor() {
  try {
    const raw = await _fetchJsonAny(['/verifylog', '/verify_log']);

    const logData = Array.isArray(raw) ? raw : (raw.log || []);
    if (raw && raw.pipeline) updateVisualPipeline(raw.pipeline);

    const tbody = document.getElementById('monitor-body');
    if (!tbody) return;

    if (!logData || logData.length === 0) {
      tbody.innerHTML = `<tr><td colspan="5" style="text-align:center; padding:40px; color:var(--text-muted);">Esperando datos de sensores...</td></tr>`;
      return;
    }

    tbody.innerHTML = logData.map(row => {
      const ts = row.timestamp || '--:--:--';
      const dni = _firstDefined(row.dni_reclamado, row.dnireclamado, row.dni, '-') || '-';
      const estado = row.estado || '';
      const score = _safeScore(row.score);
      const msg = row.mensaje || '';

      return `
        <tr>
          <td style="color:var(--text-muted);">${ts}</td>
          <td><span style="font-weight:600; color:var(--text-main);">${dni}</span></td>
          <td>${_renderEstadoChip(estado)}</td>
          <td style="font-family:monospace;">${score.toFixed(4)}</td>
          <td style="color:var(--text-muted); font-size:0.85rem;">${msg}</td>
        </tr>
      `;
    }).join('');

  } catch (e) {
    // silencioso
  }
}

function _safeScore(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0.0;
}


// ==================== MODAL USUARIO (crear/editar) ====================
function openUserModal(mode, dni = '', nombre = '') {
  modalMode = mode;
  document.getElementById('modal-usuario').classList.remove('hidden');

  const title = document.getElementById('modal-user-title');
  const inputDni = document.getElementById('input-dni');
  const inputNombre = document.getElementById('input-nombre');
  const bioSection = document.getElementById('biometria-section');
  const editActions = document.getElementById('edit-actions');
  const btnTrain = document.getElementById('btn-train');

  document.getElementById('train-result')?.classList.add('hidden');
  document.getElementById('train-progress')?.classList.add('hidden');

  pendingAudios = [];
  renderQueue();

  if (mode === 'create') {
    if (title) title.textContent = 'Nuevo Usuario';
    if (inputDni) { inputDni.value = ''; inputDni.disabled = false; }
    if (inputNombre) inputNombre.value = '';
    if (bioSection) bioSection.classList.remove('hidden');
    if (editActions) editActions.classList.add('hidden');
    if (btnTrain) btnTrain.textContent = 'Entrenar Modelo';
  } else {
    if (title) title.textContent = 'Editar Identidad';
    if (inputDni) { inputDni.value = dni; inputDni.disabled = true; }
    if (inputNombre) inputNombre.value = nombre;
    if (bioSection) bioSection.classList.add('hidden');
    if (editActions) editActions.classList.remove('hidden');
    if (btnTrain) btnTrain.textContent = 'Guardar Cambios';
  }
}

function closeUserModal() {
  document.getElementById('modal-usuario').classList.add('hidden');
}

function handleUserSubmit(e) {
  e.preventDefault();
  if (modalMode === 'create') crearUsuario(e);
  else editarUsuario(e);
}

async function editarUsuario(e) {
  e.preventDefault();
  const dni = document.getElementById('input-dni').value;
  const nombre = document.getElementById('input-nombre').value;

  try {
    const res = await fetch(`/users/${dni}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ nombre })
    });

    if (res.ok) {
      alert('Usuario actualizado.');
      closeUserModal();
      cargarCRM();
    } else {
      alert('Error al actualizar.');
    }
  } catch (err) {
    alert('Error: ' + err.message);
  }
}

function addToQueueFromInput() {
  const input = document.getElementById('train-file-input');
  if (!input || input.files.length === 0) return;

  Array.from(input.files).forEach(file => {
    pendingAudios.push({ blob: file, filename: file.name, source: 'file' });
  });
  input.value = '';
  renderQueue();
}

function renderQueue() {
  const list = document.getElementById('audio-queue-list');
  if (!list) return;
  list.innerHTML = '';

  if (pendingAudios.length === 0) {
    list.innerHTML = `<li class="empty-queue">Cola vacía.</li>`;
    return;
  }

  pendingAudios.forEach(item => {
    const li = document.createElement('li');
    const icon = (item.source === 'mic') ? 'mic_none' : 'audio_file';
    li.innerHTML = `
      <span class="material-icons-outlined icon">${icon}</span>
      <span style="flex:1;">${item.filename}</span>
      <span style="font-size:0.75rem; color:var(--text-muted);">Pendiente</span>
    `;
    list.appendChild(li);
  });
}


// Grabación manual
let createAudioCtx = null;
let createMediaStream = null;
let createRecorderNode = null;
let createRecordingBuffer = [];

async function toggleCreateRecording() {
  const btn = document.getElementById('btn-rec-queue');
  const status = document.getElementById('rec-queue-status');

  if (!createAudioCtx) {
    try {
      createMediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      createAudioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });

      const source = createAudioCtx.createMediaStreamSource(createMediaStream);
      createRecorderNode = createAudioCtx.createScriptProcessor(4096, 1, 1);
      createRecordingBuffer = [];

      createRecorderNode.onaudioprocess = (e) => {
        createRecordingBuffer.push(new Float32Array(e.inputBuffer.getChannelData(0)));
      };

      source.connect(createRecorderNode);
      createRecorderNode.connect(createAudioCtx.destination);

      if (btn) {
        btn.innerHTML = `<span class="material-icons-outlined" style="vertical-align:middle; font-size:18px;">stop_circle</span>`;
        btn.style.color = 'var(--error)';
      }
      if (status) status.classList.remove('hidden');

    } catch (e) {
      alert('Micro error: ' + (e && e.message ? e.message : e));
    }
  } else {
    try { createRecorderNode.disconnect(); } catch (_) {}
    try { createMediaStream.getTracks().forEach(t => t.stop()); } catch (_) {}

    const length = createRecordingBuffer.reduce((acc, chunk) => acc + chunk.length, 0);
    const result = new Float32Array(length);
    let offset = 0;
    for (let chunk of createRecordingBuffer) {
      result.set(chunk, offset);
      offset += chunk.length;
    }

    const wav = encodeWAV(result, 16000);
    pendingAudios.push({ blob: wav, filename: `micrec_${Date.now()}.wav`, source: 'mic' });

    try { await createAudioCtx.close(); } catch (_) {}
    createAudioCtx = null;
    createMediaStream = null;
    createRecorderNode = null;
    createRecordingBuffer = [];

    if (btn) {
      btn.innerHTML = `<span class="material-icons-outlined" style="vertical-align:middle; font-size:18px;">mic_none</span>`;
      btn.style.color = '';
    }
    if (status) status.classList.add('hidden');

    renderQueue();
  }
}

async function crearUsuario(e) {
  e.preventDefault();

  const form = document.getElementById('form-create-user');
  const formData = new FormData();
  const dni = form.querySelector('[name="dni"]').value;
  const nombre = form.querySelector('[name="nombre"]').value;

  if (!dni || !nombre) {
    alert('Complete DNI y Nombre');
    return;
  }

  formData.append('dni', dni);
  formData.append('nombre', nombre);

  let filesAppended = 0;
  pendingAudios.forEach(item => {
    formData.append('files', item.blob, item.filename);
    filesAppended++;
  });

  if (filesAppended === 0) {
    alert('Selecciona archivos o graba audios.');
    return;
  }

  const btn = document.getElementById('btn-train');
  const resultDiv = document.getElementById('train-result');
  const progressPanel = document.getElementById('train-progress');
  const fill = document.getElementById('progress-fill');

  if (btn) btn.disabled = true;
  if (resultDiv) resultDiv.classList.add('hidden');
  if (progressPanel) progressPanel.classList.remove('hidden');
  if (fill) fill.style.width = '0%';
  const progressText = document.getElementById('progress-text');
  if (progressText) progressText.textContent = 'Iniciando...';

  ['LOAD','SLICE','TRAIN','UPLOAD','DONE'].forEach(s => {
    const el = document.getElementById(`step-${s}`);
    if (el) el.classList.remove('active','done');
  });

  let polling = null;

  try {
    const res = await fetch('/users', { method: 'POST', body: formData });
    if (!res.ok) {
      let detail = '';
      try { const j = await res.json(); detail = j.detail || ''; } catch(_) {}
      throw new Error(detail || 'Error');
    }

    if (btn) btn.innerText = 'Entrenando...';

    polling = setInterval(async () => {
      try {
        const resP = await fetch(`/progress?dni=${dni}`);
        const data = await resP.json();

        if (data.percent !== undefined && fill) fill.style.width = `${data.percent}%`;
        if (data.msg && progressText) progressText.textContent = data.msg;
        if (data.step) updateChecklist(data.step);

        if (data.percent >= 100 || data.step === 'DONE') {
          clearInterval(polling);
          if (fill) fill.style.width = '100%';
          updateChecklist('DONE');
          setTimeout(() => {
            if (progressPanel) progressPanel.classList.add('hidden');
            alert(`Usuario creado: ${nombre}`);
            closeUserModal();
            cargarCRM();
            if (btn) btn.disabled = false;
          }, 600);
        }

        if (data.step === 'ERROR') {
          clearInterval(polling);
          throw new Error(data.msg || 'Error entrenando');
        }
      } catch (e) {
        try { clearInterval(polling); } catch(_) {}
      }
    }, 1000);

  } catch (err) {
    if (polling) clearInterval(polling);
    if (progressPanel) progressPanel.classList.add('hidden');
    if (resultDiv) {
      resultDiv.className = 'error-msg';
      resultDiv.textContent = `Error: ${err.message}`;
      resultDiv.classList.remove('hidden');
    }
    if (btn) btn.disabled = false;
  }
}

function updateChecklist(step) {
  const steps = ['LOAD','SLICE','TRAIN','UPLOAD','DONE'];
  let passed = true;
  steps.forEach(s => {
    const el = document.getElementById(`step-${s}`);
    if (!el) return;

    if (s === step) {
      el.classList.add('active');
      el.classList.remove('done');
      passed = false;
    } else if (passed) {
      el.classList.add('done');
      el.classList.remove('active');
    } else {
      el.classList.remove('active');
      el.classList.remove('done');
    }
  });

  if (step === 'DONE') {
    const el = document.getElementById('step-DONE');
    if (el) el.classList.add('done');
  }
}

async function deleteUser(dni) {
  if (!confirm('Seguro que quieres borrar?')) return;
  try {
    const res = await fetch(`/users/${dni}`, { method: 'DELETE' });
    if (res.ok) cargarCRM();
  } catch (e) {}
}


// ==================== HISTORIAL (Riesgo) ====================
async function openHistoryModal(dni) {
  document.getElementById('modal-history').classList.remove('hidden');
  document.getElementById('hist-title-dni').innerText = dni;
  currentInspectorDNI = null;

  const thead = document.querySelector('#modal-history thead tr');
  if (thead) thead.innerHTML = `<th>Fecha</th><th>Estado</th><th>Score</th><th>Detalle</th>`;

  const tbody = document.getElementById('history-body');
  if (tbody) tbody.innerHTML = `<tr><td colspan="4" style="text-align:center; padding:20px;">Cargando...</td></tr>`;

  try {
    // Compat: backend actual usa /userhistory/{dni}; se mantiene /user_history/{dni} por si existe
    const data = await _fetchJsonAny([`/userhistory/${dni}`, `/user_history/${dni}`]);

    if (!data.history || data.history.length === 0) {
      if (tbody) tbody.innerHTML = `<tr><td colspan="4" style="text-align:center; padding:20px; color:var(--text-muted);">Sin pruebas registradas.</td></tr>`;
      return;
    }

    if (tbody) {
      tbody.innerHTML = data.history.map(h => {
        const date = new Date(h.fecha).toLocaleString();
        const scorePct = (_safeScore(h.score) * 100).toFixed(1);

        let statusColor = 'var(--text-main)';
        if (h.estado === 'GENUINO') statusColor = 'var(--success)';
        else if (h.estado === 'FRAUDE') statusColor = 'var(--error)';
        else if (h.estado === 'DEEPFAKE') statusColor = 'var(--error)';

        return `
          <tr>
            <td style="font-size:0.85rem;">${date}</td>
            <td><span style="font-weight:600; color:${statusColor};">${h.estado}</span></td>
            <td style="font-family:monospace;">${scorePct}</td>
            <td style="color:var(--text-muted); font-size:0.8rem;">${h.mensaje || '-'}</td>
          </tr>
        `;
      }).join('');
    }

  } catch (e) {
    if (tbody) tbody.innerHTML = `<tr><td colspan="4" style="text-align:center; color:var(--error);">Error al cargar historial.</td></tr>`;
  }
}

function closeHistoryModal() {
  document.getElementById('modal-history').classList.add('hidden');
  currentInspectorDNI = null;
}


// ==================== HISTORIAL GLOBAL (Sesiones) ====================
let cachedHistory = [];
let displayedHistory = [];
let currentHistPage = 1;
let histRowsPerPage = 10;

async function cargarHistorialGlobal() {
  const tbody = document.querySelector('#tab-historial tbody');
  if (tbody) tbody.innerHTML = `<tr><td colspan="5" style="text-align:center; padding:30px; color:var(--text-muted);">Cargando historial global...</td></tr>`;

  try {
    const res = await fetch('/all_sessions');
    const data = await res.json();
    cachedHistory = Array.isArray(data) ? data : [];
    applyHistoryFilters();
  } catch (e) {
    console.error("Error loading history", e);
    if (tbody) tbody.innerHTML = `<tr><td colspan="5" style="text-align:center; color:var(--error);">Error de conexión.</td></tr>`;
  }
}

function applyHistoryFilters() {
  const termEl = document.getElementById('history-search');
  const term = (termEl ? termEl.value : '').toLowerCase();

  displayedHistory = (cachedHistory || []).filter(h => {
    const dni = String(h.dni || '').toLowerCase();
    const nombre = String(h.nombre_usuario || h.nombreusuario || '').toLowerCase();
    const estado = String(h.estado || '').toLowerCase();
    return dni.includes(term) || nombre.includes(term) || estado.includes(term);
  });

  const totalPages = Math.ceil(displayedHistory.length / histRowsPerPage) || 1;
  if (currentHistPage > totalPages) currentHistPage = 1;
  if (currentHistPage < 1) currentHistPage = 1;

  renderHistoryTable();
}

function renderHistoryTable() {
  const tbody = document.querySelector('#tab-historial tbody');
  if (!tbody) return;

  const start = (currentHistPage - 1) * histRowsPerPage;
  const end = start + histRowsPerPage;
  const pageItems = displayedHistory.slice(start, end);

  if (pageItems.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align:center; padding:30px; color:var(--text-muted);">No hay sesiones registradas.</td></tr>`;
    return;
  }

  tbody.innerHTML = pageItems.map(h => {
    const dateObj = new Date(h.fecha);
    const date = isNaN(dateObj) ? 'Desconocida' : dateObj.toLocaleString();

    let statusColor = 'var(--text-main)';
    const st = String(h.estado || '').toLowerCase();
    if (st === 'completed') statusColor = 'var(--success)';
    else if (st === 'error' || st === 'failed') statusColor = 'var(--error)';

    const nombre = h.nombre_usuario || h.nombreusuario || 'Desconocido';

    return `
      <tr>
        <td style="font-size:0.85rem;">${date}</td>
        <td>${_numOr(h.duracion, 0).toFixed(1)}s</td>
        <td>
          <div style="font-weight:600;">${nombre}</div>
          <div style="font-size:0.75rem; color:var(--text-muted);">${h.dni || '-'}</div>
        </td>
        <td><span style="color:${statusColor}; font-weight:500; text-transform:uppercase; font-size:0.8rem;">${h.estado || ''}</span></td>
        <td>
          <button class="btn-icon" title="Ver Detalles de Sesión" onclick="openConversationDetail('${h.conversation_id || h.conversationid}', '${h.fecha || ''}')">
            <span class="material-icons-outlined" style="font-size:18px;">description</span>
          </button>
        </td>
      </tr>
    `;
  }).join('');
}

function changeHistPage(delta) {
  currentHistPage += delta;
  applyHistoryFilters();
}


// ==================== MODAL CONVERSACIÓN ====================
function openModal(id) {
  document.getElementById(id).classList.remove('hidden');
}
function closeModal(id) {
  document.getElementById(id).classList.add('hidden');
}

async function openConversationDetail(convId, dateStr) {
  openModal('conversation-detail-modal');

  const dateEl = document.getElementById('conv-modal-date');
  if (dateEl) dateEl.innerText = new Date(dateStr).toLocaleString();

  const container = document.getElementById('transcript-container');
  const player = document.getElementById('conv-audio-player');
  if (player) {
    player.src = `/history/audio/${convId}`;
    player.parentElement?.classList.remove('hidden');
  }

  if (container) {
    container.innerHTML = `
      <div style="text-align:center; margin-top:40px; color:var(--primary);">
        <span class="material-icons-outlined" style="animation:spin 1s linear infinite;">autorenew</span>
        <p style="margin-top:8px; color:var(--text-muted);">Cargando transcripción...</p>
      </div>
    `;
  }

  try {
    const res = await fetch(`/history/details/${convId}`);
    if (!res.ok) throw new Error('Error fetching details');
    const data = await res.json();

    if (container) container.innerHTML = '';

    if (data.transcription && data.transcription.length > 0) {
      data.transcription.forEach(msg => {
        const isUser = msg.role === 'user';
        let text = msg.text || msg.message;

        let bubbleClass = isUser ? 'chat-user' : 'chat-agent';
        if (!text) {
          if (msg.type === 'tool_use' || msg.toolCalls) {
            text = '[Herramienta Interna Ejecutada]';
            bubbleClass = 'chat-tool';
          } else return;
        }

        const bubble = `
          <div style="display:flex; flex-direction:column; align-items:${isUser ? 'flex-end' : 'flex-start'}; margin-bottom:12px;">
            <span style="font-size:0.65rem; color:var(--text-muted); margin-bottom:4px; margin-${isUser ? 'right' : 'left'}:4px;">
              ${isUser ? 'Usuario' : 'Agente'}
            </span>
            <div class="chat-bubble ${bubbleClass}">${text}</div>
          </div>
        `;
        container.innerHTML += bubble;
      });
    } else {
      if (container) container.innerHTML = `<p style="text-align:center; color:var(--text-muted); margin-top:40px;">No hay transcripción disponible.</p>`;
    }

  } catch (e) {
    if (container) container.innerHTML = `<p style="text-align:center; color:var(--error); margin-top:40px;">Error cargando detalles.</p>`;
  }
}

async function syncHistory() {
  const btn = event.currentTarget;
  const originalHTML = btn.innerHTML;
  btn.innerHTML = `<span class="material-icons-outlined" style="font-size:16px; animation:spin 1s linear infinite;">autorenew</span> Sync...`;
  btn.disabled = true;

  try {
    const res = await fetch('/history/sync', { method: 'POST' });
    const data = await res.json();

    if (data.status === 'ok') {
      alert(`Sincronización completada. ${data.new_sessions} nuevas sesiones.`);
      if (currentInspectorDNI) openHistoryModal(currentInspectorDNI);
    } else {
      alert('Error en sincronización: ' + data.msg);
    }
  } catch (e) {
    alert('Error de conexión');
  } finally {
    btn.innerHTML = originalHTML;
    btn.disabled = false;
  }
}


// ==================== INSPECTOR ====================
async function openInspector(dni) {
  if (isProcessingInsp) return;

  currentInspectorDNI = dni;
  document.getElementById('inspector-modal').classList.remove('hidden');

  document.getElementById('inspector-loading').classList.remove('hidden');
  document.getElementById('inspector-data').classList.add('hidden');

  const resDiv = document.getElementById('verify-result');
  if (resDiv) resDiv.innerHTML = '';

  const btnVerify = document.getElementById('btn-verify-insp');
  if (btnVerify) btnVerify.disabled = true;

  wavBlob = null;

  try {
    const res = await fetch(`/user_details/${dni}`);
    const u = await res.json();

    document.getElementById('insp-nombre').innerText = u.nombre || '';
    document.getElementById('insp-dni').innerText = u.dni || '';
    document.getElementById('insp-score').innerText = (_safeScore(u.auto_check || u.autocheck) * 100).toFixed(1);
    document.getElementById('insp-muestras').innerText = u.muestras || '-';
    document.getElementById('insp-fecha').innerText = u.created_at ? new Date(u.created_at).toLocaleDateString() : '-';

    const audioEl = document.getElementById('insp-audio');
    if (audioEl && u.ref_audio_url) audioEl.src = u.ref_audio_url;

    document.getElementById('inspector-loading').classList.add('hidden');
    document.getElementById('inspector-data').classList.remove('hidden');
  } catch (e) {
    console.error('openInspector error', e);
  }
}

function closeInspector() {
  if (isProcessingInsp && !confirm('Cerrar?')) return;

  document.getElementById('inspector-modal').classList.add('hidden');

  try { document.getElementById('insp-audio').pause(); } catch (_) {}

  if (audioContext) { try { audioContext.close(); } catch (_) {} audioContext = null; }
  if (mediaStream) { try { mediaStream.getTracks().forEach(t => t.stop()); } catch (_) {} mediaStream = null; }

  isProcessingInsp = false;
  const btnRecord = document.getElementById('btn-record');
  if (btnRecord) btnRecord.disabled = false;
}

async function toggleRecording() {
  const btn = document.getElementById('btn-record');

  if (!audioContext) {
    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });

      const source = audioContext.createMediaStreamSource(mediaStream);
      recorderNode = audioContext.createScriptProcessor(4096, 1, 1);

      let leftChannel = [];
      recorderNode.onaudioprocess = (e) => {
        leftChannel.push(new Float32Array(e.inputBuffer.getChannelData(0)));
      };

      source.connect(recorderNode);
      recorderNode.connect(audioContext.destination);
      window.currentRecording = { leftChannel };

      if (btn) {
        btn.innerHTML = `<span class="material-icons-outlined" style="vertical-align:middle;">stop_circle</span>`;
        btn.style.color = 'var(--error)';
      }

      document.getElementById('record-status').classList.remove('hidden');
      document.getElementById('btn-verify-insp').disabled = true;

    } catch (e) {
      alert('Micro error: ' + (e && e.message ? e.message : e));
    }

  } else {
    try { recorderNode.disconnect(); } catch (_) {}
    try { mediaStream.getTracks().forEach(t => t.stop()); } catch (_) {}
    mediaStream = null;

    const leftChannel = window.currentRecording.leftChannel || [];
    const len = leftChannel.reduce((a, c) => a + c.length, 0);
    const buf = new Float32Array(len);
    let off = 0;
    for (let c of leftChannel) { buf.set(c, off); off += c.length; }

    wavBlob = encodeWAV(buf, 16000);

    try { await audioContext.close(); } catch (_) {}
    audioContext = null;

    if (btn) {
      btn.innerHTML = `<span class="material-icons-outlined" style="vertical-align:middle;">mic</span> Grabar`;
      btn.style.color = '';
    }

    document.getElementById('record-status').classList.add('hidden');
    document.getElementById('btn-verify-insp').disabled = false;
  }
}

async function verifyInspector() {
  if (!wavBlob) return;

  isProcessingInsp = true;

  const resDiv = document.getElementById('verify-result');
  const btnRecord = document.getElementById('btn-record');
  const btnVerify = document.getElementById('btn-verify-insp');

  if (btnRecord) btnRecord.disabled = true;
  if (btnVerify) btnVerify.disabled = true;

  if (resDiv) resDiv.innerHTML = `<span style="color:var(--primary);">Procesando (El servidor VAD evaluará tu audio local)...</span>`;

  try {
    const formData = new FormData();
    formData.append('dnireclamado', currentInspectorDNI);
    formData.append('file', wavBlob, 'rec.wav');

    const res = await fetch('/verify_file', { method: 'POST', body: formData });
    const data = await res.json();

    setTimeout(cargarCRM, 1000);

    const _score = (typeof data.score === 'number') ? data.score : parseFloat(data.score || '0');
    const _scorePct = (isFinite(_score) ? _score * 100 : 0).toFixed(1);
    const _estado = (data.estado || '').toUpperCase();
    let color, bg, icon;
    if (_estado === 'GENUINO') {
      color = '#1a5c34'; bg = '#d4edda'; icon = 'check_circle';
    } else if (_estado === 'DEEPFAKE') {
      color = '#7a1020'; bg = '#fde8ec'; icon = 'warning';
    } else if (_estado === 'AUDIO_INSUFICIENTE' || _estado === 'AUDIOINSUFICIENTE') {
      color = '#6b4700'; bg = '#fff3cd'; icon = 'mic_off';
    } else {
      color = '#7a1020'; bg = '#fde8ec'; icon = 'cancel';
    }

    if (resDiv) {
      resDiv.innerHTML = `
        <div style="margin-top:10px; padding:12px; background:${bg}; border-radius:8px; color:${color};">
          <div style="display:flex; align-items:center; gap:8px;">
            <span class="material-icons-outlined">${icon}</span>
            <strong>${data.estado}</strong>
          </div>
          <div style="font-size:0.85rem; margin-top:4px;">Score: ${_scorePct}%</div>
          <div style="font-size:0.8rem; margin-top:4px; color:var(--text-muted);">${data.mensaje || ''}</div>
        </div>
      `;
    }

  } catch (e) {
    if (resDiv) resDiv.innerHTML = `Error`;
  } finally {
    isProcessingInsp = false;
    if (btnRecord) btnRecord.disabled = false;
    if (btnVerify) btnVerify.disabled = false;
  }
}


// ==================== CONFIGURACIÓN ====================
// NOTA: backend actual solo persiste: umbralidentidad, activebiometrics, activedeepfake, deepfakemodel, spybuffersecs.
// Para el resto (bypass + params por modelo).

const _CFG_EXTRA_LS_KEY = 'voicebio.config.extra.v1';

function _getEl(id) { return document.getElementById(id); }

function _readRadio(name, fallback) {
  const el = document.querySelector(`input[name="${name}"]:checked`);
  return el ? el.value : fallback;
}
function _setRadio(name, value) {
  const el = document.querySelector(`input[name="${name}"][value="${value}"]`);
  if (el) el.checked = true;
}
function _readNum(id, fallback) {
  const el = _getEl(id);
  if (!el) return fallback;
  const n = Number(el.value);
  return Number.isFinite(n) ? n : fallback;
}
function _readBool(id, fallback) {
  const el = _getEl(id);
  if (!el) return fallback;
  return !!el.checked;
}

function _loadExtraCfgLS() {
  try {
    const raw = localStorage.getItem(_CFG_EXTRA_LS_KEY);
    if (!raw) return {};
    const obj = JSON.parse(raw);
    return (obj && typeof obj === 'object') ? obj : {};
  } catch (_) {
    return {};
  }
}
function _saveExtraCfgLS(extra) {
  try {
    localStorage.setItem(_CFG_EXTRA_LS_KEY, JSON.stringify(extra || {}));
  } catch (_) {}
}

function updateDeepfakeAdvancedUIState() {
  const deepOn = !!_getEl('cfg-deepfake')?.checked;
  const model = _readRadio('cfg-deepfake-model', '500m');

  const wrapModel = _getEl('cfg-deepfake-model-wrap');
  if (wrapModel) wrapModel.style.opacity = deepOn ? '1' : '0.55';

  const r500 = _getEl('cfg-deepfake-model-500m');
  const r1b = _getEl('cfg-deepfake-model-1b');
  if (r500) r500.disabled = !deepOn;
  if (r1b) r1b.disabled = !deepOn;

  const bypass = _getEl('cfg-deepfake-bypass');
  if (bypass) bypass.disabled = !deepOn;

  const paramsWrap = _getEl('cfg-deepfake-params-wrap');
  if (paramsWrap) {
    paramsWrap.querySelectorAll('input').forEach(inp => {
      // No deshabilitar el propio deepfake checkbox
      if (inp.id === 'cfg-deepfake') return;
      inp.disabled = !deepOn;
    });
  }

  // Mostrar params del modelo activo
  const s500 = _getEl('df-params-500m');
  const s1b = _getEl('df-params-1b');
  if (s500) s500.style.display = (model === '500m') ? 'block' : 'none';
  if (s1b) s1b.style.display = (model === '1b') ? 'block' : 'none';
}

async function loadConfig() {
  try {
    const res = await fetch("/config");
    const cfg = await res.json();

    // backend
    const umbral = _numOr(_firstDefined(cfg.umbralidentidad, cfg.umbral_identidad, cfg.umbralIdentidad), 0.5);
    const biom = _boolOr(_firstDefined(cfg.activebiometrics, cfg.active_biometrics, cfg.activeBiometrics), true);
    const deep = _boolOr(_firstDefined(cfg.activedeepfake, cfg.active_deepfake, cfg.activeDeepfake), false);
    const buff = _numOr(_firstDefined(cfg.spybuffersecs, cfg.spy_buffer_secs, cfg.spyBufferSecs), 5);
    const model = String(_firstDefined(cfg.deepfakemodel, cfg.deepfake_model, cfg.deepfakeModel) || '500m').trim().toLowerCase();

    // extras LS (fallback)
    const extra = _loadExtraCfgLS();

    const dfBypass = _boolOr(_firstDefined(cfg.deepfakebypass, cfg.deepfake_bypass, extra.deepfake_bypass), true);

    const df500_thr = _numOr(_firstDefined(extra.df500m_threshold), 0.50);
    const df500_minsecs = _numOr(_firstDefined(extra.df500m_minsecs), 1.20);
    const df500_minrms = _numOr(_firstDefined(extra.df500m_minrms), 0.006);
    const df500_maxabs = _numOr(_firstDefined(extra.df500m_maxabs), 0.999);

    const df1b_thr = _numOr(_firstDefined(extra.df1b_threshold), 0.50);
    const df1b_minsecs = _numOr(_firstDefined(extra.df1b_minsecs), 1.20);
    const df1b_minrms = _numOr(_firstDefined(extra.df1b_minrms), 0.006);
    const df1b_maxabs = _numOr(_firstDefined(extra.df1b_maxabs), 0.999);

    // Pintar UI
    const elUmbral = _getEl("cfg-umbral");
    const lblUmbral = _getEl("lbl-umbral");
    const elBiom = _getEl("cfg-biometrics");
    const elDeep = _getEl("cfg-deepfake");
    const elBuff = _getEl("cfg-buffer");

    if (elUmbral) elUmbral.value = String(umbral);
    if (lblUmbral) lblUmbral.innerText = String(umbral);
    if (elBiom) elBiom.checked = !!biom;
    if (elDeep) elDeep.checked = !!deep;
    if (elBuff) elBuff.value = String(buff);

    _setRadio('cfg-deepfake-model', (model === '1b') ? '1b' : '500m');

    if (_getEl('cfg-deepfake-bypass')) _getEl('cfg-deepfake-bypass').checked = !!dfBypass;

    // Params 500m
    if (_getEl('cfg-df500m-threshold')) _getEl('cfg-df500m-threshold').value = String(df500_thr);
    if (_getEl('cfg-df500m-minsecs')) _getEl('cfg-df500m-minsecs').value = String(df500_minsecs);
    if (_getEl('cfg-df500m-minrms')) _getEl('cfg-df500m-minrms').value = String(df500_minrms);
    if (_getEl('cfg-df500m-maxabs')) _getEl('cfg-df500m-maxabs').value = String(df500_maxabs);

    // Params 1b
    if (_getEl('cfg-df1b-threshold')) _getEl('cfg-df1b-threshold').value = String(df1b_thr);
    if (_getEl('cfg-df1b-minsecs')) _getEl('cfg-df1b-minsecs').value = String(df1b_minsecs);
    if (_getEl('cfg-df1b-minrms')) _getEl('cfg-df1b-minrms').value = String(df1b_minrms);
    if (_getEl('cfg-df1b-maxabs')) _getEl('cfg-df1b-maxabs').value = String(df1b_maxabs);

    updateDeepfakeAdvancedUIState();

  } catch (e) {
    console.error("Error loading config", e);
  }
}

async function saveConfig(e) {
  e.preventDefault();

  const umbral = _readNum("cfg-umbral", 0.5);
  const activeBiom = _readBool("cfg-biometrics", true);
  const activeDeep = _readBool("cfg-deepfake", false);
  const buff = parseInt(String(_readNum("cfg-buffer", 5)), 10);

  const deepfakeModel = _readRadio('cfg-deepfake-model', '500m');
  const deepfakeBypass = _readBool('cfg-deepfake-bypass', true);

  // params (extras)
  const extra = {
    deepfake_bypass: deepfakeBypass,

    df500m_threshold: _readNum('cfg-df500m-threshold', 0.50),
    df500m_minsecs: _readNum('cfg-df500m-minsecs', 1.20),
    df500m_minrms: _readNum('cfg-df500m-minrms', 0.006),
    df500m_maxabs: _readNum('cfg-df500m-maxabs', 0.999),

    df1b_threshold: _readNum('cfg-df1b-threshold', 0.50),
    df1b_minsecs: _readNum('cfg-df1b-minsecs', 1.20),
    df1b_minrms: _readNum('cfg-df1b-minrms', 0.006),
    df1b_maxabs: _readNum('cfg-df1b-maxabs', 0.999),
  };

  // Persistencias extras SIEMPRE en el navegador (fallback)
  _saveExtraCfgLS(extra);

  // Payload para backend
  const payload = {
    // legacy que main.py normaliza
    umbralidentidad: umbral,
    activebiometrics: activeBiom,
    activedeepfake: activeDeep,
    deepfakemodel: deepfakeModel,
    spybuffersecs: buff,

    // snake (por si backend evoluciona)
    umbral_identidad: umbral,
    active_biometrics: activeBiom,
    active_deepfake: activeDeep,
    deepfake_model: deepfakeModel,
    spy_buffer_secs: buff,

    // futuros (no rompe aunque backend ignore)
    deepfake_bypass: deepfakeBypass,
    deepfakebypass: deepfakeBypass,
    ...extra,
  };

  const btn = document.querySelector('#form-config button[type="submit"]');
  const originalHTML = btn ? btn.innerHTML : null;
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span class="material-icons-outlined">autorenew</span> Guardando...';
  }

  try {
    const res = await fetch("/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    if (res.ok) {
      alert("Configuración guardada correctamente.");
      try { await loadConfig(); } catch {}
    } else {
      let detail = "";
      try {
        const j = await res.json();
        detail = j?.detail || j?.mensaje || j?.msg || "";
      } catch {}
      alert("Error al guardar." + (detail ? "\n" + detail : ""));
    }
  } catch (e2) {
    console.error("saveConfig error", e2);
    alert("Error de conexión.");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = originalHTML;
    }
  }
}


// ==================== VISUAL PIPELINE ====================
function updateVisualPipeline(data) {
  if (!data) return;
  const nodes = ['input', 'tts', 'spoof', 'ai', 'db'];

  nodes.forEach(n => {
    const node = document.getElementById(`node-${n}`);
    const badge = document.getElementById(`badge-${n}`);
    const line = document.getElementById(`line-${nodes.indexOf(n) + 1}`);

    if (node) {
      node.classList.remove('active', 'success', 'error');
      if (data[n]) {
        node.classList.add(data[n].status || 'active');
        if (badge) badge.innerText = data[n].time ? (data[n].time + 'ms') : (data[n].status === 'done' ? 'OK' : '...');
      }
    }

    if (line && data[n]) line.classList.add('active');
  });
}


// ============================================================================
// NUEVO CLIENT TOOL DE ELEVENLABS (Orquestador de Eventos)
// ============================================================================
const ELEVEN_CLIENT_TOOL_NAMES = ['checkvoiceidentityclient', 'check_voice_identity_client'];
let __elevenClientToolRegistered = false;

function registerElevenLabsClientTool() {
  if (__elevenClientToolRegistered) return;

  const attach = () => {
    const widget = document.querySelector('elevenlabs-convai');
    if (!widget) return false;

    widget.addEventListener('elevenlabs-convai:call', (event) => {
      try {
        if (!event.detail || !event.detail.config) return;

        const toolHandler = async (params) => {
          const traceId = _makeTraceId();
          const raw = (
            params?.dnireclamado ?? 
            params?.dni_reclamado ?? 
            params?.dniReclamado ?? 
            params?.dni ?? 
            ""
          );
          const dni = String(raw).replace(/\D/g, "");

          console.log('[VOICEBIO] Tool disparado por el Agente. Cediendo control al VAD del Backend...', { traceId, dni });

          if (!dni) {
            return { 
              estado: 'ERROR', 
              mensaje: 'Falta dnireclamado (solo números).', 
              score: 0.0, 
              trace_id: traceId 
            };
          }

          const controller = new AbortController();
          const timeoutMs = 45000;
          const t = setTimeout(() => controller.abort(), timeoutMs);

          try {
            const res = await fetch('/verify', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                dnireclamado: dni,
                dni_reclamado: dni,
                dni: dni,
                traceid: traceId,
                trace_id: traceId,
              }),
              signal: controller.signal,
            });

            let data = null;
            try { 
              data = await res.json(); 
            } catch { 
              data = null; 
            }

            if (!res.ok) {
              const msg = (data && (data.detail || data.mensaje)) 
                ? data.detail || data.mensaje 
                : `HTTP ${res.status} en /verify`;
              console.warn('[VOICEBIO] Error devuelto por Python', { traceId, status: res.status, msg });
              return { 
                estado: 'ERROR', 
                mensaje: msg, 
                score: 0.0, 
                trace_id: traceId 
              };
            }

            try { updateMonitor(); } catch {}

            if (data && typeof data === 'object' && !Array.isArray(data)) {
              data.trace_id = traceId;
            }

            console.log('[VOICEBIO] /verify completado con éxito', { traceId, data });
            return data;

          } catch (e) {
            const msg = (e && e.name === 'AbortError')
              ? `Timeout de ${timeoutMs}ms. El usuario no habló o la verificación tardó mucho.`
              : (e.message || String(e));
            console.warn('[VOICEBIO] Excepción de conexión', { traceId, msg });
            return { 
              estado: 'ERROR', 
              mensaje: msg, 
              score: 0.0, 
              trace_id: traceId 
            };
          } finally {
            clearTimeout(t);
          }
        };

        // Registra AMBOS nombres de tools
        event.detail.config.clientTools = Object.fromEntries(
          ELEVEN_CLIENT_TOOL_NAMES.map(name => [name, toolHandler])
        );

        console.log(`[ElevenLabs] clientTools listos: ${ELEVEN_CLIENT_TOOL_NAMES.join(', ')}`);
      } catch (e) {
        console.error('[ElevenLabs] Error configurando clientTools', e);
      }
    });

    return true;
  };

  if (attach()) {
    __elevenClientToolRegistered = true;
    return;
  }

  const start = Date.now();
  const maxMs = 15000;

  const obs = new MutationObserver(() => {
    if (attach()) {
      __elevenClientToolRegistered = true;
      obs.disconnect();
    } else if (Date.now() - start > maxMs) {
      obs.disconnect();
      console.warn('[ElevenLabs] No se pudo registrar el clientTool (widget no apareció a tiempo).');
    }
  });

  obs.observe(document.documentElement, { childList: true, subtree: true });
  __elevenClientToolRegistered = true;
}



// ==================== WAV ENCODER (Usado para grabar en el Inspector localmente) ====================
function encodeWAV(samples, sampleRate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);

  const writeString = (view, offset, string) => {
    for (let i = 0; i < string.length; i++) view.setUint8(offset + i, string.charCodeAt(i));
  };

  writeString(view, 0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  writeString(view, 8, 'WAVE');
  writeString(view, 12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(view, 36, 'data');
  view.setUint32(40, samples.length * 2, true);

  let offset = 44;
  for (let i = 0; i < samples.length; i++) {
    let s = Math.max(-1, Math.min(1, samples[i]));
    s = s < 0 ? s * 0x8000 : s * 0x7FFF;
    view.setInt16(offset, s, true);
    offset += 2;
  }

  return new Blob([view], { type: 'audio/wav' });
}


// ==================== INIT ====================
registerElevenLabsClientTool();

loadConfig();

setInterval(updateMonitor, 250);
setInterval(checkStatus, 5000);

updateMonitor();
checkStatus();

showTab('tab-dashboard');

// Keep Deepfake UI in sync (incluye bypass/params)
(function(){
  try {
    const deep = _getEl("cfg-deepfake");
    if (deep) deep.addEventListener("change", updateDeepfakeAdvancedUIState);

    document.querySelectorAll('input[name="cfg-deepfake-model"]').forEach(r => {
      r.addEventListener("change", updateDeepfakeAdvancedUIState);
    });

    const bypass = _getEl("cfg-deepfake-bypass");
    if (bypass) bypass.addEventListener("change", () => {
      // solo UI (persistencia en saveConfig)
    });

    updateDeepfakeAdvancedUIState();
  } catch(e) {}
})();
