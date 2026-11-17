/* Live canvas composition of the same trimmed clips used by the export pipeline. */
(() => {
  const canvas = document.getElementById('composite');
  if (!canvas) return;
  const ctx = canvas.getContext('2d'),
    media = new Map();
  let timeline = {
      clips: []
    },
    project = '',
    time = 0,
    playing = false,
    last = 0,
    audioContext = null;
  const gains = new WeakMap();
  const play = document.getElementById('preview-play'),
    seek = document.getElementById('preview-seek'),
    clock = document.getElementById('preview-clock');
  const schedule = () => {
    let end = 0;
    const base = [];
    for (const c of timeline.clips.filter(c => !c.track)) {
      const d = c.end - c.start;
      if (!Number.isFinite(d) || d <= 0) continue;
      const overlap = base.length && c.transition === 'fade' ? Math.min(c.transition_duration ?? .5, d / 2, base.at(-1).duration / 2) : 0;
      const at = end - overlap;
      base.push({
        clip: c,
        at,
        duration: d,
        overlap
      });
      end = at + d;
    }
    return {
      base,
      duration: end
    };
  };

  function element(c, audio = false) {
    const key = (audio ? 'a:' : 'v:') + c.id;
    const source = '/api/projects/' + project + '/assets/' + c.asset + '/media';
    if (media.has(key) && media.get(key).getAttribute('src') !== source) {
      const old = media.get(key);
      old.pause();
      old.removeAttribute('src');
      old.load();
      media.delete(key);
    }
    if (!media.has(key)) {
      const el = document.createElement(audio ? 'audio' : 'video');
      el.preload = 'auto';
      el.crossOrigin = 'use-credentials';
      el.playsInline = true;
      el.src = source;
      el.addEventListener('loadeddata', () => {
        if (!playing) draw();
      });
      el.addEventListener('seeked', () => {
        if (!playing) draw();
      });
      media.set(key, el);
    }
    return media.get(key);
  }

  function sync(el, at, volume) {
    if (Math.abs(el.currentTime - at) > (playing ? .18 : .015)) {
      try {
        el.currentTime = Math.max(0, at);
      } catch {}
    }
    if (audioContext) {
      if (!gains.has(el)) {
        const node = audioContext.createMediaElementSource(el),
          gain = audioContext.createGain();
        node.connect(gain);
        gain.connect(audioContext.destination);
        gains.set(el, gain);
      }
      gains.get(el).gain.value = Math.max(0, volume);
      el.volume = 1;
    } else el.volume = Math.min(1, Math.max(0, volume));
    if (playing && el.paused) el.play().catch(() => {
      document.getElementById('preview-note').textContent = 'Your browser blocked preview audio. Press Play again; exports still include the soundtrack.';
    });
    if (!playing && !el.paused) el.pause();
  }

  function frame(c, t, alpha = 1, overlay = false, audioAlpha = alpha) {
    const el = element(c);
    sync(el, c.start + t, c.muted ? 0 : (c.volume ?? 1) * audioAlpha);
    if (el.readyState < 2) return;
    const x = overlay ? (c.x ?? .65) * 1280 : 0,
      y = overlay ? (c.y ?? .05) * 720 : 0,
      w = overlay ? (c.width ?? .3) * 1280 : 1280,
      h = overlay ? (c.height ?? .3) * 720 : 720;
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.fillStyle = '#000';
    ctx.fillRect(x, y, w, h);
    const ratio = Math.min(w / el.videoWidth, h / el.videoHeight),
      vw = el.videoWidth * ratio,
      vh = el.videoHeight * ratio;
    ctx.drawImage(el, x + (w - vw) / 2, y + (h - vh) / 2, vw, vh);
    if (c.caption) {
      ctx.font = Math.round(32 * w / 1280) + 'px sans-serif';
      ctx.textAlign = 'center';
      const tw = ctx.measureText(c.caption).width;
      const cy = y + h - 40 * h / 720;
      ctx.fillStyle = 'rgba(0,0,0,.6)';
      ctx.fillRect(x + (w - tw) / 2 - 12, cy - 34 * w / 1280, tw + 24, 45 * w / 1280);
      ctx.fillStyle = '#fff';
      ctx.fillText(c.caption, x + w / 2, cy);
    }
    ctx.restore();
  }

  function draw() {
    const s = schedule(),
      active = new Set();
    ctx.fillStyle = '#151515';
    ctx.fillRect(0, 0, 1280, 720);
    for (let index = 0; index < s.base.length; index++) {
      const entry = s.base[index];
      if (time >= entry.at && time < entry.at + entry.duration) {
        let alpha = entry.overlap ? Math.min(1, (time - entry.at) / entry.overlap) : 1;
        const next = s.base[index + 1];
        let audioAlpha = alpha;
        if (next?.overlap && time >= next.at) audioAlpha *= Math.max(0, 1 - (time - next.at) / next.overlap);
        frame(entry.clip, time - entry.at, alpha, false, audioAlpha);
        active.add('v:' + entry.clip.id);
      }
    }
    for (const c of timeline.clips.filter(c => c.track).sort((a, b) => a.track - b.track)) {
      const at = c.at ?? 0;
      if (time >= at && time < at + c.end - c.start) {
        frame(c, time - at, 1, true);
        active.add('v:' + c.id);
      }
    }
    for (const a of timeline.audio ?? []) {
      if (time >= a.at && time < a.at + a.end - a.start) {
        const t = time - a.at,
          d = a.end - a.start;
        let volume = a.volume;
        if (a.fade_in) volume *= Math.min(1, t / a.fade_in);
        if (a.fade_out) volume *= Math.min(1, (d - t) / a.fade_out);
        sync(element(a, true), a.start + t, volume);
        active.add('a:' + a.id);
      }
    }
    for (const [key, el] of media)
      if (!active.has(key) && !el.paused) el.pause();
    seek.max = s.duration || 1;
    seek.value = time;
    clock.textContent = time.toFixed(1) + ' / ' + s.duration.toFixed(1) + 's';
    play.disabled = !s.base.length;
  }

  function tick(now) {
    if (!playing) return;
    time += Math.min(.15, (now - last) / 1000);
    last = now;
    const end = schedule().duration;
    if (time >= end) {
      time = 0;
      playing = false;
      play.textContent = '▶ Play edited timeline';
    }
    draw();
    if (playing) requestAnimationFrame(tick);
  }
  play.onclick = () => {
    if (!audioContext) {
      const Constructor = window.AudioContext || window.webkitAudioContext;
      if (Constructor) audioContext = new Constructor();
    }
    audioContext?.resume();
    playing = !playing;
    play.textContent = playing ? 'Ⅱ Pause' : '▶ Play edited timeline';
    last = performance.now();
    draw();
    if (playing) requestAnimationFrame(tick);
  };
  seek.oninput = () => {
    time = +seek.value;
    draw();
  };
  window.addEventListener('timeline-change', event => {
    const changed = project !== event.detail.project;
    if (changed) {
      playing = false;
      play.textContent = '▶ Play edited timeline';
      time = 0;
      for (const el of media.values()) {
        el.pause();
        el.removeAttribute('src');
        el.load();
      }
      media.clear();
    }
    project = event.detail.project;
    timeline = event.detail.timeline;
    const retained = new Set([...timeline.clips.map(c => 'v:' + c.id), ...(timeline.audio ?? []).map(c => 'a:' + c.id)]);
    for (const [key, el] of media) {
      if (!retained.has(key)) {
        el.pause();
        el.removeAttribute('src');
        el.load();
        media.delete(key);
      }
    }
    time = Math.min(time, Math.max(0, schedule().duration - .01));
    draw();
  });
  window.addEventListener('preview-stop', () => {
    playing = false;
    play.textContent = '▶ Play edited timeline';
    for (const el of media.values()) el.pause();
  });
  window.addEventListener('beforeunload', () => {
    for (const el of media.values()) el.pause();
  });
  draw();
})();
