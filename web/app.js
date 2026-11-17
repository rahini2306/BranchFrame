'use strict';
const $ = id => document.getElementById(id);
const state = {
  user: null,
  projects: [],
  project: null,
  branch: 'main',
  draft: {
    clips: []
  },
  dirty: false,
  register: false,
  tab: 'editor',
  merge: null,
  uploading: false
};
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;'
} [c]));
const e = escapeHTML;
const date = t => new Date(t * 1000).toLocaleString(undefined, {
  month: 'short',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit'
});

function toast(message, error = false) {
  $('toast').textContent = message;
  $('toast').className = error ? 'error' : '';
  $('toast').style.display = 'block';
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => $('toast').style.display = 'none', 5500)
}
async function api(path, data, options = {}) {
  const response = await fetch(path, {
    ...options,
    method: data === undefined ? 'GET' : 'POST',
    headers: data === undefined ? {} : {
      'Content-Type': 'application/json'
    },
    body: data === undefined ? undefined : JSON.stringify(data)
  });
  const value = await response.json();
  if (!response.ok) {
    if (response.status === 401) showAuth();
    throw new Error(value.error || 'Request failed')
  }
  return value
}

function safe(fn) {
  return async event => {
    try {
      await fn(event)
    } catch (error) {
      toast(error.message, true)
    }
  }
}

function projectAPI(action = '', data) {
  return api('/api/projects/' + state.project.id + (action ? '/' + action : ''), data)
}

function head(branch = state.branch) {
  return state.project.branches.find(b => b.name === branch)?.head
}

function commit(id = head()) {
  return state.project.commits.find(c => c.id === id)
}

function asset(id) {
  return state.project.assets.find(a => a.id === id)
}

function editable() {
  return state.project?.role !== 'reviewer'
}

function media(id) {
  return '/api/projects/' + state.project.id + '/assets/' + id + '/media'
}

function markDirty() {
  state.dirty = true;
  $('dirty-status').textContent = 'Uncommitted changes — save a commit to keep this version.';
  $('dirty-status').style.color = '#963f35'
}

function confirmDiscard() {
  if (state.uploading) {
    toast('Let this upload finish before changing projects or branches.', true);
    return false;
  }
  return !state.dirty || window.confirm('Discard your uncommitted timeline changes?')
}

function ask(title, description, label = 'Name', initial = '') {
  return new Promise(resolve => {
    const dialog = $('dialog');
    $('dialog-title').textContent = title;
    $('dialog-description').textContent = description;
    $('dialog-label').firstChild.textContent = label;
    $('dialog-input').value = initial;
    dialog.returnValue = '';
    dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm' ? $('dialog-input').value.trim() : null), {
      once: true
    });
    dialog.showModal();
    $('dialog-input').focus()
  })
}

function showAuth() {
  $('auth').classList.remove('hidden');
  $('shell').classList.add('hidden');
  state.user = null
}
async function boot() {
  try {
    state.user = await api('/api/me');
    $('auth').classList.add('hidden');
    $('shell').classList.remove('hidden');
    $('user-name').textContent = state.user.name;
    $('avatar').textContent = state.user.name[0].toUpperCase();
    await loadProjects();
    const health = await api('/api/health');
    $('renderer-status').textContent = health.renderer ? '● Local renderer ready' : '○ External renderer required';
    $('renderer-status').title = 'Remote workers may also process exports';
  } catch (error) {
    showAuth()
  }
}
async function loadProjects() {
  state.projects = await api('/api/projects');
  $('project-count').textContent = state.projects.length;
  $('project-grid').innerHTML = state.projects.length ? state.projects.map((p, i) => `<button class="project-card" data-project="${p.id}"><div class="project-art"><span>${String(i+1).padStart(2,'0')}</span></div><div class="project-meta"><h3>${e(p.name)}</h3><div><span>${e(p.role)} workspace</span><span>${date(p.created)}</span></div></div></button>`).join('') : '<div class="empty">A blank canvas, full of possibility.<br>Create your first project to start collecting footage and ideas.</div>';
  $('project-grid').querySelectorAll('[data-project]').forEach(button => button.onclick = safe(() => openProject(button.dataset.project)))
}
async function openProject(id) {
  if (!confirmDiscard()) return;
  state.project = await api('/api/projects/' + id);
  state.branch = 'main';
  state.dirty = false;
  state.draft = structuredClone(commit().timeline);
  $('dashboard').classList.add('hidden');
  $('studio').classList.remove('hidden');
  renderProject();
  selectTab('editor')
}
async function refresh(keepDraft = false) {
  const draft = state.draft;
  state.project = await projectAPI();
  if (!keepDraft) {
    state.draft = structuredClone(commit().timeline);
    state.dirty = false
  } else state.draft = draft;
  renderProject()
}

