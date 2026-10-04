const $ = (selector) => document.querySelector(selector);
const fileInput = $('#fileInput');
const dropzone = $('#dropzone');
const stagedWrap = $('#stagedWrap');
const stagedFiles = $('#stagedFiles');
const startButton = $('#startButton');
const mergeFiles = $('#mergeFiles');
let staged = [];
let selectedPreviewId = null;
let activeStopId = null;
let lastState = { jobs: [], merged_outputs: {} };

const escapeHtml = (value) => { const div = document.createElement('div'); div.textContent = value; return div.innerHTML; };
const formatBytes = (bytes) => bytes < 1048576 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1048576).toFixed(1)} MB`;

function addFiles(files) {
  const known = new Set(staged.map((item) => `${item.file.name}:${item.file.size}`));
  [...files].forEach((file) => {
    const key = `${file.name}:${file.size}`;
    if (!known.has(key)) {
      staged.push({ id: crypto.randomUUID(), file, selected: true });
      known.add(key);
    }
  });
  renderStaged();
}

function renderStaged() {
  stagedWrap.classList.toggle('is-hidden', !staged.length);
  startButton.disabled = !staged.some((item) => item.selected);
  stagedFiles.innerHTML = staged.map((item, index) => `
    <div class="staged-file" draggable="true" data-id="${item.id}">
      <span class="drag">⠿</span><label><input type="checkbox" ${item.selected ? 'checked' : ''}><span class="custom-check"></span></label>
      <div class="file-icon">♫</div><div class="file-meta"><strong>${escapeHtml(item.file.name)}</strong><small>${formatBytes(item.file.size)} · Part ${index + 1}</small></div>
      <button class="remove" aria-label="Remove">×</button>
    </div>`).join('');
  stagedFiles.querySelectorAll('.staged-file').forEach((row) => {
    const id = row.dataset.id;
    row.querySelector('input').onchange = (event) => { staged.find((item) => item.id === id).selected = event.target.checked; renderStaged(); };
    row.querySelector('.remove').onclick = () => { staged = staged.filter((item) => item.id !== id); renderStaged(); };
    row.ondragstart = (e) => {
      e.dataTransfer.effectAllowed = 'move';
      setTimeout(() => row.classList.add('dragging'), 0);
    };
    row.ondragend = () => {
      row.classList.remove('dragging');
      const draggedId = row.dataset.id;
      const order = [...stagedFiles.querySelectorAll('.staged-file')].map((item) => item.dataset.id);
      staged.sort((left, right) => order.indexOf(left.id) - order.indexOf(right.id));
      renderStaged();
      const newRow = stagedFiles.querySelector(`[data-id="${draggedId}"]`);
      if (newRow) {
        newRow.classList.add('dropped');
        setTimeout(() => newRow.classList.remove('dropped'), 800);
      }
    };
  });
}

stagedFiles.ondragover = (event) => {
  event.preventDefault();
  const moving = stagedFiles.querySelector('.dragging');
  const target = event.target.closest('.staged-file');
  if (!moving || !target || moving === target) return;
  const box = target.getBoundingClientRect();
  target.parentNode.insertBefore(moving, event.clientY < box.top + box.height / 2 ? target : target.nextSibling);
};

fileInput.onchange = () => { addFiles(fileInput.files); fileInput.value = ''; };
['dragenter', 'dragover'].forEach((name) => dropzone.addEventListener(name, (event) => { event.preventDefault(); dropzone.classList.add('over'); }));
['dragleave', 'drop'].forEach((name) => dropzone.addEventListener(name, (event) => { event.preventDefault(); dropzone.classList.remove('over'); }));
dropzone.ondrop = (event) => addFiles(event.dataTransfer.files);
$('#clearFiles').onclick = () => { staged = []; renderStaged(); };

function bindSegmented(id) {
  $(id).onclick = (event) => {
    const button = event.target.closest('button');
    if (!button) return;
    $(id).querySelectorAll('button').forEach((item) => item.classList.toggle('active', item === button));
  };
}
bindSegmented('#preprocessModes');
bindSegmented('#preprocessWorkers');
bindSegmented('#computeDevices');
function syncPreprocessControls() {
  const disabled = !$('#usePreprocessing').checked;
  $('#preprocessModes').classList.toggle('disabled', disabled);
  $('#preprocessWorkerSetting').classList.toggle('disabled', disabled);
}
$('#usePreprocessing').onchange = syncPreprocessControls;
syncPreprocessControls();
mergeFiles.onchange = () => $('#mergeNameWrap').classList.toggle('is-hidden', !mergeFiles.checked);

startButton.onclick = async () => {
  const chosen = staged.filter((item) => item.selected);
  if (!chosen.length) return;
  startButton.disabled = true;
  startButton.classList.add('loading');
  $('#formError').textContent = '';
  const form = new FormData();
  chosen.forEach((item) => form.append('files', item.file, item.file.name));
  form.append('language', $('#language').value);
  form.append('model', $('#model').value);
  form.append('compute_device', $('#computeDevices .active').dataset.value);
  form.append('use_preprocessing', $('#usePreprocessing').checked);
  form.append('preprocessing_mode', $('#preprocessModes .active').dataset.value);
  form.append('preprocessing_workers', $('#preprocessWorkers .active').dataset.value);
  form.append('keep_processed_audio', $('#keepProcessed').checked);
  form.append('merge_requested', mergeFiles.checked);
  form.append('merge_name', $('#mergeName').value || 'merged_lectures');
  try {
    const data = await requestJson('/api/jobs', { method: 'POST', body: form });
    selectedPreviewId = data.jobs[0]?.id;
    staged = staged.filter((item) => !item.selected);
    renderStaged();
  } catch (error) {
    $('#formError').textContent = error.message;
  } finally {
    startButton.classList.remove('loading');
    startButton.disabled = !staged.some((item) => item.selected);
  }
};

const activeStatuses = ['preprocessing', 'ready', 'transcribing', 'pausing', 'paused', 'stopping'];
const runningStatuses = ['preprocessing', 'transcribing'];
const statusIcon = (status) => ({ queued: '⠿', preprocessing: '◌', ready: '✓', transcribing: '◌', pausing: 'Ⅱ', paused: 'Ⅱ', stopping: '◌', completed: '✓', failed: '!', cancelled: '×' })[status];
const mergePalette = ['#007aff', '#af52de', '#ff9500', '#34c759', '#ff2d55', '#5ac8fa'];
const groupColors = new Map();
let nextColorIndex = 0;

function mergeColor(groupId) {
  if (!groupId) return mergePalette[0];
  if (!groupColors.has(groupId)) {
    groupColors.set(groupId, mergePalette[nextColorIndex % mergePalette.length]);
    nextColorIndex++;
  }
  return groupColors.get(groupId);
}

function mergedTranscript(job, jobs) {
  return jobs.filter((item) => item.group_id === job.group_id).map((item, index) =>
    `-----------------\nPart ${index + 1}\n-----------------\n\n${item.transcript || ''}`
  ).join('\n\n');
}
function statusText(job) {
  if (job.status === 'failed') return job.error;
  if (job.status === 'preprocessing') return `Preprocessing · ${Math.round(job.preprocessing_progress)}%`;
  if (job.status === 'ready') return 'Ready for transcription';
  if (job.status === 'transcribing') return `Transcribing · ${Math.round(job.transcription_progress)}%`;
  if (job.status === 'paused') return job.phase === 'preprocessing' ? `Paused · ${Math.round(job.preprocessing_progress)}%` : `Paused · ${Math.round(job.transcription_progress)}%`;
  return ({ queued: 'Waiting · drag to reorder', pausing: 'Pausing…', stopping: 'Stopping…', completed: 'Completed', cancelled: 'Cancelled' })[job.status];
}

function pauseControl(job) {
  if (![...runningStatuses, 'pausing', 'paused'].includes(job.status)) return '';
  const resume = ['pausing', 'paused'].includes(job.status);
  const action = resume ? 'resume' : 'pause';
  const label = resume ? 'Resume job' : 'Pause job';
  const icon = resume
    ? '<path d="M8 5v14l11-7L8 5Z"/>'
    : '<path d="M7 5h4v14H7V5Zm6 0h4v14h-4V5Z"/>';
  return `<button class="pause-toggle ${resume ? 'resume' : ''}" type="button" data-action="${action}" title="${label}" aria-label="${label}"><svg viewBox="0 0 24 24" aria-hidden="true">${icon}</svg></button>`;
}

function trashControl(job) {
  if (job.status === 'stopping') return '';
  return `<button class="cancel" type="button" title="Delete from queue" aria-label="Delete ${escapeHtml(job.source_name)} from queue"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3h6l1 2h4v2H4V5h4l1-2Zm-3 6h12l-1 12H7L6 9Zm4 2v7h2v-7h-2Zm4 0v7h2v-7h-2Z"/></svg></button>`;
}

