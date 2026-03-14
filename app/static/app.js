const API = '/api/v1/meetings';
let pollingIntervals = {};

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
        alert('Неподдерживаемый формат. Допустимые: ' + allowed.join(', '));
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
    return (bytes / 1048576).toFixed(1) + ' MB';
}

// --- Upload ---
uploadBtn.addEventListener('click', async () => {
    if (!selectedFile) return;

    uploadBtn.disabled = true;
    uploadBtn.textContent = 'Загрузка...';

    const form = new FormData();
    form.append('file', selectedFile);

    try {
        const resp = await fetch(`${API}/transcribe?language=${langSelect.value}`, {
            method: 'POST',
            body: form,
        });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || 'Ошибка загрузки');
        }

        const data = await resp.json();
        clearFile();
        addJobToList(data);
        startPolling(data.job_id);
    } catch (e) {
        alert('Ошибка: ' + e.message);
    } finally {
        uploadBtn.disabled = false;
        uploadBtn.textContent = 'Начать обработку';
    }
});

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
    // Remove empty state
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

    let html = `
        <div class="job-header">
            <span class="job-id">${shortId}...</span>
            <span class="status-badge status-${job.status}">${statusLabel}</span>
        </div>
        <div class="progress-bar ${isTerminal(job.status) ? '' : 'active'}">
            <div class="progress-fill" style="width: ${progress}%"></div>
        </div>
    `;

    if (job.status === 'completed') {
        html += '<div class="job-results">';
        if (job.summary_pdf_url) html += buildResultLink(job.summary_pdf_url, 'PDF-отчёт', 'pdf');
        if (job.summary_txt_url) html += buildResultLink(job.summary_txt_url, 'Саммари', 'txt');
        if (job.tasks_url) html += buildResultLink(job.tasks_url, 'Задачи', 'txt');
        if (job.transcript_url) html += buildResultLink(job.transcript_url, 'Расшифровка', 'txt');
        html += '</div>';
    }

    if (job.status === 'failed' && job.error) {
        html += `<div class="job-error">${escapeHtml(job.error)}</div>`;
    }

    return html;
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
        transcribing: 'Распознавание',
        analyzing: 'Анализ',
        generating_files: 'Генерация файлов',
        uploading: 'Загрузка в S3',
        completed: 'Готово',
        failed: 'Ошибка',
    };
    return map[status] || status;
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
