// Detect base path for reverse proxy support (e.g. /aiap/)
const BASE = window.location.pathname.replace(/\/+$/, '');
const API = `${BASE}/api/v1/meetings`;
let pollingIntervals = {};
let openLogs = {};

// --- DOM refs ---
const dropZone = document.getElementById('dropZone');
const fileInput = document.getElementById('fileInput');
const fileInfo = document.getElementById('fileInfo');
const fileName = document.getElementById('fileName');
const fileSize = document.getElementById('fileSize');
const removeBtn = document.getElementById('removeFile');
const uploadBtn = document.getElementById('uploadBtn');
const langSelect = document.getElementById('langSelect');
const jobsList = document.getElementById('jobsList');
const modal = document.getElementById('modal');
const modalTitle = document.getElementById('modalTitle');
const modalBody = document.getElementById('modalBody');
const uploadProgress = document.getElementById('uploadProgress');
const uploadFill = document.getElementById('uploadFill');
const uploadPercent = document.getElementById('uploadPercent');
const uploadLabel = document.getElementById('uploadLabel');
const uploadSpeed = document.getElementById('uploadSpeed');
const statusCard = document.getElementById('statusCard');
const statusGrid = document.getElementById('statusGrid');
const statusOverall = document.getElementById('statusOverall');
const healthDot = document.getElementById('healthDot');

let selectedFile = null;

// --- Drop zone ---
dropZone.addEventListener('click', () => fileInput.click());

dropZone.addEventListener('dragover', e => {
    e.preventDefault();
    dropZone.classList.add('dragover');
});

dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));

dropZone.addEventListener('drop', e => {
    e.preventDefault();
    dropZone.classList.remove('dragover');
    if (e.dataTransfer.files.length) selectFile(e.dataTransfer.files[0]);
});

fileInput.addEventListener('change', () => {
    if (fileInput.files.length) selectFile(fileInput.files[0]);
});

removeBtn.addEventListener('click', clearFile);

function selectFile(file) {
    const allowed = ['.wav', '.mp3', '.ogg', '.flac', '.m4a', '.opus', '.webm'];
    const ext = '.' + file.name.split('.').pop().toLowerCase();
    if (!allowed.includes(ext)) {
        showNotification('Неподдерживаемый формат. Допустимые: ' + allowed.join(', '), 'error');
        return;
    }
    selectedFile = file;
    fileName.textContent = file.name;
    fileSize.textContent = formatSize(file.size);
    fileInfo.classList.add('visible');
    uploadBtn.disabled = false;
}

function clearFile() {
    selectedFile = null;
    fileInput.value = '';
    fileInfo.classList.remove('visible');
    uploadBtn.disabled = true;
}

function formatSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    if (bytes < 1073741824) return (bytes / 1048576).toFixed(1) + ' MB';
    return (bytes / 1073741824).toFixed(2) + ' GB';
}

