'use strict';

// Dashboard-only interactions. The hydraulic model and investigation state live in app.js.
(() => {
  const get = id => document.getElementById(id);
  const body = document.body;
  let zoom = 1;

  function syncSidebar() {
    const open = body.classList.contains('sidebar-open');
    const toggle = get('sidebarToggle');
    if (toggle) toggle.setAttribute('aria-expanded', String(open));
    const expand = get('sidebarExpand');
    if (expand) {
      expand.setAttribute('aria-expanded', String(body.classList.contains('sidebar-wide')));
      expand.textContent = body.classList.contains('sidebar-wide') ? '⤡' : '⤢';
    }
    const backdrop = get('sidebarBackdrop');
    if (backdrop) backdrop.hidden = !open;
  }

  function setView(view) {
    const overview = view === 'overview';
    if (overview) {
      body.classList.remove('sidebar-open', 'sidebar-wide');
      syncSidebar();
    }
    get('overview').hidden = !overview;
    get('dashboard').hidden = overview;
    get('appSidebar').hidden = overview;
    get('overviewToggle').textContent = overview ? 'Open dashboard' : 'Project overview';
    body.classList.toggle('overview-mode', overview);
    window.scrollTo({ top: 0, behavior: 'instant' });
  }

  function setZoom(next) {
    zoom = Math.max(0.75, Math.min(2.5, Math.round(next * 100) / 100));
    window.applyNetworkZoom();
  }

  window.applyNetworkZoom = function () {
    const map = get('networkMap');
    const svg = map?.querySelector('svg');
    if (svg) {
      svg.style.width = `${zoom * 100}%`;
      svg.style.minWidth = '0';
    }
    get('mapZoomValue').textContent = `${Math.round(zoom * 100)}%`;
    get('zoomOut').disabled = zoom <= 0.75;
    get('zoomIn').disabled = zoom >= 2.5;
  };

  window.renderSelectedInspector = function () {
    const pane = get('networkInspector');
    if (!pane || !state.topology) return;
    const selected = state.selected;
    if (!selected) {
      pane.innerHTML = '<p class="eyebrow">ELEMENT DETAILS</p><h3>Select a pipe or junction</h3><p>Click a point or connection on the map to compare its healthy and observed readings.</p>';
      return;
    }
    const isEdge = selected.kind === 'flow' || selected.kind === 'pump';
    const item = (isEdge ? state.topology.edges : state.topology.nodes).find(entry => String(entry.data.id) === String(selected.id));
    if (!item) return;
    const kind = isEdge ? 'flow' : 'pressure';
    const title = item.data.name || label(selected.kind, selected.id);
    const elementType = isEdge ? (selected.kind === 'pump' ? 'Pump' : 'Pipe') : selected.kind === 'pressure' ? 'Junction' : selected.kind === 'tank' ? 'Tank' : 'Water source';
    const baseline = state.baseline?.[kind === 'flow' ? 'flows' : 'pressures']?.[selected.id];
    const observed = state.readings?.[kind === 'flow' ? 'flows' : 'pressures']?.[selected.id];
    const canMeasure = isEdge ? selected.kind === 'flow' : selected.kind === 'pressure';
    const delta = Number.isFinite(baseline) && Number.isFinite(observed) ? observed - baseline : null;
    const ranked = state.report?.ranked_faults || [];
    const rank = ranked.findIndex(entry => String(entry.candidate?.location_id) === String(selected.id) && (isEdge ? entry.candidate?.fault_type !== 'demand_surge' : entry.candidate?.fault_type === 'demand_surge'));
    const endpoint = isEdge ? `<div class="inspector-endpoints">${html(friendly(item.data.source) || `Point ${item.data.source}`)} → ${html(friendly(item.data.target) || `Point ${item.data.target}`)}</div>` : '';
    const metrics = canMeasure ? `<div class="inspector-metrics"><div class="metric"><span>Healthy baseline</span><b>${fmt(baseline, kind)}</b></div><div class="metric"><span>Observed</span><b>${fmt(observed, kind)}</b></div><div class="metric"><span>Change</span><b>${delta === null ? '—' : `${delta > 0 ? '+' : ''}${fmt(delta, kind)}`}</b></div></div>` : '<p class="inspector-note">This element has no direct sensor comparison in the current model.</p>';
    pane.innerHTML = `<p class="eyebrow">ELEMENT DETAILS</p><span class="kind-tag">${html(elementType)}</span><h3>${html(title)}</h3><p class="inspector-id">ID ${html(selected.id)}</p>${endpoint}${metrics}${rank >= 0 ? `<p class="inspector-rank">Ranked hypothesis #${rank + 1}: ${html(cause(ranked[rank].candidate.fault_type))}</p>` : ''}`;
  };

  get('sidebarToggle')?.addEventListener('click', () => {
    const willOpen = !body.classList.contains('sidebar-open');
    if (willOpen) {
      body.classList.add('sidebar-open', 'sidebar-wide');
    } else {
      body.classList.remove('sidebar-open', 'sidebar-wide');
    }
    syncSidebar();
  });
  get('sidebarExpand')?.addEventListener('click', () => {
    body.classList.add('sidebar-open');
    body.classList.toggle('sidebar-wide');
    syncSidebar();
  });
  get('sidebarBackdrop')?.addEventListener('click', () => {
    body.classList.remove('sidebar-open', 'sidebar-wide');
    syncSidebar();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && body.classList.contains('sidebar-open')) {
      body.classList.remove('sidebar-open', 'sidebar-wide');
      syncSidebar();
    }
  });
  get('overviewToggle')?.addEventListener('click', () => setView(get('overview').hidden ? 'overview' : 'dashboard'));
  get('openDashboard')?.addEventListener('click', () => setView('dashboard'));
  get('brandHome')?.addEventListener('click', () => setView('dashboard'));
  
  // Single setup button adjacent to overview
  const triggerSetup = () => {
    setView('dashboard');
    const onboard = get('onboarding');
    if (onboard) {
      onboard.hidden = !onboard.hidden;
      if (!onboard.hidden) {
        onboard.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    }
    body.classList.remove('sidebar-open', 'sidebar-wide');
    syncSidebar();
  };
  get('topbarSetupBtn')?.addEventListener('click', triggerSetup);
  get('openOnboarding')?.addEventListener('click', triggerSetup);
  get('closeSetupBtn')?.addEventListener('click', () => {
    const onboard = get('onboarding');
    if (onboard) onboard.hidden = true;
  });

  get('readyPanel')?.querySelector('a[href="#setup"]')?.addEventListener('click', event => {
    event.preventDefault();
    setView('dashboard');
    const onboard = get('onboarding');
    if (onboard) onboard.hidden = true;
    body.classList.add('sidebar-open', 'sidebar-wide');
    syncSidebar();
    get('setup')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
  get('baselineMetric')?.addEventListener('change', event => {
    document.querySelectorAll('[data-metric-panel]').forEach(panel => {
      panel.hidden = panel.dataset.metricPanel !== event.target.value;
    });
  });
  get('zoomOut')?.addEventListener('click', () => setZoom(zoom - 0.25));
  get('zoomIn')?.addEventListener('click', () => setZoom(zoom + 0.25));
  get('zoomReset')?.addEventListener('click', () => setZoom(1));
  syncSidebar();
  window.applyNetworkZoom();
  window.renderSelectedInspector();
})();
