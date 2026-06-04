/**
 * Factory Canvas Animation Engine
 *
 * Renders animated factory floor using Canvas 2D.
 * Single EventSource drives both canvas sprites AND DOM updates.
 * Sprite movement is interpolated directly in the animation loop (no anime.js needed).
 * Station positions are proportional — canvas adapts to any size.
 */

// --------------- Station layout (normalized 0..1) ---------------

const STATIONS = [
  { id: 'printer',          x: 0.16, y: 0.55, label: 'PRINTER' },
  { id: 'heat_press',       x: 0.36, y: 0.55, label: 'HEAT PRESS' },
  { id: 'quality_control',  x: 0.56, y: 0.55, label: 'QC' },
  { id: 'packaging',        x: 0.76, y: 0.55, label: 'PACKAGING' },
];

const CONVEYOR_Y = 0.68;
const SPRITE_SPEED = 0.03;  // interpolation speed per frame

// --------------- State ---------------

let sprites = {};          // orderId -> { x, y, design, status }
let stationStatus = {};    // stationId -> 'idle'|'busy'|'failed'
let effects = [];          // [{ x, y, type, timer }]

// Cache order metadata from events (design_name is sent with routing/station_start)
let orderMeta = {};        // orderId -> { design: 'dragon', ... }

// --------------- Init ---------------