// --- Upload with XHR progress ---
uploadBtn.addEventListener('click', () => {
    if (!selectedFile) return;

    uploadBtn.disabled = true;
    uploadBtn.textContent = 'Загрузка...';
    uploadProgress.style.display = 'block';
    uploadFill.style.width = '0%';
    uploadPercent.textContent = '0%';
    uploadLabel.textContent = 'Загрузка файла на сервер...';
    uploadSpeed.textContent = '';

    const form = new FormData();
    form.append('file', selectedFile);

    const xhr = new XMLHttpRequest();
    const startTime = Date.now();

    xhr.upload.addEventListener('progress', e => {
        if (e.lengthComputable) {
            const pct = Math.round((e.loaded / e.total) * 100);
            uploadFill.style.width = pct + '%';
            uploadPercent.textContent = pct + '%';
            uploadLabel.textContent = `Загрузка файла... ${formatSize(e.loaded)} / ${formatSize(e.total)}`;

            const elapsed = (Date.now() - startTime) / 1000;
            if (elapsed > 0.5) {
                const speed = e.loaded / elapsed;
                const remaining = (e.total - e.loaded) / speed;
                uploadSpeed.textContent = `${formatSize(speed)}/s — осталось ~${Math.ceil(remaining)}с`;
            }
        }
    });

    xhr.addEventListener('load', () => {
        if (xhr.status >= 200 && xhr.status < 300) {
            const data = JSON.parse(xhr.responseText);
            uploadLabel.textContent = 'Файл загружен, обработка начата';
            uploadFill.style.width = '100%';
            uploadPercent.textContent = '100%';
            uploadSpeed.textContent = '';

            setTimeout(() => {
                uploadProgress.style.display = 'none';
            }, 2000);

            clearFile();
            addJobToList(data);
            startPolling(data.job_id);
        } else {
            let msg = 'Ошибка загрузки';
            try {
                const err = JSON.parse(xhr.responseText);
                msg = err.detail || msg;
            } catch (_) {}
            showNotification(msg, 'error');
            uploadProgress.style.display = 'none';
        }
        uploadBtn.disabled = false;
        uploadBtn.textContent = 'Начать обработку';
    });

    xhr.addEventListener('error', () => {
        showNotification('Сетевая ошибка при загрузке файла', 'error');
        uploadProgress.style.display = 'none';
        uploadBtn.disabled = false;
        uploadBtn.textContent = 'Начать обработку';
    });

    xhr.open('POST', `${API}/transcribe?language=${langSelect.value}`);
    xhr.send(form);
});

// --- Notification toast ---
function showNotification(message, type = 'info') {
    const existing = document.querySelector('.notification');
    if (existing) existing.remove();

    const el = document.createElement('div');
    el.className = `notification notification-${type}`;
    el.innerHTML = `
        <span class="notification-text">${escapeHtml(message)}</span>
        <button class="notification-close" onclick="this.parentElement.remove()">&times;</button>
    `;
    document.body.appendChild(el);

    setTimeout(() => el.classList.add('visible'), 10);
    setTimeout(() => {
        el.classList.remove('visible');
        setTimeout(() => el.remove(), 300);
    }, 6000);
}

// --- Health check ---
function toggleStatus() {
    statusCard.style.display = statusCard.style.display === 'none' ? 'block' : 'none';
}

async function checkHealth() {
    statusCard.style.display = 'block';
    healthDot.className = 'health-dot checking';

    statusGrid.innerHTML = `
        <div class="status-item checking"><div class="status-item-header"><span class="status-icon">&#9679;</span> S3 Storage<span class="status-checking">проверка...</span></div></div>
        <div class="status-item checking"><div class="status-item-header"><span class="status-icon">&#9679;</span> Yandex STT<span class="status-checking">проверка...</span></div></div>
        <div class="status-item checking"><div class="status-item-header"><span class="status-icon">&#9679;</span> Claude AI<span class="status-checking">проверка...</span></div></div>
    `;
    statusOverall.textContent = 'Проверка...';
    statusOverall.className = 'status-overall checking';

    try {
        const resp = await fetch(`${BASE}/health/details`);
        const data = await resp.json();

        statusGrid.innerHTML = '';
        data.services.forEach(svc => {
            statusGrid.innerHTML += buildServiceCard(svc);
        });

        statusOverall.textContent = data.status === 'ok' ? 'Все службы работают' : 'Есть проблемы';
        statusOverall.className = `status-overall ${data.status === 'ok' ? 'ok' : 'error'}`;
        healthDot.className = `health-dot ${data.status === 'ok' ? 'ok' : 'error'}`;
    } catch (e) {
        statusGrid.innerHTML = '<div class="status-item error"><div class="status-item-header"><span class="status-icon">&#9679;</span> Не удалось проверить статус</div><div class="status-message">' + escapeHtml(e.message) + '</div></div>';
        statusOverall.textContent = 'Ошибка';
        statusOverall.className = 'status-overall error';
        healthDot.className = 'health-dot error';
    }
}