async function persistQueueOrder() {
  const order = [...$('#queueList').querySelectorAll('.queue-item[data-status="queued"]')].map((row) => row.dataset.id);
  try {
    await requestJson('/api/queue', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ order }) });
  } catch (error) {
    $('#formError').textContent = error.message;
    loadState();
  }
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || 'Unable to update the queue.');
  return data;
}

async function runJobAction(url, options) {
  try {
    $('#formError').textContent = '';
    await requestJson(url, options);
  } catch (error) {
    $('#formError').textContent = error.message;
    loadState();
  }
}

async function loadState() {
  try {
    renderState(await requestJson('/api/state'));
  } catch (error) {
    $('#formError').textContent = 'Unable to connect to the transcription service.';
  }
}

function bindQueueInteractions() {
  const queue = $('#queueList');
  queue.querySelectorAll('.queue-item').forEach((row) => {
    row.onclick = (event) => {
      if (!event.target.closest('button') && !row.classList.contains('dragging')) {
        selectedPreviewId = row.dataset.id;
        renderState(lastState);
      }
    };
    row.onkeydown = (event) => {
      if ((event.key === 'Enter' || event.key === ' ') && event.target === row) {
        event.preventDefault();
        selectedPreviewId = row.dataset.id;
        renderState(lastState);
      }
    };
    row.querySelector('.cancel')?.addEventListener('click', (event) => {
      event.stopPropagation();
      runJobAction(`/api/jobs/${row.dataset.id}`, { method: 'DELETE' });
    });
    row.querySelector('.pause-toggle')?.addEventListener('click', (event) => {
      event.stopPropagation();
      const action = event.currentTarget.dataset.action;
      runJobAction(`/api/jobs/${row.dataset.id}/${action}`, { method: 'POST' });
    });
    if (row.dataset.status === 'queued') {
      row.ondragstart = (e) => { 
        isDraggingQueue = true; 
        e.dataTransfer.effectAllowed = 'move';
        
        const isMerge = row.classList.contains('merge-group');
        const groupId = isMerge ? row.dataset.groupId : null;
        
        setTimeout(() => {
          row.classList.add('dragging');
          if (groupId) {
            queue.querySelectorAll(`.queue-item[data-group-id="${groupId}"]:not(.dragging)`).forEach(el => el.classList.add('group-sibling-dragging'));
          }
        }, 0);
      };
      row.ondragend = () => { 
        isDraggingQueue = false; 
        const groupId = row.classList.contains('merge-group') ? row.dataset.groupId : null;
        const movings = groupId ? [...queue.querySelectorAll(`.queue-item[data-group-id="${groupId}"]`)] : [row];
        movings.forEach(el => {
          el.classList.remove('dragging', 'group-sibling-dragging'); 
          el.classList.add('dropped');
          setTimeout(() => el.classList.remove('dropped'), 800);
        });
        persistQueueOrder(); 
      };
    }
  });
  queue.ondragover = (event) => {
    event.preventDefault();
    const moving = queue.querySelector('.queue-item.dragging');
    if (!moving) return;
    const target = event.target.closest('.queue-item[data-status="queued"]:not(.dragging)');
    if (!target) return;
    
    const movingGroupId = moving.classList.contains('merge-group') ? moving.dataset.groupId : null;
    const targetGroupId = target.classList.contains('merge-group') ? target.dataset.groupId : null;
    
    const box = target.getBoundingClientRect();
    const insertAfter = event.clientY > box.top + box.height / 2;
    
    if (movingGroupId && movingGroupId === targetGroupId) {
      queue.insertBefore(moving, insertAfter ? target.nextSibling : target);
      return;
    }
    
    const movings = movingGroupId ? [...queue.querySelectorAll(`.queue-item[data-group-id="${movingGroupId}"]`)] : [moving];
    let dropReference = target;
    
    if (targetGroupId) {
      const targetGroup = [...queue.querySelectorAll(`.queue-item[data-group-id="${targetGroupId}"]`)];
      if (targetGroup.length) {
        dropReference = insertAfter ? targetGroup[targetGroup.length - 1] : targetGroup[0];
      }
    }
    
    const insertNode = insertAfter ? dropReference.nextSibling : dropReference;
    movings.forEach(m => queue.insertBefore(m, insertNode));
  };
}