function initFactoryCanvas(canvasId, sseUrl) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;

  const ctx = canvas.getContext('2d');

  function resize() {
    const wrap = canvas.parentElement;
    canvas.width = wrap.clientWidth * devicePixelRatio;
    canvas.height = wrap.clientHeight * devicePixelRatio;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
  }
  resize();
  window.addEventListener('resize', resize);

  let done = false;
  const es = new EventSource(sseUrl);

  // --- Event handlers ---

  es.addEventListener('plan', (e) => {
    const data = JSON.parse(e.data);
    updateQueue(data.queue || []);
    updateReason(data.reason || '');
    updateStat('stat-replan', data.re_plan_count || 0);
    logActivity(`📋 Plan #${data.re_plan_count}: ${data.reason}`);
  });

  es.addEventListener('stats', (e) => {
    const data = JSON.parse(e.data);
    updateStat('stat-completed', data.completed || 0);
    updateStat('stat-iteration', data.iteration || 0);
    updateStat('stat-replan', data.re_plan_count || 0);
  });

  es.addEventListener('routing', (e) => {
    const data = JSON.parse(e.data);
    cacheOrderMeta(data.order_id, { design: data.design_name || 'base' });
    logActivity(`🧭 ${data.order_id}: routing — ${(data.reason||'').slice(0, 80)}`);

    sprites[data.order_id] = {
      x: 0.02,
      y: CONVEYOR_Y,
      targetX: 0.02,
      targetY: CONVEYOR_Y,
      design: data.design_name || 'base',
      status: 'routing',
      opacity: 1,
    };
    highlightStations(data.route || []);
  });

  es.addEventListener('station_start', (e) => {
    const data = JSON.parse(e.data);
    cacheOrderMeta(data.order_id, { design: data.design_name || 'base' });
    logActivity(`▶ ${data.order_id}: ${data.station}`);
    stationStatus[data.station] = 'busy';

    const sprite = sprites[data.order_id];
    if (sprite) {
      sprite.design = data.design_name || sprite.design || 'base';
    }

    const station = STATIONS.find(s => s.id === data.station);
    if (station && sprite) {
      sprite.targetX = station.x;
      sprite.status = 'processing';
    }
  });

  es.addEventListener('station_done', (e) => {
    const data = JSON.parse(e.data);
    stationStatus[data.station] = data.success ? 'idle' : 'failed';
    if (!data.success) {
      const st = STATIONS.find(s => s.id === data.station);
      if (st) effects.push({ x: st.x, y: st.y - 0.05, type: 'fire', timer: 80 });
    }
    logActivity(`${data.success ? '✅' : '❌'} ${data.order_id}: ${data.station} ${data.success ? 'done' : 'FAILED'}`);
  });

  es.addEventListener('qc_verdict', (e) => {
    const data = JSON.parse(e.data);
    const st = STATIONS.find(s => s.id === 'quality_control');
    const icon = data.verdict === 'pass' ? 'pass' : (data.verdict === 'rework' ? 'rework' : 'fail');
    if (st) effects.push({ x: st.x, y: st.y - 0.12, type: icon, timer: 60 });
    logActivity(`🔍 ${data.order_id}: QC ${data.verdict.toUpperCase()} — ${data.reason}`);
  });

  es.addEventListener('order_complete', (e) => {
    const data = JSON.parse(e.data);
    logActivity(`📦 ${data.order_id}: COMPLETED!`);
    const sprite = sprites[data.order_id];
    if (sprite) {
      const pkgStation = STATIONS.find(s => s.id === 'packaging');
      sprite.targetX = pkgStation ? pkgStation.x : 0.80;
      sprite.targetY = CONVEYOR_Y + 0.15;
      sprite.opacity = 0;
      sprite.status = 'completed';
    }
  });

  es.addEventListener('done', () => { done = true; es.close(); });
  es.addEventListener('error', () => {
    if (!done) logActivity('⚠️ SSE connection error');
    done = true;
  });

  // --- Animation loop ---

  function draw() {
    const W = canvas.width / devicePixelRatio;
    const H = canvas.height / devicePixelRatio;
    ctx.save();
    ctx.scale(devicePixelRatio, devicePixelRatio);

    // Background
    ctx.fillStyle = '#1a1a2e';
    ctx.fillRect(0, 0, W, H);

    // Floor
    ctx.fillStyle = '#2d2d44';
    ctx.fillRect(0, H * 0.82, W, H * 0.18);
    ctx.strokeStyle = '#444466';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, H * 0.82);
    ctx.lineTo(W, H * 0.82);
    ctx.stroke();

    // Conveyor belt with animated rollers
    const cy = H * CONVEYOR_Y;
    ctx.fillStyle = '#3d3d55';
    ctx.fillRect(0, cy - 5, W, 10);
    ctx.strokeStyle = '#555577';
    ctx.lineWidth = 1;
    ctx.strokeRect(0, cy - 5, W, 10);

    const rollerSpacing = W / 20;
    const offset = (Date.now() / 40) % rollerSpacing;
    ctx.fillStyle = '#5a5a7a';
    for (let i = 0; i <= 20; i++) {
      ctx.beginPath();
      ctx.arc(offset + i * rollerSpacing - rollerSpacing, cy, 4, 0, Math.PI * 2);
      ctx.fill();
    }

    // Station machines
    STATIONS.forEach(st => {
      const sx = W * st.x;
      const sy = H * st.y;
      const status = stationStatus[st.id] || 'idle';

      ctx.fillStyle = status === 'failed' ? '#6b3030' : status === 'busy' ? '#2a3a6b' : '#2d2d44';
      ctx.strokeStyle = status === 'failed' ? '#e94560' : status === 'busy' ? '#4a7fff' : '#444466';
      ctx.lineWidth = 2;
      roundRect(ctx, sx - 45, sy - 55, 90, 90, 10);
      ctx.fill();
      ctx.stroke();

      ctx.fillStyle = '#eee';
      ctx.font = '10px system-ui';
      ctx.textAlign = 'center';
      ctx.fillText(st.label, sx, sy + 55);

      // Status dot
      ctx.beginPath();
      ctx.arc(sx, sy - 60, 5, 0, Math.PI * 2);
      ctx.fillStyle = status === 'failed' ? '#e94560' : status === 'busy' ? '#ffd700' : '#27AE60';
      ctx.fill();
    });

    // Effects (fire, QC icons)
    effects = effects.filter(e => e.timer > 0);
    effects.forEach(e => {
      const ex = W * e.x;
      const ey = H * e.y;
      e.timer--;
      if (e.type === 'fire') {
        ctx.fillStyle = '#e94560';
        ctx.font = `${24 + Math.sin(Date.now()/80)*6}px system-ui`;
        ctx.textAlign = 'center';
        ctx.fillText('🔥', ex, ey);
      } else {
        const emoji = e.type === 'pass' ? '✅' : e.type === 'fail' ? '❌' : '🔧';
        ctx.fillStyle = '#ffd700';
        ctx.font = '22px system-ui';
        ctx.textAlign = 'center';
        ctx.fillText(emoji, ex, ey);
      }
    });

    // T-shirt sprites (interpolate towards targets)
    Object.entries(sprites).forEach(([orderId, sprite]) => {
      // Interpolate position
      if (sprite.targetX !== undefined) {
        sprite.x += (sprite.targetX - sprite.x) * SPRITE_SPEED;
      }
      if (sprite.targetY !== undefined) {
        sprite.y += (sprite.targetY - sprite.y) * SPRITE_SPEED;
      }

      if (sprite.status === 'completed' && sprite.y > CONVEYOR_Y + 0.12) return;
      const sx = W * sprite.x;
      const sy = H * sprite.y;

      ctx.globalAlpha = sprite.opacity !== undefined ? sprite.opacity : 1;

      ctx.fillStyle = getDesignColor(sprite.design);
      ctx.strokeStyle = '#ffffff44';
      ctx.lineWidth = 1;
      roundRect(ctx, sx - 16, sy - 18, 32, 32, 5);
      ctx.fill();
      ctx.stroke();

      ctx.fillStyle = '#eee';
      ctx.font = '8px system-ui';
      ctx.textAlign = 'center';
      ctx.fillText(orderId, sx, sy + 26);

      ctx.globalAlpha = 1;
    });

    ctx.restore();

    if (!done || Object.values(sprites).some(s => s.status !== 'completed')) {
      requestAnimationFrame(draw);
    } else {
      setTimeout(() => {
        ctx.save();
        ctx.scale(devicePixelRatio, devicePixelRatio);
        ctx.fillStyle = 'rgba(0,0,0,0.6)';
        ctx.fillRect(0, 0, W, H);
        ctx.fillStyle = '#27AE60';
        ctx.font = 'bold 32px system-ui';
        ctx.textAlign = 'center';
        ctx.fillText('✅ Simulation Complete!', W/2, H/2);
        ctx.restore();
      }, 800);
    }
  }

  requestAnimationFrame(draw);
}