function buildServiceCard(svc) {
    const icons = {
        'S3 Storage': '&#128451;',
        'Yandex STT': '&#127908;',
        'Claude AI': '&#129302;',
    };
    const icon = icons[svc.name] || '&#9881;';
    const latency = svc.latency_ms != null ? `<span class="status-latency">${svc.latency_ms}ms</span>` : '';

    return `
        <div class="status-item ${svc.status}">
            <div class="status-item-header">
                <span class="status-icon status-icon-${svc.status}">&#9679;</span>
                <span class="status-name">${icon} ${escapeHtml(svc.name)}</span>
                ${latency}
            </div>
            <div class="status-message">${escapeHtml(svc.message)}</div>
        </div>
    `;
}

// --- Jobs ---
async function loadJobs() {
    try {
        const resp = await fetch(`${API}/jobs`);
        const jobs = await resp.json();

        if (!jobs.length) {
            jobsList.innerHTML = '<div class="jobs-empty">Нет задач. Загрузите аудиофайл для начала.</div>';
            return;
        }

        jobsList.innerHTML = '';
        jobs.reverse().forEach(job => {
            renderJob(job);
            if (!isTerminal(job.status)) startPolling(job.job_id);
        });
    } catch (e) {
        jobsList.innerHTML = '<div class="jobs-empty">Не удалось загрузить задачи</div>';
    }
}

function addJobToList(job) {
    const empty = jobsList.querySelector('.jobs-empty');
    if (empty) empty.remove();
    renderJob(job, true);
}

function renderJob(job, prepend = false) {
    const el = document.createElement('div');
    el.className = 'job-item';
    el.id = `job-${job.job_id}`;
    el.innerHTML = buildJobHTML(job);

    if (prepend) {
        jobsList.prepend(el);
    } else {
        jobsList.appendChild(el);
    }
}

function updateJob(job) {
    const el = document.getElementById(`job-${job.job_id}`);
    if (el) {
        el.innerHTML = buildJobHTML(job);
    } else {
        addJobToList(job);
    }
}

function buildJobHTML(job) {
    const progress = getProgress(job.status);
    const statusLabel = getStatusLabel(job.status);
    const shortId = job.job_id.substring(0, 8);
    const isActive = !isTerminal(job.status);

    let html = `
        <div class="job-header">
            <span class="job-id">${shortId}...</span>
            <span class="status-badge status-${job.status}">${statusLabel}</span>
        </div>
    `;

    // Progress bar with step indicator
    if (isActive) {
        html += `
            <div class="job-progress-section">
                <div class="progress-bar active">
                    <div class="progress-fill" style="width: ${progress}%"></div>
                </div>
                <div class="job-steps">
                    ${buildStepIndicators(job.status)}
                </div>
            </div>
        `;
    }

    if (job.status === 'completed') {
        html += '<div class="job-results">';
        if (job.summary_pdf_url) html += buildResultLink(job.summary_pdf_url, 'PDF-отчёт', 'pdf');
        if (job.summary_txt_url) html += buildResultLink(job.summary_txt_url, 'Саммари', 'txt');
        if (job.tasks_url) html += buildResultLink(job.tasks_url, 'Задачи', 'txt');
        if (job.transcript_url) html += buildResultLink(job.transcript_url, 'Расшифровка', 'txt');
        html += '</div>';
    }

    if (job.status === 'failed' && job.error) {
        html += `<div class="job-error">
            <strong>Ошибка:</strong> ${escapeHtml(job.error)}
        </div>`;
    }

    // Log panel
    if (job.logs && job.logs.length) {
        const logId = `log-${job.job_id}`;
        const isOpen = openLogs[job.job_id];
        html += `
            <div class="job-log">
                <div class="job-log-header" onclick="toggleLog('${job.job_id}')">
                    <span>Лог обработки (${job.logs.length})</span>
                    <span class="job-log-toggle ${isOpen ? 'open' : ''}">&#9660;</span>
                </div>
                <div class="job-log-body ${isOpen ? 'open' : ''}" id="${logId}">
                    <div class="job-log-entries">
                        ${job.logs.map(l => {
                            const cls = l.includes('ОШИБКА') ? 'error-entry' : '';
                            return `<div class="job-log-entry ${cls}">${escapeHtml(l)}</div>`;
                        }).join('')}
                    </div>
                </div>
            </div>
        `;
    }

    return html;
}