function selectTab(tab) {
  state.tab = tab;
  document.querySelectorAll('[data-tab]').forEach(b => b.classList.toggle('selected', b.dataset.tab === tab));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.toggle('hidden', p.id !== 'tab-' + tab))
}

function renderProject() {
  const p = state.project;
  $('project-title').textContent = p.name;
  $('breadcrumb').textContent = 'Workspace / ' + p.name;
  $('branch-select').innerHTML = p.branches.map(b => `<option ${b.name===state.branch?'selected':''}>${e(b.name)}</option>`).join('');
  $('commit-button').disabled = !editable();
  $('new-branch').disabled = !editable();
  $('export-button').disabled = !editable();
  $('upload').disabled = !editable();
  $('team-form').classList.toggle('hidden', p.role !== 'owner');
  renderAssets();
  renderTimeline();
  renderHistory();
  renderMergeSelectors();
  renderReviews();
  renderExports();
  renderTeam()
}

function renderAssets() {
  $('assets').innerHTML = state.project.assets.length ? state.project.assets.map(a => `<div class="asset"><div><strong title="${e(a.name)}">${e(a.name)}</strong><small>${(a.size/1048576).toFixed(1)} MB · ${a.sha.slice(0,8)}</small></div><button data-preview="${a.id}" title="Preview source">▷</button><button data-add="${a.id}" ${editable()?'':'disabled'} title="Add to timeline">＋</button></div>`).join('') : '<div class="empty small">Upload your first video.<br>MP4, MOV, WebM · up to 250 MB</div>';
  $('assets').querySelectorAll('[data-preview]').forEach(b => b.onclick = () => preview(b.dataset.preview));
  $('assets').querySelectorAll('[data-add]').forEach(b => b.onclick = safe(() => addClip(b.dataset.add)))
}

function preview(aid, start = 0) {
  $('preview').src = media(aid);
  $('preview').onloadedmetadata = () => {
    $('preview').currentTime = start
  };
  $('preview-empty').classList.add('hidden');
  $('preview-label').textContent = asset(aid)?.name || 'Source video'
}
async function addClip(aid) {
  const duration = await new Promise(resolve => {
    const video = document.createElement('video');
    video.preload = 'metadata';
    video.onloadedmetadata = () => {
      const d = video.duration;
      video.removeAttribute('src');
      video.load();
      resolve(Number.isFinite(d) ? d : 5)
    };
    video.onerror = () => resolve(5);
    video.src = media(aid)
  });
  state.draft.clips.push({
    id: crypto.randomUUID().replaceAll('-', ''),
    asset: aid,
    start: 0,
    end: Math.min(3600, Math.round(duration * 100) / 100),
    caption: '',
    muted: false
  });
  markDirty();
  renderTimeline();
  preview(aid)
}