let isDraggingQueue = false;

function renderState(state) {
  lastState = state;
  const jobs = state.jobs;
  $('#queueCount').textContent = jobs.filter((job) => ['queued', ...activeStatuses].includes(job.status)).length;
  const queue = $('#queueList');
  if (!isDraggingQueue && !queue.querySelector('.dropped')) {
    queue.innerHTML = !jobs.length ? '<div class="queue-empty"><p>No audio in queue</p></div>' : jobs.map((job) => {
      const currentProgress = ['preprocessing', 'ready'].includes(job.phase) ? job.preprocessing_progress : job.transcription_progress;
      return `
      <div class="queue-item ${job.id === selectedPreviewId ? 'selected' : ''} ${job.merge_requested ? 'merge-group' : ''}" style="${job.merge_requested ? `--group-color:${mergeColor(job.group_id)}` : ''}" data-id="${job.id}" data-group-id="${job.group_id}" data-status="${job.status}" draggable="${job.status === 'queued'}" role="button" tabindex="0">
        <span class="queue-state ${job.status}">${statusIcon(job.status)}</span>
        <span class="queue-copy"><strong>${escapeHtml(job.source_name)}</strong><small>${job.merge_requested ? '<em>Merge group</em> · ' : ''}${escapeHtml(statusText(job))}</small><i><b style="width:${currentProgress}%"></b></i></span>
        <span class="queue-actions">${pauseControl(job)}${trashControl(job)}</span>
      </div>`;
    }).join('');
    bindQueueInteractions();
  }
  if (!selectedPreviewId || !jobs.some((job) => job.id === selectedPreviewId)) selectedPreviewId = jobs.find((job) => activeStatuses.includes(job.status))?.id || jobs[0]?.id;
  renderPreview(jobs.find((job) => job.id === selectedPreviewId), jobs, state.merged_outputs);
  $('#mergedOutputs').innerHTML = Object.values(state.merged_outputs).map((name) => `<a href="/download/${encodeURIComponent(name)}"><span>✓</span><div><strong>Merged file ready</strong><small>${escapeHtml(name)}</small></div><b>↓</b></a>`).join('');
}