// --------------- DOM helpers ---------------

function updateQueue(queue) {
  const el = document.getElementById('queue-list');
  if (!el) return;
  el.innerHTML = queue.map(id => {
    const meta = orderMeta[id] || {};
    return `<span class="order">${id}</span>`;
  }).join(' ');
}

function updateReason(text) {
  const el = document.getElementById('reason-text');
  if (el) el.textContent = text;
}

function updateStat(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function logActivity(msg) {
  const el = document.getElementById('log-entries');
  if (!el) return;
  const div = document.createElement('div');
  div.className = 'log-entry';
  div.textContent = new Date().toLocaleTimeString() + ' ' + msg;
  el.prepend(div);
  while (el.children.length > 100) el.lastChild.remove();
}

function cacheOrderMeta(orderId, meta) {
  if (!orderMeta[orderId]) orderMeta[orderId] = {};
  Object.assign(orderMeta[orderId], meta);
}

// --------------- Canvas helpers ---------------

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

function getDesignColor(design) {
  const colors = {
    base: '#ECF0F1', dragon: '#2C3E50', unicorn: '#FDEBD0',
    cyberpunk: '#1B1B2F', minimal: '#FFFFFF', retro: '#D4C5A9',
    floral: '#F5EEF8', geometric: '#EBF5FB',
  };
  return colors[design] || '#ECF0F1';
}

function highlightStations(route) {
  route.forEach((step, i) => {
    setTimeout(() => {
      stationStatus[step.station] = step.required ? 'busy' : 'idle';
      if (!step.required) {
        setTimeout(() => { stationStatus[step.station] = 'idle'; }, 300);
      }
    }, i * 150);
  });
}

// --------------- Auto-init (runs after DOM ready) ---------------

(function() {
  const canvas = document.getElementById('factory-canvas');
  if (!canvas) return;

  let sseUrl;
  const path = window.location.pathname;

  if (path.startsWith('/replay/')) {
    const threadId = path.split('/').pop();
    document.getElementById('thread-id') && (document.getElementById('thread-id').textContent = threadId);
    sseUrl = '/stream/' + threadId;
  } else {
    const params = new URLSearchParams(window.location.search);
    sseUrl = '/stream?order_count=' + (params.get('order_count') || '10')
      + '&urgent_ratio=' + (params.get('urgent_ratio') || '0.3');
  }

  initFactoryCanvas('factory-canvas', sseUrl);
})();