function renderTimeline() {
  const clips = state.draft.clips;
  $('clip-count').textContent = clips.length + ' clip' + (clips.length === 1 ? '' : 's');
  $('timeline').innerHTML = clips.length ? clips.map((c, i) => `<div class="clip" data-clip="${c.id}"><div class="clip-number">${i+1}</div><div class="clip-name"><strong>${e(asset(c.asset)?.name||'Unknown asset')}</strong><button data-play="${i}">▷ Preview source</button></div><label>IN<input data-field="start" type="number" min="0" step="0.01" value="${c.start}" ${editable()?'':'disabled'}></label><label>OUT<input data-field="end" type="number" min="0.01" step="0.01" value="${c.end}" ${editable()?'':'disabled'}></label><label>CAPTION<input data-field="caption" maxlength="160" value="${e(c.caption)}" placeholder="Add a line…" ${editable()?'':'disabled'}></label><label>MUTE<input data-field="muted" type="checkbox" ${c.muted?'checked':''} ${editable()?'':'disabled'}></label><div class="actions"><button data-up="${i}" aria-label="Move clip up" ${!editable()||!i?'disabled':''}>↑</button><button data-down="${i}" aria-label="Move clip down" ${!editable()||i===clips.length-1?'disabled':''}>↓</button><button data-remove="${i}" aria-label="Remove clip" ${editable()?'':'disabled'}>×</button></div></div>`).join('') : '<div class="empty">No clips yet. Add footage from the media library to start your timeline.</div>';
  $('dirty-status').textContent = state.dirty ? 'Uncommitted changes — save a commit to keep this version.' : 'All changes committed · ' + head().slice(0, 8);
  $('dirty-status').style.color = state.dirty ? '#963f35' : '';
  $('timeline').querySelectorAll('[data-field]').forEach(input => input.oninput = () => {
    const c = clips.find(c => c.id === input.closest('[data-clip]').dataset.clip);
    c[input.dataset.field] = input.type === 'checkbox' ? input.checked : input.type === 'number' ? Number(input.value) : input.value;
    markDirty()
  });
  $('timeline').querySelectorAll('[data-play]').forEach(b => b.onclick = () => {
    const c = clips[+b.dataset.play];
    preview(c.asset, c.start)
  });
  for (const type of ['up', 'down', 'remove']) $('timeline').querySelectorAll('[data-' + type + ']').forEach(b => b.onclick = () => {
    const i = +b.dataset[type];
    if (type === 'remove') clips.splice(i, 1);
    else {
      const j = i + (type === 'up' ? -1 : 1);
      [clips[i], clips[j]] = [clips[j], clips[i]]
    }
    markDirty();
    renderTimeline()
  })
}

function renderHistory() {
  const commits = [...state.project.commits].sort((a, b) => b.created - a.created);
  $('history').innerHTML = commits.map(c => {
    const branches = state.project.branches.filter(b => b.head === c.id).map(b => b.name);
    return `<article class="history-item"><div class="history-body"><div class="section-heading"><strong>${e(c.message)}</strong><span class="badge">${e(branches.join(', ')||'commit')}</span></div><small>${c.id.slice(0,8)} · ${date(c.created)} · ${c.timeline.clips.length} clips<br>Parents: ${c.parents.map(p=>p.slice(0,8)).join(', ')||'root'}</small><div class="actions"><button data-inspect="${c.id}">Inspect timeline</button><button data-diff="${c.id}" ${c.parents.length?'':'disabled'}>View changes</button><button data-fork="${c.id}" ${editable()?'':'disabled'}>Branch from here</button><button data-restore="${c.id}" ${editable()?'':'disabled'}>Restore to draft</button></div><div id="detail-${c.id}"></div></div></article>`
  }).join('');
  $('history').querySelectorAll('[data-inspect]').forEach(b => b.onclick = () => {
    const c = commit(b.dataset.inspect);
    $('detail-' + c.id).innerHTML = c.timeline.clips.map((clip, i) => `<div class="change"><strong>${i+1}. ${e(asset(clip.asset)?.name)}</strong><p>${clip.start}s → ${clip.end}s · ${clip.muted?'muted':'original audio'}<br>${e(clip.caption)}</p></div>`).join('') || '<p class="muted">Empty timeline</p>'
  });
  $('history').querySelectorAll('[data-diff]').forEach(b => b.onclick = safe(async () => {
    const c = commit(b.dataset.diff);
    const changes = await projectAPI('diff?before=' + c.parents[0] + '&after=' + c.id);
    $('detail-' + c.id).innerHTML = changesHTML(changes)
  }));
  $('history').querySelectorAll('[data-fork]').forEach(b => b.onclick = safe(() => createBranch(b.dataset.fork)));
  $('history').querySelectorAll('[data-restore]').forEach(b => b.onclick = () => {
    if (!confirmDiscard()) return;
    state.draft = structuredClone(commit(b.dataset.restore).timeline);
    markDirty();
    renderTimeline();
    selectTab('editor');
    toast('Earlier revision restored to draft. Commit it to save.');
  })
}