function renderPreview(job, jobs = lastState.jobs, mergedOutputs = lastState.merged_outputs) {
  const stop = $('#stopCurrent');
  if (!job) {
    activeStopId = null;
    document.body.classList.remove('preview-paused');
    document.body.classList.remove('is-transcribing');
    stop.classList.add('is-hidden');
    $('#previewTitle').textContent = 'No audio selected';
    $('#preprocessingBar').style.width = '0%';
    $('#preprocessingLabel').textContent = '0%';
    $('#transcriptionBar').style.width = '0%';
    $('#transcriptionLabel').textContent = '0%';
    $('#transcriptText').textContent = '';
    $('#transcriptText').classList.add('is-hidden');
    $('#emptyPreview').classList.remove('is-hidden');
    $('#downloadCurrent').classList.add('is-hidden');
    return;
  }
  const isMerge = job.merge_requested;
  const activeJob = isMerge ? jobs.find((item) => item.group_id === job.group_id && activeStatuses.includes(item.status)) : job;
  const displayJob = activeJob || job;
  activeStopId = activeJob?.id || null;
  document.body.classList.toggle('preview-paused', ['pausing', 'paused'].includes(activeJob?.status));
  document.body.classList.toggle('is-transcribing', activeStatuses.includes(activeJob?.status));
  const mergeTitle = (job.merge_name || 'merged_lectures').replace(/\.txt$/i, '');
  $('#previewTitle').textContent = isMerge ? `${mergeTitle}.txt` : job.source_name;
  const preprocessing = displayJob.use_preprocessing ? displayJob.preprocessing_progress : 100;
  $('#preprocessingBar').style.width = `${preprocessing}%`;
  $('#preprocessingLabel').textContent = displayJob.use_preprocessing ? `${Math.round(preprocessing)}%` : 'Skipped';
  $('#transcriptionBar').style.width = `${displayJob.transcription_progress}%`;
  $('#transcriptionLabel').textContent = `${Math.round(displayJob.transcription_progress)}%`;
  const previewText = isMerge ? mergedTranscript(job, jobs) : job.transcript;
  const hasText = Boolean(previewText);
  $('#emptyPreview').classList.toggle('is-hidden', hasText);
  $('#transcriptText').classList.toggle('is-hidden', !hasText);
  if (hasText) {
    $('#transcriptText').textContent = previewText;
    $('#transcriptText').scrollTop = $('#transcriptText').scrollHeight;
  }
  stop.classList.toggle('is-hidden', !activeJob || ![...runningStatuses, 'pausing', 'paused', 'stopping'].includes(activeJob.status));
  stop.disabled = activeJob?.status === 'stopping';
  stop.textContent = activeJob?.status === 'stopping' ? 'Stopping…' : (activeJob?.phase === 'preprocessing' ? 'Stop preprocessing' : 'Stop transcription');
  const download = $('#downloadCurrent');
  const outputFile = isMerge ? mergedOutputs[job.group_id] : job.output_file;
  download.classList.toggle('is-hidden', !outputFile);
  if (outputFile) download.href = `/download/${encodeURIComponent(outputFile)}`;
}

$('#stopCurrent').onclick = () => {
  if (activeStopId) runJobAction(`/api/jobs/${activeStopId}/stop`, { method: 'POST' });
};
const setDrawer = (open) => { document.body.classList.toggle('drawer-closed', !open); $('#drawerToggle').setAttribute('aria-expanded', String(open)); };
$('#drawerToggle').onclick = () => setDrawer(document.body.classList.contains('drawer-closed'));
$('#drawerBackdrop').onclick = () => setDrawer(false);

loadState();
const eventStream = new EventSource('/api/events');
eventStream.onmessage = (event) => {
  try { renderState(JSON.parse(event.data)); } catch { loadState(); }
};