function buildStepIndicators(currentStatus) {
    const steps = [
        { key: 'pending', label: 'Очередь' },
        { key: 'transcribing', label: 'Распознавание' },
        { key: 'analyzing', label: 'Анализ' },
        { key: 'generating_files', label: 'Генерация' },
        { key: 'uploading', label: 'Загрузка' },
    ];
    const order = ['pending', 'transcribing', 'analyzing', 'generating_files', 'uploading', 'completed'];
    const currentIdx = order.indexOf(currentStatus);

    return steps.map((step, i) => {
        let cls = 'step-pending';
        if (i < currentIdx) cls = 'step-done';
        else if (i === currentIdx) cls = 'step-active';
        return `<span class="job-step ${cls}">${step.label}</span>`;
    }).join('');
}

function buildResultLink(url, label, type) {
    const icons = { pdf: '\u{1F4C4}', txt: '\u{1F4DD}' };
    const icon = icons[type] || '\u{1F4CE}';

    if (type === 'pdf') {
        return `<a href="${url}" target="_blank" class="result-link" download>
            <span class="result-icon">${icon}</span>${label}
        </a>`;
    }

    return `<a href="#" class="result-link" onclick="viewText('${url}', '${label}'); return false;">
        <span class="result-icon">${icon}</span>${label}
    </a>`;
}

// --- Polling ---
function startPolling(jobId) {
    if (pollingIntervals[jobId]) return;

    pollingIntervals[jobId] = setInterval(async () => {
        try {
            const resp = await fetch(`${API}/status/${jobId}`);
            const job = await resp.json();
            updateJob(job);

            if (isTerminal(job.status)) {
                clearInterval(pollingIntervals[jobId]);
                delete pollingIntervals[jobId];

                if (job.status === 'completed') {
                    showNotification('Обработка завершена!', 'success');
                } else if (job.status === 'failed') {
                    showNotification('Обработка завершилась с ошибкой', 'error');
                }
            }
        } catch (e) {
            // silently retry
        }
    }, 3000);
}

function isTerminal(status) {
    return status === 'completed' || status === 'failed';
}

function getProgress(status) {
    const map = { pending: 10, transcribing: 30, analyzing: 55, generating_files: 75, uploading: 90, completed: 100, failed: 100 };
    return map[status] || 0;
}

function getStatusLabel(status) {
    const map = {
        pending: 'Ожидание',
        transcribing: 'Распознавание речи',
        analyzing: 'Анализ (Claude AI)',
        generating_files: 'Генерация файлов',
        uploading: 'Загрузка в S3',
        completed: 'Готово',
        failed: 'Ошибка',
    };
    return map[status] || status;
}

// --- Log toggle ---
function toggleLog(jobId) {
    openLogs[jobId] = !openLogs[jobId];
    const el = document.getElementById(`log-${jobId}`);
    const toggle = el?.previousElementSibling?.querySelector('.job-log-toggle');
    if (el) el.classList.toggle('open');
    if (toggle) toggle.classList.toggle('open');
}

// --- Text viewer modal ---
async function viewText(url, title) {
    modalTitle.textContent = title;
    modalBody.textContent = 'Загрузка...';
    modal.classList.add('active');

    try {
        const resp = await fetch(url);
        if (!resp.ok) throw new Error('Не удалось загрузить файл');
        modalBody.textContent = await resp.text();
    } catch (e) {
        modalBody.textContent = 'Ошибка загрузки: ' + e.message;
    }
}

function closeModal() {
    modal.classList.remove('active');
}

modal.addEventListener('click', e => {
    if (e.target === modal) closeModal();
});

document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeModal();
});

function escapeHtml(text) {
    const d = document.createElement('div');
    d.textContent = text;
    return d.innerHTML;
}

// --- Init ---
loadJobs();
// Auto-check health on load
checkHealth();
