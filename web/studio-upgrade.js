/* Shared drafts, review policy, multitrack controls, and account recovery UI. */
(() => {
  let revision = 0,
    stream = null,
    pending = false,
    saving = null,
    timer = null,
    version = 0,
    conflict = false,
    context = '';
  const signature = t => JSON.stringify(t, (key, value) => value && typeof value === 'object' && !Array.isArray(value) ? Object.fromEntries(Object.keys(value).sort().map(k => [k, value[k]])) : value);

  function changedPreview() {
    if (state.project) window.dispatchEvent(new CustomEvent('timeline-change', {
      detail: {
        timeline: structuredClone(state.draft),
        project: state.project.id
      }
    }));
  }

  function status(message, error = false) {
    $('collaboration-status').textContent = message;
    $('collaboration-status').classList.toggle('warning', error);
  }
  async function shared() {
    const pid = state.project.id,
      branch = state.branch;
    const value = await projectAPI('draft?branch=' + encodeURIComponent(branch));
    if (context !== pid + '/' + branch) return;
    revision = value.revision;
    state.draft = structuredClone(value.timeline);
    state.dirty = signature(state.draft) !== signature(commit().timeline);
    pending = false;
    renderTimeline();
    status('Shared draft synchronized · revision ' + revision);
  }
  async function flush() {
    clearTimeout(timer);
    if (conflict) throw new Error('Resolve the live draft conflict before committing.');
    if (saving) {
      await saving;
      if (pending) return flush();
      return;
    }
    if (!pending || !state.project || !editable()) return;
    const pid = state.project.id,
      branch = state.branch,
      sent = structuredClone(state.draft),
      sentVersion = version;
    saving = (async () => {
      try {
        const value = await api('/api/projects/' + pid + '/draft', {
          branch,
          revision,
          timeline: sent
        });
        if (context !== pid + '/' + branch) return;
        revision = value.revision;
        if (version === sentVersion) {
          const merged = signature(value.timeline) !== signature(sent);
          if (merged) state.draft = value.timeline;
          pending = false;
          state.dirty = signature(state.draft) !== signature(commit().timeline);
          // Keep focused inputs and their draft-object bindings during ordinary saves.
          if (merged) renderTimeline();
          status('Shared draft saved · revision ' + revision);
        } else if (signature(value.timeline) !== signature(sent)) {
          conflict = true;
          $('live-conflict').classList.remove('hidden');
          status('A teammate changed this draft while you were typing. Your local work is preserved.', true);
        } else timer = setTimeout(() => flush().catch(() => {}), 600);
      } catch (error) {
        conflict = true;
        $('live-conflict').classList.remove('hidden');
        status(error.message, true);
        throw error;
      } finally {
        saving = null;
      }
    })();
    return saving;
  }

  function change() {
    version++;
    pending = true;
    changedPreview();
    if (!context || !editable() || conflict) return;
    status('Saving shared draft…');
    clearTimeout(timer);
    timer = setTimeout(() => flush().catch(() => {}), 650);
  }

  function close() {
    window.dispatchEvent(new Event('preview-stop'));
    stream?.close();
    stream = null;
    clearTimeout(timer);
    context = '';
    pending = false;
    conflict = false;
  }
  async function open() {
    close();
    if (!state.project) return;
    context = state.project.id + '/' + state.branch;
    conflict = false;
    $('live-conflict').classList.add('hidden');
    await shared();
    const pid = state.project.id,
      branch = state.branch;
    stream = new EventSource('/api/projects/' + pid + '/events?branch=' + encodeURIComponent(branch));
    stream.addEventListener('draft', async event => {
      if (context !== pid + '/' + branch) return;
      const value = JSON.parse(event.data);
      $('presence').textContent = value.presence.map(p => p.name).join(', ') + (value.presence.length ? ' · online' : '');
      try {
        if (value.head !== head() || signature(value.approval) !== signature(state.project.approval?.[head()])) await refresh(true);
        if (!pending && !saving && !conflict && value.revision > revision) await shared();
      } catch (error) {
        status(error.message, true);
      }
    });
    stream.onerror = () => status('Live connection reconnecting. Your edits remain in this browser.', true);
  }

  function advanced() {
    if (!state.project) return;
    changedPreview();
    const clips = state.draft.clips;
    $('timeline').querySelectorAll('.clip-options').forEach(n => n.remove());
    $('timeline').querySelectorAll('[data-clip]').forEach(row => {
      const c = clips.find(c => c.id === row.dataset.clip);
      const options = document.createElement('div');
      options.className = 'clip-options';
      options.innerHTML = `<label>TRACK<select data-extra="track">${[0,1,2,3].map(t=>`<option value="${t}" ${+(c.track??0)===t?'selected':''}>${t===0?'0 · main video':t+' · overlay'}</option>`).join('')}</select></label><label>TRANSITION<select data-extra="transition"><option value="cut">Cut</option><option value="fade" ${c.transition==='fade'?'selected':''}>Crossfade</option></select></label><label>FADE (s)<input data-extra="transition_duration" type="number" min="0.05" max="2" step="0.05" value="${c.transition_duration??.5}"></label><label>VOLUME<input data-extra="volume" type="number" min="0" max="2" step="0.1" value="${c.volume??1}"></label>${c.track?`<label>AT (s)<input data-extra="at" type="number" min="0" max="3600" step="0.1" value="${c.at??0}"></label>${['x','y','width','height'].map((key,i)=>`<label>${key.toUpperCase()}<input data-extra="${key}" type="number" min="${i>1?.01:0}" max="1" step="0.05" value="${c[key]??[.65,.05,.3,.3][i]}"></label>`).join('')}`:''}`;
      row.insertAdjacentElement('afterend', options);
      options.querySelectorAll('[data-extra]').forEach(input => {
        input.disabled = !editable();
        input.onchange = () => {
          c[input.dataset.extra] = ['track', 'transition_duration', 'volume', 'at', 'x', 'y', 'width', 'height'].includes(input.dataset.extra) ? Number(input.value) : input.value;
          markDirty();
          renderTimeline();
        };
      });
    });
    const audio = state.draft.audio ?? [];
    $('audio-timeline').innerHTML = audio.map(a => `<div class="music-clip" data-music="${a.id}"><strong>${e(asset(a.asset)?.name||'Audio')}</strong>${['start','end','at','volume','fade_in','fade_out'].map(key=>`<label>${key.replaceAll('_',' ').toUpperCase()}<input data-audio-field="${key}" type="number" min="0" max="${key==='volume'?2:3600}" step="0.1" value="${a[key]??0}"></label>`).join('')}<button data-remove-music="${a.id}" aria-label="Remove audio">×</button></div>`).join('') || '<p class="small muted">Add a music or voiceover file from the media library.</p>';
    $('audio-timeline').querySelectorAll('[data-audio-field]').forEach(input => {
      input.disabled = !editable();
      input.onchange = () => {
        const a = audio.find(a => a.id === input.closest('[data-music]').dataset.music);
        a[input.dataset.audioField] = Number(input.value);
        markDirty();
      };
    });
    $('audio-timeline').querySelectorAll('[data-remove-music]').forEach(b => {
      b.disabled = !editable();
      b.onclick = () => {
        state.draft.audio = audio.filter(a => a.id !== b.dataset.removeMusic);
        markDirty();
        renderTimeline();
      };
    });
    const gate = state.project.approval?.[head()];
    $('approval-status').textContent = gate?.required ? (gate.allowed ? '✓ Independently approved for export.' : 'Export requires an independent approval with no unresolved change requests.') : 'Approval gate is disabled for this project.';
    $('export-button').disabled = !editable() || !!(gate?.required && !gate.allowed);
    $('require-approval').checked = !!state.project.require_approval;
    $('policy-form').classList.toggle('hidden', state.project.role !== 'owner');
  }
  async function addAudio(aid) {
    const el = document.createElement('audio');
    el.preload = 'metadata';
    const duration = await new Promise((resolve, reject) => {
      el.onloadedmetadata = () => resolve(el.duration);
      el.onerror = () => reject(new Error('Could not read this audio file'));
      el.src = media(aid);
    });
    el.removeAttribute('src');
    el.load();
    if (!Number.isFinite(duration)) throw new Error('Audio duration is unavailable');
    state.draft.audio ??= [];
    state.draft.audio.push({
      id: crypto.randomUUID().replaceAll('-', ''),
      asset: aid,
      start: 0,
      end: Math.min(duration, 3600),
      at: 0,
      volume: .25,
      fade_in: 0,
      fade_out: 0
    });
    markDirty();
    renderTimeline();
  }
  $('reload-shared').onclick = safe(async () => {
    conflict = false;
    await shared();
    $('live-conflict').classList.add('hidden');
  });
  $('save-recovery').onclick = safe(async () => {
    const local = structuredClone(state.draft),
      name = 'recovery/' + Date.now().toString(36),
      base = head();
    await projectAPI('branches', {
      name,
      head: base
    });
    await projectAPI('commits', {
      branch: name,
      expected_head: base,
      timeline: local,
      message: 'Preserve conflicting live edits'
    });
    state.branch = name;
    state.dirty = false;
    pending = false;
    conflict = false;
    await refresh();
    toast('Your edits were preserved on ' + name);
  });
  $('policy-form').onsubmit = safe(async event => {
    event.preventDefault();
    await projectAPI('settings', {
      require_approval: $('require-approval').checked
    });
    await refresh(true);
    toast('Export review policy updated.');
  });
  $('forgot-password').onclick = safe(async () => {
    const email = await ask('A fresh start.', 'Enter your account email to receive a password reset link.', 'Email address', $('email').value);
    if (!email) return;
    const result = await api('/api/request-password-reset', {
      email
    });
    toast(result.message);
  });
  $('resend-verification').onclick = safe(async () => {
    const result = await api('/api/request-verification', {});
    toast(result.message);
  });
  window.BranchFrameUpgrade = {
    open,
    close,
    change,
    flush,
    advanced,
    addAudio,
    preview: changedPreview,
    account() {
      const visible = state.user && !state.user.email_verified && state.user.email_delivery;
      $('verify-banner').classList.toggle('hidden', !visible);
    }
  };
})();