function changesHTML(changes) {
  return changes.length ? changes.map(c => `<div class="change"><strong>${e(c.clip.slice(0,10))} · ${e(c.kind)}</strong><p>${e(JSON.stringify(c.before??'—'))} → ${e(JSON.stringify(c.after??'—'))}</p></div>`).join('') : '<p class="muted">No timeline differences.</p>'
}

function renderMergeSelectors() {
  const previous = $('merge-source').value;
  $('merge-source').innerHTML = state.project.branches.filter(b => b.name !== state.branch).map(b => `<option ${b.name===previous?'selected':''}>${e(b.name)}</option>`).join('');
  $('merge-target').value = state.branch;
  $('compare').disabled = !$('merge-source').value;
  state.merge = null;
  $('merge-result').replaceChildren()
}

function mergeRequest(apply = false, resolutions = {}) {
  return {
    target: state.branch,
    source: $('merge-source').value,
    expected_target: head(),
    expected_source: head($('merge-source').value),
    apply,
    resolutions
  }
}
async function compare() {
  const request = mergeRequest();
  const result = await projectAPI('merge', request);
  state.merge = {
    request,
    result
  };
  const differences = await projectAPI('diff?before=' + head() + '&after=' + head(request.source));
  $('merge-result').innerHTML = `<p class="muted small">Common ancestor ${result.base.slice(0,8)} · ${result.conflicts.length} conflicts. “Ours” is ${e(state.branch)}; “theirs” is ${e(request.source)}.</p>${changesHTML(differences)}${result.conflicts.map((c,i)=>`<div class="conflict"><strong>${e(c.path)}</strong><pre>Ours: ${e(JSON.stringify(c.ours))}\nTheirs: ${e(JSON.stringify(c.theirs))}</pre><label>Keep<select data-resolution="${i}"><option value="">Choose a resolution</option><option value="ours">Ours — ${e(state.branch)}</option><option value="theirs">Theirs — ${e(request.source)}</option></select></label></div>`).join('')}<button class="primary" id="apply-merge" ${editable()?'':'disabled'}>Merge into ${e(state.branch)}</button>`;
  $('apply-merge').onclick = safe(async () => {
    if (!confirmDiscard()) return;
    const resolutions = {};
    for (const input of $('merge-result').querySelectorAll('[data-resolution]')) {
      if (!input.value) throw new Error('Resolve every conflict first');
      resolutions[result.conflicts[+input.dataset.resolution].path] = input.value
    }
    await projectAPI('merge', {
      ...request,
      apply: true,
      resolutions
    });
    await refresh();
    toast('Branches merged. Both parent histories are preserved.');
    selectTab('history')
  })
}

function renderReviews() {
  $('reviews').innerHTML = [...state.project.reviews].sort((a, b) => b.created - a.created).map(r => `<article class="review"><div class="review-head"><span>${e(r.kind.replaceAll('_',' '))} · ${r.at}s · commit ${r.commit_id.slice(0,8)}</span><span>${date(r.created)}</span></div><p>${e(r.body)}</p></article>`).join('') || '<div class="empty">Feedback stays attached to the exact commit it refers to.</div>'
}

function renderExports() {
  state.renderedExportSignature = JSON.stringify(state.project.renders);
  $('exports').innerHTML = [...state.project.renders].sort((a, b) => b.created - a.created).map(r => `<article class="export-card"><div class="section-heading"><div><h3>Export ${r.id.slice(0,8)}</h3><p class="small muted">Commit ${r.commit_id.slice(0,8)} · ${date(r.created)} · ${r.cache_hits} cached segments reused</p></div><span class="badge ${e(r.status)}">${e(r.status)}</span></div>${r.jobs.map(j=>`<div class="job">${j.kind==='segment'?'Clip '+(j.position+1):'Assemble final video'} · ${e(j.status)} · ${j.attempts} attempts${j.error?' · '+e(j.error):''}</div>`).join('')}${r.status==='done'?`<video controls preload="metadata" src="/api/projects/${state.project.id}/renders/${r.id}/media"></video><p><a href="/api/projects/${state.project.id}/renders/${r.id}/media" download="branchframe-${r.id.slice(0,8)}.mp4">Download MP4 ↗</a></p>`:r.status==='failed'?`<p class="small">${e(r.error)}. Fix the source clip or renderer, then submit a new export.</p>`:'<p class="small muted">Workers process this export in the background. Status updates automatically.</p>'}</article>`).join('') || '<div class="empty">Your next finished cut will appear here.<br>Commit your timeline, then request an export.</div>'
}

