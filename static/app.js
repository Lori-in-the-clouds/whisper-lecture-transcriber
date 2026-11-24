const $ = (selector) => document.querySelector(selector);
const fileInput = $('#fileInput');
const dropzone = $('#dropzone');
const stagedWrap = $('#stagedWrap');
const stagedFiles = $('#stagedFiles');
const startButton = $('#startButton');
const mergeFiles = $('#mergeFiles');
const languageSelect = $('#language');
const fileTitleTooltip = document.createElement('div');
fileTitleTooltip.id = 'fileTitleTooltip';
fileTitleTooltip.className = 'file-title-tooltip is-hidden';
fileTitleTooltip.setAttribute('aria-hidden', 'true');
document.body.append(fileTitleTooltip);
let staged = [];
let selectedPreviewId = null;
let activeStopId = null;
let lastState = { jobs: [], merged_outputs: {} };
let shownFileTitle = null;
let fileTitlePointer = null;

const escapeHtml = (value) => { const div = document.createElement('div'); div.textContent = value; return div.innerHTML; };
const formatBytes = (bytes) => bytes < 1048576 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1048576).toFixed(1)} MB`;

function hideFileTitle() {
  shownFileTitle = null;
  fileTitlePointer = null;
  fileTitleTooltip.classList.add('is-hidden');
}

function showFileTitle(title, pointer = null) {
  if (!title || !title.clientWidth || title.scrollWidth <= title.clientWidth) {
    hideFileTitle();
    return;
  }
  const bounds = title.getBoundingClientRect();
  const drawer = title.closest('.drawer-scroll')?.getBoundingClientRect();
  if (drawer && (bounds.bottom <= drawer.top || bounds.top >= drawer.bottom)) {
    hideFileTitle();
    return;
  }
  shownFileTitle = title;
  fileTitlePointer = pointer;
  if (fileTitleTooltip.textContent !== title.textContent) fileTitleTooltip.textContent = title.textContent;
  fileTitleTooltip.classList.remove('is-hidden');

  const margin = 12;
  const gap = 5;
  const viewport = document.documentElement;
  const right = viewport.clientWidth - fileTitleTooltip.offsetWidth - margin;
  const bottom = viewport.clientHeight - fileTitleTooltip.offsetHeight - margin;
  const below = bounds.bottom + gap;
  const top = below <= bottom ? below : bounds.top - fileTitleTooltip.offsetHeight - gap;
  fileTitleTooltip.style.left = `${Math.max(margin, Math.min(bounds.left, right))}px`;
  fileTitleTooltip.style.top = `${Math.max(margin, Math.min(top, bottom))}px`;
}

function focusedFileTitle() {
  const focused = document.activeElement;
  if (!focused?.matches(':focus-visible')) return null;
  if (focused.matches('.queue-item')) return focused.querySelector('.file-title');
  return focused.closest('.file-title');
}

function refreshFileTitle() {
  if (!shownFileTitle) return;
  const title = fileTitlePointer
    ? document.elementFromPoint(fileTitlePointer.x, fileTitlePointer.y)?.closest('.file-title')
    : focusedFileTitle();
  showFileTitle(title, fileTitlePointer);
}

document.addEventListener('pointerover', (event) => {
  const title = event.target.closest('.file-title');
  if (event.pointerType !== 'touch' && title && !title.contains(event.relatedTarget)) {
    showFileTitle(title, { x: event.clientX, y: event.clientY });
  }
});
document.addEventListener('pointermove', (event) => {
  if (fileTitlePointer && event.pointerType !== 'touch') {
    if (!shownFileTitle.contains(event.target)) {
      showFileTitle(focusedFileTitle());
      return;
    }
    fileTitlePointer.x = event.clientX;
    fileTitlePointer.y = event.clientY;
  }
});
document.addEventListener('pointerout', (event) => {
  if (fileTitlePointer && shownFileTitle?.contains(event.target) && !shownFileTitle.contains(event.relatedTarget)) {
    showFileTitle(focusedFileTitle());
  }
});
document.addEventListener('focusin', () => {
  const title = focusedFileTitle();
  if (title) showFileTitle(title);
});
document.addEventListener('focusout', () => {
  if (!fileTitlePointer) hideFileTitle();
});
document.addEventListener('scroll', refreshFileTitle, true);
document.addEventListener('dragstart', hideFileTitle);
window.addEventListener('resize', refreshFileTitle);
window.addEventListener('blur', hideFileTitle);

function addFiles(files) {
  const known = new Set(staged.map((item) => `${item.file.name}:${item.file.size}`));
  [...files].forEach((file) => {
    const key = `${file.name}:${file.size}`;
    if (!known.has(key)) {
      staged.push({ id: crypto.randomUUID(), file, selected: true, language: '' });
      known.add(key);
    }
  });
  renderStaged();
}

function renderStaged() {
  stagedWrap.classList.toggle('is-hidden', !staged.length);
  startButton.disabled = startButton.classList.contains('loading') || !staged.some((item) => item.selected);
  let part = 0;
  stagedFiles.innerHTML = staged.map((item) => {
    const partLabel = item.selected ? `Part ${++part}` : 'Excluded';
    return `<div class="staged-file" draggable="true" data-id="${item.id}">
      <div class="staged-file-details">
        <span class="drag">⠿</span><label><input type="checkbox" ${item.selected ? 'checked' : ''}><span class="custom-check"></span></label>
        <div class="file-icon">♫</div><div class="file-meta"><strong class="file-title" tabindex="0">${escapeHtml(item.file.name)}</strong><small>${formatBytes(item.file.size)}${mergeFiles.checked ? ` · ${partLabel}` : ''}</small></div>
        <button class="remove" aria-label="Remove">×</button>
      </div>
      <label class="field staged-language"><span>File language</span><select></select></label>
    </div>`;
  }).join('');
  stagedFiles.querySelectorAll('.staged-file').forEach((row, index) => {
    const item = staged[index];
    const checkbox = row.querySelector('input');
    checkbox.setAttribute('aria-label', `Include ${item.file.name}`);
    checkbox.onchange = () => {
      item.selected = checkbox.checked;
      renderStaged();
      stagedFiles.querySelector(`[data-id="${item.id}"] input`).focus();
    };
    row.querySelector('.remove').onclick = () => { staged = staged.filter((file) => file !== item); renderStaged(); };
    const select = row.querySelector('select');
    select.setAttribute('aria-label', `File language for ${item.file.name}`);
    select.add(new Option('', ''));
    [...languageSelect.options].forEach((option) => select.add(new Option(option.text, option.value)));
    select.value = item.language;
    select.disabled = !item.selected;
    select.onchange = () => { item.language = select.value; };
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
  updateFileLanguageDefaults();
  refreshFileTitle();
}

function updateFileLanguageDefaults() {
  const label = languageSelect.selectedOptions[0].text;
  stagedFiles.querySelectorAll('.staged-language select').forEach((select) => {
    select.options[0].text = `Use default (${label})`;
  });
}
languageSelect.addEventListener('change', updateFileLanguageDefaults);

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
bindSegmented('#preprocessSampleRate');
bindSegmented('#preprocessWorkers');
bindSegmented('#computeDevices');
function syncPreprocessControls() {
  const disabled = !$('#usePreprocessing').checked;
  $('#preprocessModes').classList.toggle('disabled', disabled);
  $('#preprocessSampleRateSetting').classList.toggle('disabled', disabled);
  $('#preprocessWorkerSetting').classList.toggle('disabled', disabled);
}
$('#usePreprocessing').onchange = syncPreprocessControls;
syncPreprocessControls();
function syncMergeControls() {
  $('#mergeNameWrap').classList.toggle('is-hidden', !mergeFiles.checked);
  renderStaged();
}
mergeFiles.addEventListener('change', syncMergeControls);
window.addEventListener('pageshow', syncMergeControls);
syncMergeControls();

startButton.onclick = async () => {
  const chosen = staged.filter((item) => item.selected);
  if (!chosen.length) return;
  startButton.disabled = true;
  startButton.classList.add('loading');
  $('#formError').textContent = '';
  const form = new FormData();
  const merge = mergeFiles.checked;
  chosen.forEach((item) => {
    form.append('files', item.file, item.file.name);
    form.append('file_languages', item.language || languageSelect.value);
  });
  form.append('language', languageSelect.value);
  form.append('model', $('#model').value);
  form.append('compute_device', $('#computeDevices .active').dataset.value);
  form.append('use_preprocessing', $('#usePreprocessing').checked);
  form.append('preprocessing_mode', $('#preprocessModes .active').dataset.value);
  form.append('preprocessing_sample_rate', $('#preprocessSampleRate .active').dataset.value);
  form.append('preprocessing_workers', $('#preprocessWorkers .active').dataset.value);
  form.append('keep_processed_audio', $('#keepProcessed').checked);
  form.append('merge_requested', merge);
  form.append('merge_name', $('#mergeName').value || 'merged_lectures');
  try {
    const data = await requestJson('/api/jobs', { method: 'POST', body: form });
    selectedPreviewId = data.jobs[0]?.id;
    staged = staged.filter((item) => !chosen.includes(item));
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
const statusPaths = {
  queued: '<circle cx="12" cy="12" r="8"/><path d="M12 7v5l3 2"/>',
  preprocessing: '<path d="m10 3-.5 2-2 .9-1.8-.6-2 3.4 1.4 1.4v2.8L3.7 14l2 3.4 1.8-.6 2 .9.5 2h4l.5-2 2-.9 1.8.6 2-3.4-1.4-1.1v-2.8l1.4-1.4-2-3.4-1.8.6-2-.9L14 3Z"/><circle cx="12" cy="11.5" r="3"/>',
  ready: '<path d="M13 3H5v18h14v-9M13 3v6h6M13 3l6 6M8 15h7m-3-3 3 3-3 3"/>',
  transcribing: '<path d="M4 10v4m4-7v10m4-13v16m4-13v10m4-7v4"/>',
  pausing: '<path d="M8 5v14M16 5v14"/>',
  paused: '<path d="M8 5v14M16 5v14"/>',
  stopping: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
  completed: '<path d="m6 12 4 4 8-8"/>',
  failed: '<path d="M12 7v6m0 4h.01"/><path d="m12 3 10 18H2Z"/>',
  cancelled: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
};
const statusIcon = (status) => `<svg viewBox="0 0 24 24" aria-hidden="true">${statusPaths[status] || statusPaths.queued}</svg>`;
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

const transcriptArticle = $('#transcriptText');
const partNavigator = $('#partNavigator');
let previewKey = null;
let partScrollFrame = null;
let navigationPartId = null;
let navigationFrame = null;

function cancelPartNavigation() {
  if (navigationFrame !== null) cancelAnimationFrame(navigationFrame);
  navigationFrame = null;
  navigationPartId = null;
}

function partScrollTop(section) {
  const top = transcriptArticle.scrollTop + section.getBoundingClientRect().top
    - transcriptArticle.getBoundingClientRect().top - transcriptArticle.clientTop;
  return Math.max(0, Math.min(top, transcriptArticle.scrollHeight - transcriptArticle.clientHeight));
}

function alignNavigationPart() {
  const section = navigationPartId && document.getElementById(navigationPartId);
  if (!section) return;
  const top = partScrollTop(section);
  if (Math.abs(top - transcriptArticle.scrollTop) > .5) {
    transcriptArticle.scrollTo({ top, behavior: 'instant' });
  }
}

function navigateToPart(section) {
  cancelPartNavigation();
  navigationPartId = section.id;
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) {
    alignNavigationPart();
    scheduleActivePart();
    return;
  }
  const from = transcriptArticle.scrollTop;
  const started = performance.now();
  const animate = (now) => {
    if (!section.isConnected) { cancelPartNavigation(); return; }
    const progress = Math.min(1, (now - started) / 320);
    const eased = 1 - (1 - progress) ** 3;
    // Recalculate the destination as live text changes the earlier parts' height.
    transcriptArticle.scrollTo({ top: from + (partScrollTop(section) - from) * eased, behavior: 'instant' });
    scheduleActivePart();
    navigationFrame = progress < 1 ? requestAnimationFrame(animate) : null;
  };
  navigationFrame = requestAnimationFrame(animate);
}

function updateActivePart() {
  const sections = [...transcriptArticle.querySelectorAll('.transcript-part')];
  const buttons = [...partNavigator.querySelectorAll('button')];
  if (!buttons.length) return;
  const readingLine = transcriptArticle.getBoundingClientRect().top + 40;
  const available = sections.filter((_, index) => !buttons[index].disabled);
  let current = available[0];
  for (const section of available) {
    if (section.getBoundingClientRect().top <= readingLine) current = section;
  }
  if (transcriptArticle.scrollHeight > transcriptArticle.clientHeight &&
      transcriptArticle.scrollTop + transcriptArticle.clientHeight >= transcriptArticle.scrollHeight - 2) {
    current = available.at(-1);
  }
  buttons.forEach((button, index) => {
    const active = sections[index] === current;
    button.classList.toggle('active', active);
    if (active) button.setAttribute('aria-current', 'location');
    else button.removeAttribute('aria-current');
  });
}

function scheduleActivePart() {
  if (partScrollFrame !== null) return;
  partScrollFrame = requestAnimationFrame(() => {
    partScrollFrame = null;
    updateActivePart();
  });
}
transcriptArticle.addEventListener('scroll', scheduleActivePart, { passive: true });
// Keep an explicitly opened part in view as earlier parts receive live text.
['wheel', 'touchstart', 'pointerdown', 'keydown'].forEach((type) => {
  transcriptArticle.addEventListener(type, cancelPartNavigation, { passive: true });
});
new ResizeObserver(() => {
  if (navigationFrame === null) alignNavigationPart();
  scheduleActivePart();
}).observe(transcriptArticle);

partNavigator.addEventListener('click', (event) => {
  const button = event.target.closest('button');
  if (!button || button.disabled) return;
  const section = document.getElementById(button.getAttribute('aria-controls'));
  if (!section) return;
  navigateToPart(section);
});

function renderTranscript(job, jobs) {
  const parts = job?.merge_requested ? jobs.filter((item) => item.group_id === job.group_id) : [];
  const key = job ? (job.merge_requested ? `group:${job.group_id}` : `job:${job.id}`) : null;
  const changed = key !== previewKey;
  const anchor = !changed && navigationPartId ? document.getElementById(navigationPartId) : null;
  const followEnd = !changed && !anchor && transcriptArticle.scrollHeight - transcriptArticle.scrollTop - transcriptArticle.clientHeight < 48;
  if (changed || !job) {
    cancelPartNavigation();
    transcriptArticle.replaceChildren();
    partNavigator.replaceChildren();
    previewKey = key;
  }
  if (parts.length) {
    const signature = JSON.stringify(parts.map((part) => part.id));
    if (transcriptArticle.dataset.parts !== signature || changed) {
      transcriptArticle.replaceChildren();
      partNavigator.replaceChildren();
      parts.forEach((part, index) => {
        const section = document.createElement('section');
        section.className = 'transcript-part';
        section.id = `transcript-part-${part.id}`;
        const heading = document.createElement('h2');
        heading.textContent = `Part ${index + 1}`;
        const body = document.createElement('div');
        body.className = 'transcript-part-body';
        section.append(heading, body);
        transcriptArticle.append(section);
        const button = document.createElement('button');
        button.type = 'button';
        button.setAttribute('aria-label', `Part ${index + 1}`);
        button.title = `Part ${index + 1}`;
        button.setAttribute('aria-controls', section.id);
        button.innerHTML = '<span class="part-line" aria-hidden="true"></span>';
        partNavigator.append(button);
      });
      transcriptArticle.dataset.parts = signature;
    }
    parts.forEach((part, index) => {
      const body = transcriptArticle.children[index].querySelector('.transcript-part-body');
      const text = part.transcript || '';
      if (body.textContent !== text) body.textContent = text;
      const button = partNavigator.children[index];
      button.classList.toggle('is-pending', !text.trim());
      button.classList.toggle('has-error', part.status === 'failed');
    });
  } else {
    delete transcriptArticle.dataset.parts;
    const text = job?.transcript || '';
    if (transcriptArticle.textContent !== text) transcriptArticle.textContent = text;
  }
  const hasText = parts.length > 0 || Boolean(job?.transcript);
  $('#emptyPreview').classList.toggle('is-hidden', hasText);
  transcriptArticle.classList.toggle('is-hidden', !hasText);
  partNavigator.classList.toggle('is-hidden', !parts.length);
  $('.live-stage').classList.toggle('has-parts', parts.length > 0);
  if (changed) transcriptArticle.scrollTop = 0;
  else if (navigationFrame !== null) { /* The animation owns scrolling until it ends. */ }
  else if (anchor?.isConnected) alignNavigationPart();
  else if (followEnd) transcriptArticle.scrollTop = transcriptArticle.scrollHeight;
  scheduleActivePart();
}

function transcriptName(stem, job, language = (job.language || 'en').toLowerCase()) {
  const preprocessing = job.use_preprocessing ? job.preprocessing_mode : 'none';
  return `${stem}_${language}_${preprocessing}`;
}

function queuedTranscriptName(job) {
  if (job.output_file) return job.output_file.replace(/\.[^.]+$/, '');
  const sourceStem = job.source_name.replace(/\.[^.]+$/, '');
  return transcriptName(sourceStem, job);
}

function downloadPicker(filename) {
  const url = `/download/${encodeURIComponent(filename)}`;
  const formats = [['txt', 'Plain text'], ['md', 'Markdown'], ['html', 'HTML']];
  return `<details class="download-picker">
    <summary class="secondary-button">Download <span class="download-chevron" aria-hidden="true"><svg viewBox="0 0 16 16"><path d="m4 6 4 4 4-4"/></svg></span></summary>
    <div class="download-options" aria-label="Download format">${formats.map(([format, label]) =>
      `<a href="${url}?format=${format}"><span>${label}</span><small>.${format}</small></a>`).join('')}</div>
  </details>`;
}

document.addEventListener('click', (event) => {
  const picker = event.target.closest('.download-picker');
  document.querySelectorAll('.download-picker[open]').forEach((openPicker) => {
    if (openPicker !== picker || event.target.closest('.download-options a')) openPicker.open = false;
  });
});
document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;
  hideFileTitle();
  document.querySelectorAll('.download-picker[open]').forEach((picker) => {
    picker.open = false;
    picker.querySelector('summary').focus();
  });
});
document.addEventListener('toggle', (event) => {
  const picker = event.target;
  if (picker.matches('.ready-file .download-picker') && picker.open) {
    picker.querySelector('.download-options').scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'instant' });
  }
}, true);

function renderReadyFiles(jobs, mergedOutputs) {
  const files = [...new Set([
    ...Object.values(mergedOutputs),
    ...jobs.filter((job) => job.status === 'completed' && job.output_file).map((job) => job.output_file),
  ])];
  $('#readyFilesSection').classList.toggle('is-hidden', !files.length);
  const list = $('#readyFiles');
  const signature = JSON.stringify(files);
  if (list.dataset.signature === signature) return;
  const existing = new Map([...list.children].map((row) => [row.dataset.file, row]));
  files.forEach((filename, index) => {
    let row = existing.get(filename);
    if (!row) {
      row = document.createElement('li');
      row.className = 'ready-file';
      row.dataset.file = filename;
      row.innerHTML = `<span class="ready-file-name file-title" tabindex="0"></span>${downloadPicker(filename)}`;
      const name = row.querySelector('.ready-file-name');
      name.textContent = filename.replace(/\.[^.]+$/, '');
      row.querySelector('summary').title = `Download ${name.textContent}`;
    }
    if (list.children[index] !== row) list.insertBefore(row, list.children[index] || null);
  });
  const included = new Set(files);
  [...list.children].forEach((row) => { if (!included.has(row.dataset.file)) row.remove(); });
  list.dataset.signature = signature;
}

function statusText(job) {
  if (job.status === 'failed') return job.error || 'Transcription failed';
  if (job.status === 'completed' && job.merge_requested) return 'Part completed';
  if (job.status === 'preprocessing') return `Preprocessing · ${Math.round(job.preprocessing_progress)}%`;
  if (job.status === 'ready') return 'Ready for transcription';
  if (job.status === 'transcribing') return `${job.device_fallback ? 'GPU unavailable · CPU fallback' : 'Transcribing'} · ${Math.round(job.transcription_progress)}%`;
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
  queue.onclick = (event) => {
    const row = event.target.closest('.queue-item');
    if (!row) return;
    const button = event.target.closest('button');
    const url = `/api/jobs/${row.dataset.id}`;
    if (button?.classList.contains('cancel')) {
      runJobAction(url, { method: 'DELETE' });
    } else if (button?.dataset.action) {
      runJobAction(`${url}/${button.dataset.action}`, { method: 'POST' });
    } else if (!button && !row.classList.contains('dragging')) {
      selectedPreviewId = row.dataset.id;
      renderState(lastState);
    }
  };
  queue.onkeydown = (event) => {
    if ((event.key === 'Enter' || event.key === ' ') && event.target.matches('.queue-item')) {
      event.preventDefault();
      selectedPreviewId = event.target.dataset.id;
      renderState(lastState);
    }
  };
  queue.ondragstart = (event) => {
    const row = event.target.closest('.queue-item[data-status="queued"]');
    if (!row) return;
    isDraggingQueue = true;
    event.dataTransfer.effectAllowed = 'move';
    const groupId = row.classList.contains('merge-group') ? row.dataset.groupId : null;
    setTimeout(() => {
      if (!isDraggingQueue) return;
      row.classList.add('dragging');
      if (groupId) {
        queue.querySelectorAll(`.queue-item[data-group-id="${groupId}"]:not(.dragging)`).forEach((item) => {
          item.classList.add('group-sibling-dragging');
        });
      }
    }, 0);
  };
  queue.ondragend = (event) => {
    const row = event.target.closest('.queue-item');
    if (!row || !isDraggingQueue) return;
    isDraggingQueue = false;
    const groupId = row.classList.contains('merge-group') ? row.dataset.groupId : null;
    const moving = groupId ? [...queue.querySelectorAll(`.queue-item[data-group-id="${groupId}"]`)] : [row];
    moving.forEach((item) => {
      item.classList.remove('dragging', 'group-sibling-dragging');
      item.classList.add('dropped');
    });
    setTimeout(() => {
      moving.forEach((item) => item.classList.remove('dropped'));
      renderQueue(lastState.jobs);
    }, 800);
    persistQueueOrder();
  };
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
const queueRows = new Map();
bindQueueInteractions();

function renderQueue(jobs) {
  const queue = $('#queueList');
  if (isDraggingQueue || queue.querySelector('.dropped')) return;
  const ids = new Set(jobs.map((job) => job.id));
  for (const [id, entry] of queueRows) {
    if (!ids.has(id)) {
      entry.row.remove();
      queueRows.delete(id);
    }
  }
  if (!jobs.length) {
    if (!queue.firstElementChild) queue.innerHTML = '<div class="queue-empty"><p>No audio in queue</p></div>';
    return;
  }
  queue.querySelector('.queue-empty')?.remove();
  jobs.forEach((job, index) => {
    let entry = queueRows.get(job.id);
    if (!entry) {
      const row = document.createElement('div');
      row.className = 'queue-item';
      row.dataset.id = job.id;
      row.dataset.groupId = job.group_id;
      row.setAttribute('role', 'button');
      row.tabIndex = 0;
      if (job.merge_requested) {
        row.classList.add('merge-group');
        row.style.setProperty('--group-color', mergeColor(job.group_id));
      }
      row.innerHTML = `
        <span class="queue-state" aria-hidden="true"></span>
        <span class="queue-copy"><strong class="file-title"></strong><small>${job.merge_requested ? '<em>Merge group</em> · ' : ''}<span class="queue-status"></span></small><i><b></b></i></span>
        <span class="queue-actions"></span>`;
      entry = { row };
      queueRows.set(job.id, entry);
    }
    const { row } = entry;
    row.classList.toggle('selected', job.id === selectedPreviewId);
    if (row.dataset.status !== job.status) {
      row.dataset.status = job.status;
      row.draggable = job.status === 'queued';
      const state = row.querySelector('.queue-state');
      state.className = `queue-state ${job.status}`;
      state.innerHTML = statusIcon(job.status);
      row.querySelector('.queue-actions').innerHTML = pauseControl(job) + trashControl(job);
    }
    const title = queuedTranscriptName(job);
    const status = statusText(job);
    const progress = ['preprocessing', 'ready'].includes(job.phase) ? job.preprocessing_progress : job.transcription_progress;
    if (entry.title !== title) row.querySelector('.file-title').textContent = title;
    if (entry.status !== status) row.querySelector('.queue-status').textContent = status;
    if (entry.progress !== progress) row.querySelector('.queue-copy b').style.width = `${progress}%`;
    entry.title = title;
    entry.status = status;
    entry.progress = progress;
    if (row !== queue.children[index]) queue.insertBefore(row, queue.children[index] || null);
  });
}

function renderState(state) {
  lastState = state;
  const jobs = state.jobs;
  const count = String(jobs.filter((job) => ['queued', ...activeStatuses].includes(job.status)).length);
  if ($('#queueCount').textContent !== count) $('#queueCount').textContent = count;
  if (!selectedPreviewId || !jobs.some((job) => job.id === selectedPreviewId)) selectedPreviewId = jobs.find((job) => activeStatuses.includes(job.status))?.id || jobs[0]?.id;
  renderQueue(jobs);
  renderPreview(jobs.find((job) => job.id === selectedPreviewId), jobs, state.merged_outputs);
  renderReadyFiles(jobs, state.merged_outputs);
  refreshFileTitle();
}

function renderPreview(job, jobs = lastState.jobs, mergedOutputs = lastState.merged_outputs) {
  const stop = $('#stopCurrent');
  const previewTitle = $('#previewTitle');
  previewTitle.classList.toggle('file-title', Boolean(job));
  if (job) previewTitle.tabIndex = 0;
  else previewTitle.removeAttribute('tabindex');
  if (!job) {
    activeStopId = null;
    document.body.classList.remove('preview-paused');
    document.body.classList.remove('is-transcribing');
    document.body.classList.remove('preview-completed');
    stop.classList.add('is-hidden');
    previewTitle.textContent = 'No audio selected';
    $('#preprocessingBar').style.width = '0%';
    $('#preprocessingLabel').textContent = '0%';
    $('#transcriptionBar').style.width = '0%';
    $('#transcriptionLabel').textContent = '0%';
    renderTranscript(null, jobs);
    $('#downloadCurrent').classList.add('is-hidden');
    $('#downloadCurrent').querySelectorAll('details[open]').forEach((picker) => { picker.open = false; });
    return;
  }
  const isMerge = job.merge_requested;
  const groupJobs = isMerge ? jobs.filter((item) => item.group_id === job.group_id) : [];
  const activeJob = isMerge ? groupJobs.find((item) => activeStatuses.includes(item.status)) : job;
  const displayJob = activeJob || job;
  activeStopId = activeJob?.id || null;
  document.body.classList.toggle('preview-paused', ['pausing', 'paused'].includes(activeJob?.status));
  document.body.classList.toggle('is-transcribing', activeStatuses.includes(activeJob?.status));
  const mergeTitle = (job.merge_name || 'merged_lectures').replace(/(?:\.(?:txt|md|html))+$/i, '');
  const groupLanguages = new Set(groupJobs.map((item) => (item.language || 'en').toLowerCase()));
  const title = isMerge
    ? (mergedOutputs[job.group_id]?.replace(/\.[^.]+$/, '') || transcriptName(mergeTitle, job, groupLanguages.size > 1 ? 'mixed' : undefined))
    : queuedTranscriptName(job);
  if (previewTitle.textContent !== title) previewTitle.textContent = title;
  const preprocessing = displayJob.use_preprocessing ? displayJob.preprocessing_progress : 100;
  $('#preprocessingBar').style.width = `${preprocessing}%`;
  $('#preprocessingLabel').textContent = displayJob.use_preprocessing ? `${Math.round(preprocessing)}%` : 'Skipped';
  $('#transcriptionBar').style.width = `${displayJob.transcription_progress}%`;
  $('#transcriptionLabel').textContent = `${Math.round(displayJob.transcription_progress)}%`;
  renderTranscript(job, jobs);
  stop.classList.toggle('is-hidden', !activeJob || ![...runningStatuses, 'pausing', 'paused', 'stopping'].includes(activeJob.status));
  stop.disabled = activeJob?.status === 'stopping';
  stop.textContent = activeJob?.status === 'stopping' ? 'Stopping…' : (activeJob?.phase === 'preprocessing' ? 'Stop preprocessing' : 'Stop transcription');
  const download = $('#downloadCurrent');
  const outputFile = isMerge ? mergedOutputs[job.group_id] : job.output_file;
  document.body.classList.toggle('preview-completed', Boolean(outputFile));
  download.classList.toggle('is-hidden', !outputFile);
  if (download.dataset.file !== (outputFile || '')) {
    download.innerHTML = outputFile ? downloadPicker(outputFile) : '';
    download.dataset.file = outputFile || '';
  }
}

$('#stopCurrent').onclick = () => {
  if (activeStopId) runJobAction(`/api/jobs/${activeStopId}/stop`, { method: 'POST' });
};
const setDrawer = (open) => {
  document.body.classList.toggle('drawer-closed', !open);
  $('#drawerToggle').setAttribute('aria-expanded', String(open));
  hideFileTitle();
};
$('#drawerToggle').onclick = () => setDrawer(document.body.classList.contains('drawer-closed'));
$('#drawerBackdrop').onclick = () => setDrawer(false);

loadState();
const eventStream = new EventSource('/api/events');
let pendingStateData = null;
let stateFrame = null;
eventStream.onmessage = ({ data }) => {
  pendingStateData = data;
  if (stateFrame !== null) return;
  stateFrame = requestAnimationFrame(() => {
    stateFrame = null;
    const payload = pendingStateData;
    pendingStateData = null;
    try { renderState(JSON.parse(payload)); } catch { loadState(); }
  });
};