function renderTeam() {
  $('team').innerHTML = state.project.members.map(m => `<div class="member"><div><strong>${e(m.name)}</strong><small>${e(m.email)}</small></div><span class="badge">${e(m.role)}</span></div>`).join('')
}
async function createBranch(cid = head()) {
  if (!confirmDiscard()) return;
  const name = await ask('Explore a new direction.', 'Create an independent branch from commit ' + cid.slice(0, 8) + '.', 'Branch name');
  if (!name) return;
  await projectAPI('branches', {
    name,
    head: cid
  });
  state.branch = name;
  await refresh();
  toast('Branch created: ' + name)
}
$('auth-toggle').onclick = () => {
  state.register = !state.register;
  $('name-label').classList.toggle('hidden', !state.register);
  $('name').required = state.register;
  $('auth-title').textContent = state.register ? 'Make room for ideas.' : 'Welcome back.';
  $('auth-description').textContent = state.register ? 'Your first great cut starts here.' : 'Pick up where your story left off.';
  $('auth-submit').textContent = state.register ? 'Create account →' : 'Sign in →';
  $('auth-toggle').textContent = state.register ? 'Already have an account? Sign in' : 'New here? Create an account';
  $('password').autocomplete = state.register ? 'new-password' : 'current-password'
};
$('auth-form').onsubmit = safe(async event => {
  event.preventDefault();
  $('auth-submit').disabled = true;
  try {
    await api(state.register ? '/api/register' : '/api/login', {
      email: $('email').value,
      password: $('password').value,
      name: $('name').value
    });
    $('password').value = '';
    await boot()
  } finally {
    $('auth-submit').disabled = false
  }
});
$('logout').onclick = safe(async () => {
  if (!confirmDiscard()) return;
  await api('/api/logout', {});
  state.project = null;
  state.dirty = false;
  showAuth()
});
$('home-button').onclick = safe(async () => {
  if (!confirmDiscard()) return;
  state.project = null;
  state.dirty = false;
  $('dashboard').classList.remove('hidden');
  $('studio').classList.add('hidden');
  $('breadcrumb').textContent = 'Workspace / Projects';
  await loadProjects()
});
$('new-project').onclick = safe(async () => {
  const name = await ask('Start a new story.', 'Give your team’s next project a name.', 'Project name');
  if (!name) return;
  const result = await api('/api/projects', {
    name
  });
  await loadProjects();
  await openProject(result.id)
});
$('branch-select').onchange = () => {
  const branch = $('branch-select').value;
  if (!confirmDiscard()) {
    $('branch-select').value = state.branch;
    return
  }
  state.branch = branch;
  state.draft = structuredClone(commit().timeline);
  state.dirty = false;
  renderProject()
};
$('new-branch').onclick = safe(() => createBranch());
$('commit-button').onclick = safe(async () => {
  const message = await ask('Keep this version.', 'Your commit saves the current timeline on ' + state.branch + '.', 'Commit message');
  if (!message) return;
  await projectAPI('commits', {
    branch: state.branch,
    expected_head: head(),
    timeline: state.draft,
    message
  });
  await refresh();
  toast('Changes committed.')
});
async function uploadVideo(file, projectId) {
  const base = '/api/projects/' + projectId;
  const uploadAPI = (path, data) => api(base + '/' + path, data);
  if (file.size > 250 * 1048576) throw new Error('Choose a video under 250 MB');
  const hash = async buffer => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', buffer)), b => b.toString(16).padStart(2, '0')).join('');
  const fingerprint = await hash(new TextEncoder().encode(file.name + ':' + file.size + ':' + file.lastModified));
  const session = await uploadAPI('uploads', {
    name: file.name,
    size: file.size,
    fingerprint
  });
  const status = await uploadAPI('uploads/' + session.id);
  const total = Math.ceil(file.size / session.part_size);
  for (let i = 0; i < total; i++) {
    const block = await file.slice(i * session.part_size, Math.min(file.size, (i + 1) * session.part_size)).arrayBuffer();
    const sha = await hash(block);
    if (status.received[String(i)] !== sha) {
      let success = false;
      for (let attempt = 0; attempt < 3 && !success; attempt++) {
        try {
          const response = await fetch(base + '/uploads/' + session.id + '/parts/' + i, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/octet-stream',
              'X-Part-SHA256': sha
            },
            body: block
          });
          const value = await response.json();
          if (!response.ok) throw new Error(value.error);
          success = true
        } catch (error) {
          if (attempt === 2) throw new Error(error.message + '. Select the same file again to resume.')
        }
      }
    }
    const percent = Math.round((i + 1) / total * 100);
    $('upload-status').textContent = 'Uploading ' + file.name + ' · ' + percent + '%';
    $('upload-progress').firstElementChild.style.width = percent + '%';
  }
  await uploadAPI('uploads/' + session.id + '/complete', {});
}
$('upload').onchange = safe(async () => {
  const file = $('upload').files[0];
  if (!file) return;
  const projectId = state.project.id;
  state.uploading = true;
  $('upload').disabled = true;
  $('upload-status').textContent = 'Preparing ' + file.name + '…';
  $('upload-progress').classList.remove('hidden');
  try {
    await uploadVideo(file, projectId);
    await refresh(true);
    $('upload-status').textContent = 'Upload complete. Your footage is ready.';
    toast('Footage added to your library.')
  } catch (error) {
    $('upload-status').textContent = error.message;
    throw error;
  } finally {
    state.uploading = false;
    $('upload-progress').classList.add('hidden');
    $('upload').value = '';
    $('upload').disabled = !editable()
  }
});
$('export-button').onclick = safe(async () => {
  if (state.dirty) throw new Error('Commit your changes before exporting');
  await projectAPI('renders', {
    commit: head()
  });
  await refresh(true);
  selectTab('exports');
  toast('Export queued. A renderer will pick it up.')
});
$('compare').onclick = safe(compare);
$('merge-source').onchange = () => {
  state.merge = null;
  $('merge-result').replaceChildren()
};
$('review-form').onsubmit = safe(async event => {
  event.preventDefault();
  await projectAPI('reviews', {
    commit: head(),
    kind: $('review-kind').value,
    body: $('review-body').value,
    at: Number($('review-at').value)
  });
  $('review-body').value = '';
  await refresh(true);
  toast('Review posted on commit ' + head().slice(0, 8))
});
$('team-form').onsubmit = safe(async event => {
  event.preventDefault();
  await projectAPI('members', {
    email: $('team-email').value,
    role: $('team-role').value
  });
  $('team-email').value = '';
  await refresh(true);
  toast('Teammate added.')
});
$('refresh-exports').onclick = safe(() => refresh(true));
document.querySelectorAll('[data-tab]').forEach(button => button.onclick = () => selectTab(button.dataset.tab));
window.addEventListener('beforeunload', event => {
  if (state.dirty || state.uploading) {
    event.preventDefault();
    event.returnValue = ''
  }
});
setInterval(async () => {
  if (state.user && state.project && state.tab === 'exports' && !document.hidden) {
    try {
      const updated = await projectAPI();
      if (!state.project || updated.id !== state.project.id) return;
      state.project = updated;
      const playing = Array.from($('exports').querySelectorAll('video')).some(v => !v.paused);
      if (!playing && state.renderedExportSignature !== JSON.stringify(updated.renders)) renderExports();
    } catch (error) {
      console.warn(error.message)
    }
  }
}, 4000);
if (new URLSearchParams(location.search).get('auth') === 'register') $('auth-toggle').click();
boot();
