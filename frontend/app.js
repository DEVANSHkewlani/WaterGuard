'use strict';

const state = {
  networkName: 'Net2',
  topology: null, baseline: null, readings: {pressures:{},flows:{}},
  scenario: null, series: null, report: null, truth: null, events: [],
  selected: null, running: false, runError: null, connected: false, tracing: null,
  zoneSearch: null, showZones: true, impact: null
};
const $ = id => document.getElementById(id);
const html = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const label = (kind,id) => `${kind==='pressure'?'Junction':kind==='flow'?'Pipe':kind==='reservoir'?'Reservoir':kind==='tank'?'Tank':'Pump'} ${kind==='pressure'?'J-':kind==='flow'?'P-':''}${id}`;
const cause = type => ({pipe_leak:'Pipe leak',valve_closure:'Pipe restriction',demand_surge:'Demand surge'})[type] || type;
const formatLocation = c => `${c.fault_type==='demand_surge'?'Junction J-':'Pipe P-'}${c.location_id}`;
const fmt = (v,kind) => Number.isFinite(v) ? (kind==='flow' ? `${(v*1000).toFixed(2)} L/s` : `${v.toFixed(2)} m`) : '—';
const num = (v,kind) => Number.isFinite(v) ? (kind==='flow' ? (v*1000).toFixed(2) : v.toFixed(2)) : '—';
let toastTimer;
function toast(message){const el=$('toast');if(!el)return;el.textContent=message;el.classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove('visible'),4000)}
async function json(url,options){const response=await fetch(url,options);if(!response.ok){let body=await response.text();try{body=JSON.parse(body).detail||body}catch{}throw new Error(body)}return response.json()}
function connectionHelp(){
  const b=$('connectionBanner');if(b)b.hidden=false;
  const s=$('setupSummary');if(s)s.innerHTML='<div><strong>Connect the WaterGuard server first</strong><p>The fault simulator and sensor baseline run in Python. Use the command shown above, then reload this page at http://127.0.0.1:8000/.</p></div>';
  const r=$('runButton');if(r)r.disabled=true;
}

const ZONE_COLORS = [
  '#2563eb', // Z1: Royal Blue
  '#7c3aed', // Z2: Vibrant Purple
  '#ea580c', // Z3: Warm Orange
  '#059669', // Z4: Emerald Green
  '#db2777', // Z5: Rose Pink
  '#0284c7'  // Z6: Cyan / Sky Blue
];

function renderZoneLegend() {
  const el = $('zoneLegend');
  if (!el) return;
  if (!state.topology?.zones || !state.topology.zones.length) {
    el.innerHTML = '<span class="muted">No zones defined</span>';
    return;
  }

  if (state.showZones === false) {
    el.innerHTML = '<span class="muted" style="font-size:0.82rem;">Zone overlay is hidden (Fault Focus view active). Click "Fault Focus" above to re-enable zone colors.</span>';
    if ($('zoneStatus')) {
      $('zoneStatus').textContent = 'Showing clear physical topology and highlighted fault location without zone color overlay.';
    }
    return;
  }

  const search = state.zoneSearch || {};
  const selected = new Set(search.initial_zones || []);
  const tested = new Set(search.tested_zones || []);
  const expanded = new Set(search.expanded_zones || []);

  el.innerHTML = state.topology.zones.map((zone, i) => {
    const status = tested.has(zone.id) ? 'tested'
      : expanded.has(zone.id) ? 'expanded'
      : selected.has(zone.id) ? 'selected'
      : 'idle';
    const color = ZONE_COLORS[i % ZONE_COLORS.length];
    return `<span class="zone-chip" data-status="${html(status)}" data-zone="${html(zone.id)}" title="${zone.pipe_ids.length} pipes, ${zone.junction_ids?.length || 0} junctions">
      <i style="background:${color};"></i>
      <b>${html(zone.id)}</b>: ${html(zone.name)}
      <small style="opacity:0.75">(${html(status)})</small>
    </span>`;
  }).join('');

  if ($('zoneStatus')) {
    $('zoneStatus').textContent = search.initial_zones?.length
      ? `Initially selected: ${search.initial_zones.join(', ')}. ` +
        `Tested: ${(search.tested_zones || []).join(', ') || 'none yet'}.`
      : `Network partitioned into ${state.topology.zones.length} connected zones. Each pipe and node belongs to one primary zone.`;
  }
}

async function loadAvailableNetworks(selectedName){
  try {
    const data = await json('/api/networks');
    const select = $('networkSelect');
    if(!select || !data.networks) return;
    const current = selectedName || select.value || state.networkName;
    select.innerHTML = data.networks.map(name => {
      const isNet1 = name === 'Net1';
      const isNet2 = name === 'Net2';
      const label = isNet2 ? 'Net2 (35 Junc · 40 Pipes · 6 Zones)'
        : isNet1 ? 'Net1 (9 Junc · 12 Pipes · 3 Zones)'
        : `${name} (Custom Network)`;
      return `<option value="${html(name)}" ${name === current ? 'selected' : ''}>${html(label)}</option>`;
    }).join('');
    select.value = current;
  } catch(e) {}
}

function updateNetworkLabels(){
  if(!state.topology)return;
  const net = state.networkName;
  const jCount = state.topology.junctions?.length || 0;
  const pCount = state.topology.pipes?.length || 0;
  const zCount = state.topology.zones?.length || 0;
  const tCount = state.topology.nodes?.filter(n => n.data.kind?.toLowerCase() === 'tank').length || 0;
  const rCount = state.topology.nodes?.filter(n => n.data.kind?.toLowerCase() === 'reservoir').length || 0;
  const pumpCount = state.topology.edges?.filter(e => e.data.kind?.toLowerCase() === 'pump').length || 0;

  if($('networkHeading')) $('networkHeading').textContent = `${net} distribution network`;
  if($('networkSubheading')) {
    const parts = [`${jCount} junctions`, `${pCount} pipes`];
    if(tCount) parts.push(`${tCount} tank${tCount > 1 ? 's' : ''}`);
    if(rCount) parts.push(`${rCount} reservoir${rCount > 1 ? 's' : ''}`);
    if(pumpCount) parts.push(`${pumpCount} pump${pumpCount > 1 ? 's' : ''}`);
    if(zCount) parts.push(`${zCount} zones`);
    $('networkSubheading').textContent = parts.join(' · ');
  }
  if($('modelBadge')) $('modelBadge').textContent = `SYNTHETIC ${net.toUpperCase()} · ${zCount} ZONES · LOCAL MODEL`;
  if($('pressureChartSubtitle')) $('pressureChartSubtitle').textContent = `All ${jCount} junctions · metres of water head`;
  if($('flowChartSubtitle')) $('flowChartSubtitle').textContent = `All ${pCount} pipes · litres per second; signed by pipe direction`;
}

async function switchNetwork(net){
  state.networkName = net;
  state.scenario = null;
  state.series = null;
  state.truth = null;
  state.readings = {pressures:{}, flows:{}};
  clearResult();
  renderQuantitativeImpact(null);
  document.querySelectorAll('.scenario-card').forEach(el=>el.classList.remove('active'));

  try {
    const [topology, baseline] = await Promise.all([
      json(`/api/network/${net}`),
      json(`/api/baseline/${net}`)
    ]);
    state.topology = topology;
    state.baseline = baseline.readings;
    state.connected = true;
    updateNetworkLabels();
    populateInputSensors();
    populateFaultLocations();
    renderAll();
    toast(`Loaded ${net}: ${topology.junctions.length} junctions, ${topology.pipes.length} pipes, ${topology.zones?.length || 0} zones.`);
  } catch(error) {
    toast(`Failed to load ${net}: ${error.message}`);
  }
}

async function init(){
  if(window.location.protocol==='file:'){connectionHelp();return}
  try{
    await loadAvailableNetworks(state.networkName);
    const netSelect = $('networkSelect');
    const net = netSelect?.value || state.networkName || 'Net2';
    state.networkName = net;
    if(netSelect) {
      netSelect.value = net;
      netSelect.onchange = () => switchNetwork(netSelect.value);
    }
    setupZoneToggle();
    setupNetworkUpload();
    await switchNetwork(net);
    try{state.tracing=await json('/api/observability');renderTelemetry()}catch{renderTelemetry()}
  }catch(error){connectionHelp();toast(`Unable to load the network: ${error.message}`)}
}

function populateFaultLocations(){
  if(!state.topology)return;
  const junction=$('faultType').value==='demand_surge';
  const ids=junction?state.topology.junctions:state.topology.pipes;
  $('faultLocation').innerHTML=ids.map(id=>`<option value="${html(id)}">${junction?'Junction J-':'Pipe P-'}${html(id)}</option>`).join('');
}
function populateInputSensors(){
  if(!state.topology)return;
  const kind=$('inputKind').value;
  const sensors=kind==='pressures'?state.topology.junctions:state.topology.pipes;
  $('inputSensor').innerHTML=sensors.map(id=>`<option value="${html(id)}">${kind==='pressures'?'Junction J-':'Pipe P-'}${html(id)}</option>`).join('');
  showBaselineHint();
}
function showBaselineHint(){
  if(!state.baseline)return;
  const kind=$('inputKind').value,id=$('inputSensor').value;
  const value=state.baseline[kind]?.[id];
  $('baselineHint').textContent=`Healthy model at ${kind==='pressures'?'junction J-':'pipe P-'}${id}: ${fmt(value,kind==='pressures'?'pressure':'flow')}. Enter flow in L/s; a negative value indicates reverse direction.`;
}
function clearResult(){
  state.report=null;
  state.events=[];
  state.runError=null;
  state.zoneSearch=null;
  if($('traceState'))$('traceState').textContent='Not started';
  renderResult();
  renderTrace();
  if(!state.truth) renderQuantitativeImpact(null);
}
async function loadDemo(kind){
  if(!state.connected){connectionHelp();return}
  document.querySelectorAll('.scenario-card').forEach(el=>el.classList.toggle('active',el.dataset.demo===kind));
  $('setupSummary').innerHTML='<div class="status-dot"></div><div><strong>Generating synthetic sensor readings…</strong></div>';
  try{
    const demo=await json(`/api/demo/${kind}?network=${state.networkName}`);
    state.scenario=kind;state.series=null;state.truth=demo.ground_truth;
    state.readings=demo.observed;clearResult();
    renderAll();
    syncQuantitativeImpact();
    toast(`Synthetic readings loaded for ${state.networkName}. Ground truth stays outside the investigation request.`);
  }catch(error){toast(`Could not load example: ${error.message}`)}
}
async function loadCustomFault(){
  if(!state.connected){connectionHelp();return}
  const fault_type=$('faultType').value,location_id=$('faultLocation').value,severity=Number($('faultSeverity').value);
  if(!Number.isFinite(severity)||severity<.05||severity>1){toast('Choose a strength from 0.05 to 1.00.');return}
  $('injectFault').disabled=true;
  $('setupSummary').innerHTML='<div class="status-dot"></div><div><strong>Simulating your custom fault…</strong></div>';
  try{
    const demo=await json('/api/demo/custom',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({network_name:state.networkName,fault_type,location_id,severity})});
    state.scenario='custom';state.series=demo.series;state.truth=demo.ground_truth;state.readings=demo.observed;
    document.querySelectorAll('.scenario-card').forEach(el=>el.classList.remove('active'));
    clearResult();renderAll();
    syncQuantitativeImpact();
    toast('Custom fault readings loaded. Run the investigator to see whether it finds your cause.');
  }catch(error){toast(`Could not inject fault: ${error.message}`);renderSummary()}
  finally{$('injectFault').disabled=false}
}
function addReading(){
  if(!state.connected){connectionHelp();return}
  const kind=$('inputKind').value,id=$('inputSensor').value,raw=$('inputValue').value;
  if(raw.trim()===''||!Number.isFinite(Number(raw))){toast('Enter a numeric sensor reading.');return}
  const value=kind==='flows'?Number(raw)/1000:Number(raw);
  if(kind==='pressures'&&value<0){toast('Pressure must be zero or greater for this example.');return}
  const normal=state.baseline?.[kind]?.[id];
  if(kind==='flows'&&Math.abs(value)>Math.max(1,Math.abs(normal||0)*10))toast(`That flow is far above the ${state.networkName} baseline. Check the L/s unit.`);
  if(state.scenario){state.readings={pressures:{},flows:{}};toast('Started a manual alert; the example readings were cleared.');}
  state.readings[kind][id]=value;state.scenario=null;state.truth=null;state.series=null;
  document.querySelectorAll('.scenario-card').forEach(el=>el.classList.remove('active'));
  clearResult();renderAll();$('inputValue').value='';toast('Reading added. Compare it with the baseline below.');
}
function removeReading(kind,id){delete state.readings[kind][id];state.scenario=null;state.truth=null;state.series=null;clearResult();renderAll()}
function clearReadings(){state.readings={pressures:{},flows:{}};state.scenario=null;state.truth=null;state.series=null;clearResult();renderQuantitativeImpact(null);renderAll();document.querySelectorAll('.scenario-card').forEach(el=>el.classList.remove('active'))}
function readingCount(){return Object.keys(state.readings.pressures).length+Object.keys(state.readings.flows).length}
function renderSummary(){
  const n=readingCount(), el=$('setupSummary');if(!el)return;el.classList.toggle('ready',n>0);
  if(!state.connected){connectionHelp();return}
  if(!n){el.innerHTML='<div class="status-dot"></div><div><strong>No readings loaded yet</strong><p>Choose an example, inject a custom fault, or add a manual measurement.</p></div>'}
  else{el.innerHTML=`<div class="status-dot"></div><div><strong>${state.scenario?'Synthetic '+cause(state.truth?.fault_type).toLowerCase()+' alert':'Manual sensor alert'} ready</strong><p>${Object.keys(state.readings.pressures).length} junction pressures and ${Object.keys(state.readings.flows).length} pipe flows in ${state.networkName}. The investigation receives measurements, not the hidden cause.</p></div>`}
  if($('runButton')) $('runButton').disabled=!n||state.running||!state.connected;
  if($('runTitle')) $('runTitle').textContent=state.running?'Investigation running':state.runError?'Investigation could not finish':state.report?'Investigation complete':n?'Sensor evidence is ready':'Ready when you are';
  if($('runSubtitle')) $('runSubtitle').textContent=state.running?'Validating readings and comparing hydraulic scenarios…':state.runError?state.runError:state.report?`${state.report.simulation_count} online scenarios verified in ${state.report.elapsed_ms} ms. The timeline records actual steps; no delay was added.`:n?`${n} readings will be checked against the healthy model, then the most plausible causes will be verified.`:'Load sensor values to enable the investigation.';
}
function readingList(){
  if(!state.baseline)return [];
  const rows=[];
  for(const [kind,type] of [['pressures','pressure'],['flows','flow']]){
    for(const [id,value] of Object.entries(state.readings[kind])){
      const base=state.baseline[kind]?.[id];if(!Number.isFinite(base))continue;
      rows.push({kind,id,type,value,base,delta:value-base,impact:Math.abs(value-base)/(kind==='pressures'?.8:.001)});
    }
  }
  return rows.sort((a,b)=>b.impact-a.impact);
}
function renderReadings(){
  const rows=readingList();
  const el=$('readingRows');if(!el)return;
  el.innerHTML=rows.length?rows.map(r=>`<tr><td><strong>${label(r.type,r.id)}</strong></td><td>${fmt(r.base,r.type)}</td><td>${fmt(r.value,r.type)}</td><td class="delta">${r.delta>=0?'+':''}${fmt(r.delta,r.type)}</td><td>${r.type==='flow'?(r.value<0?'Reverse direction relative to pipe ID':'Pipe flow'):'Junction pressure'}</td><td><button class="remove" data-kind="${r.kind}" data-id="${html(r.id)}" aria-label="Remove ${label(r.type,r.id)}">×</button></td></tr>`).join(''):'<tr><td colspan="6" class="empty">Load a scenario or add a reading.</td></tr>';
  el.querySelectorAll('.remove').forEach(btn=>btn.addEventListener('click',()=>removeReading(btn.dataset.kind,btn.dataset.id)));
}
function renderQuantitativeImpact(data){
  const card = $('impactCard');
  if(!card) return;
  if(!data){
    if(card.style) card.style.display = 'none';
    card.hidden = true;
    return;
  }
  if(card.style) card.style.display = 'block';
  card.hidden = false;
  const cand = data.candidate || {};
  const typeLabel = cand.fault_type === 'pipe_leak' ? 'Pipe Leak'
    : cand.fault_type === 'valve_closure' ? 'Pipe Restriction'
    : 'Demand Surge';
  const locLabel = cand.fault_type === 'demand_surge' ? `Junction J-${cand.location_id}` : `Pipe P-${cand.location_id}`;
  
  if($('impactHeading')) $('impactHeading').textContent = `Hydraulic Impact: ${typeLabel} at ${locLabel}`;
  if($('impactWaterLossPct')) $('impactWaterLossPct').textContent = `${data.water_loss.loss_percentage}%`;
  if($('impactWaterLossSub')) $('impactWaterLossSub').textContent = `${data.water_loss.flow_rate_lps} L/s · ${data.water_loss.daily_volume_m3} m³/day`;
  
  if($('impactDisruptionPct')) $('impactDisruptionPct').textContent = `${data.resource_impact.disruption_percentage}%`;
  if($('impactDisruptionSub')) $('impactDisruptionSub').textContent = `${data.resource_impact.affected_junctions_pct}% junctions affected · max drop ${data.resource_impact.max_pressure_drop_m} m`;
  
  if($('impactBenefitPct')) $('impactBenefitPct').textContent = `+${data.repair_benefits.hydraulic_efficiency_gain_pct}%`;
  if($('impactBenefitSub')) $('impactBenefitSub').textContent = `${data.repair_benefits.water_recovery_pct}% water saved · ${data.repair_benefits.water_saved_daily_m3} m³/day`;
  
  if($('impactProblemSummary')) $('impactProblemSummary').textContent = data.resource_impact.problem_summary;
  if($('impactBenefitSummary')) $('impactBenefitSummary').textContent = data.repair_benefits.benefit_summary;
}

async function syncQuantitativeImpact(){
  const card = $('impactCard');
  if(!card) return;

  if(state.report?.quantitative_impact){
    renderQuantitativeImpact(state.report.quantitative_impact);
    return;
  }

  const candidate = (state.report?.ranked_faults?.[0]?.candidate) || state.truth || null;
  if(candidate){
    try {
      const data = await json('/api/impact', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          network_name: state.networkName,
          candidate: {
            fault_type: candidate.fault_type,
            location_id: candidate.location_id,
            severity: candidate.severity || 0.55
          }
        })
      });
      renderQuantitativeImpact(data);
      return;
    } catch(err) {
      console.warn('Quantitative impact fetch failed:', err);
    }
  }

  renderQuantitativeImpact(null);
}

function setupZoneToggle(){
  const btn = $('toggleZoneOverlay');
  if(!btn) return;
  btn.addEventListener('click', () => {
    state.showZones = !state.showZones;
    btn.classList.toggle('active', state.showZones);
    const txt = $('zoneToggleText');
    if(txt) txt.textContent = state.showZones ? 'Zone Overlay: ON' : 'Fault Focus (Zones OFF)';
    renderZoneLegend();
    renderMap();
  });
}

function setupNetworkUpload(){
  const fileInput = $('inpUploadInput');
  if(!fileInput) return;
  fileInput.addEventListener('change', async (e) => {
    const file = e.target.files?.[0];
    if(!file) return;
    if(!file.name.toLowerCase().endsWith('.inp')){
      toast('Please upload an EPANET .inp file');
      fileInput.value = '';
      return;
    }
    toast(`Processing ${file.name}: loading physics and building signatures...`);
    const formData = new FormData();
    formData.append('file', file);
    try {
      const res = await fetch('/api/network/upload', {
        method: 'POST',
        body: formData
      });
      if(!res.ok){
        const err = await res.json().catch(() => ({detail: res.statusText}));
        throw new Error(err.detail || 'Upload failed');
      }
      const data = await res.json();
      toast(`Successfully loaded ${data.network_name}! (${data.junctions_count} junctions, ${data.pipes_count} pipes, ${data.zones_count} zones)`);
      await loadAvailableNetworks(data.network_name);
      await switchNetwork(data.network_name);
    } catch(err) {
      toast(`Network upload failed: ${err.message}`);
    } finally {
      fileInput.value = '';
    }
  });
}

function renderMap(){
  if(!state.topology)return;
  const isLarge = state.topology.nodes.length > 20;
  const raw=state.topology.nodes.map(n=>n.position),minX=Math.min(...raw.map(p=>p.x)),maxX=Math.max(...raw.map(p=>p.x)),minY=Math.min(...raw.map(p=>p.y)),maxY=Math.max(...raw.map(p=>p.y));
  const place=p=>({x:82+(p.x-minX)/Math.max(1,maxX-minX)*736,y:88+(p.y-minY)/Math.max(1,maxY-minY)*452});
  const nodes=Object.fromEntries(state.topology.nodes.map(n=>[n.data.id,{...n,p:place(n.position)}]));
  const ranked=state.report?.ranked_faults||[];
  const changed=Object.fromEntries(readingList().map(r=>[r.kind+':'+r.id,r.impact>.12]));

  // Active fault to prominently highlight (from report or synthetic truth)
  const activeFault = (ranked.length > 0 ? ranked[0].candidate : null) || state.truth || null;

  // Zone coloring setup
  const showZones = state.showZones !== false;
  const zones = state.topology.zones || [];
  const pipeZone = Object.fromEntries(
    zones.flatMap(zone =>
      zone.pipe_ids.map(id => [id, zone.id])
    )
  );
  const nodeZone = Object.fromEntries(
    zones.flatMap(zone =>
      (zone.junction_ids || zone.node_ids || []).map(id => [id, zone.id])
    )
  );
  const zoneIndex = Object.fromEntries(
    zones.map((zone, index) => [zone.id, index])
  );
  const selectedZones = new Set(
    state.zoneSearch?.initial_zones || []
  );
  const testedZones = new Set(
    state.zoneSearch?.tested_zones || []
  );

  let svg=`<svg viewBox="0 0 900 625" role="img" aria-label="Complete ${state.networkName} network: ${state.topology.junctions?.length} junctions, ${state.topology.pipes?.length} pipes."><rect x="0" y="0" width="900" height="625" fill="#eef7f8"/>`;
  
  // Render pipes
  for(const edge of state.topology.edges){
    const a=nodes[edge.data.source]?.p,b=nodes[edge.data.target]?.p,id=edge.data.id;
    if(!a||!b)continue;
    const pump=String(edge.data.kind).toLowerCase()==='pump';
    
    // Check if this pipe is the active fault (leader) or runner
    const r=ranked.findIndex(x=>x.candidate.fault_type!=='demand_surge'&&x.candidate.location_id===id);
    const isFaultLeader = (r === 0) || (activeFault && activeFault.fault_type !== 'demand_surge' && String(activeFault.location_id) === String(id));
    const isRunner = r > 0 && r < 3;

    const cls=`${isFaultLeader ? 'fault-highlight leader' : isRunner ? 'runner' : ''} ${changed['flows:'+id] ? 'changed' : ''}`;
    const vertical=Math.abs(a.x-b.x)<10;
    const mx=(a.x+b.x)/2+(vertical?(a.x>715?-45:39):0),my=(a.y+b.y)/2+(vertical?0:-24);
    const reading=state.readings.flows[id];
    const text=pump?'Pump 9':`P-${id}${Number.isFinite(reading)?' · '+(reading*1000).toFixed(1)+' L/s':''}`;
    const width=Math.max(56,text.length*6.3+14);

    // Zone-based pipe coloring (or clean neutral slate if zones overlay toggled off)
    const zoneId = pipeZone[id];
    const zoneColor = (showZones && zoneId)
      ? ZONE_COLORS[zoneIndex[zoneId] % ZONE_COLORS.length]
      : '#8ca4af';
    const zoneClass = (showZones && testedZones.has(zoneId)) ? 'zone-tested'
      : (showZones && selectedZones.has(zoneId)) ? 'zone-selected'
      : '';

    const pipeWidth = isFaultLeader ? (isLarge ? 11 : 14) : isRunner ? (isLarge ? 8 : 10) : (isLarge ? 5 : 6);
    const strokeColor = isFaultLeader ? '#ff1744' : isRunner ? '#f59e0b' : zoneColor;

    // Glowing white halo underlay for faulted pipe so it is unmistakable above all zones
    if(isFaultLeader){
      svg+=`<line class="fault-halo" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="#ffffff" style="stroke:#ffffff !important; stroke-width:${pipeWidth+8}px; stroke-linecap:round; opacity:0.95; filter:drop-shadow(0 0 6px #ff1744);"/>`;
    }

    svg+=`<g class="map-pipe ${cls} ${zoneClass}" data-zone="${html(zoneId || '')}" data-kind="${pump?'pump':'flow'}" data-id="${html(id)}"><line class="pipe-line" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="${strokeColor}" style="stroke:${strokeColor} !important; stroke-width:${pipeWidth}px; stroke-linecap:round;"/><line class="hit" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" style="stroke:transparent; stroke-width:24px;"/><title>${html(text)} · ${html(edge.data.source)} to ${html(edge.data.target)}${zoneId?' · '+zoneId:''}${isFaultLeader?' [FAULT LOCATION]':''}</title></g>`;
    
    // Callouts: prominent hazard badge for fault leader, or standard callout for readings/selection
    if(isFaultLeader){
      const faultLabel = activeFault?.fault_type === 'pipe_leak' ? '🚨 FAULT: Leak' : '🚨 FAULT: Valve Closed';
      const badgeW = 160;
      svg+=`<g class="edge-callout fault-callout" aria-hidden="true"><rect x="${mx-badgeW/2}" y="${my-14}" width="${badgeW}" height="24" rx="6"/><text x="${mx}" y="${my+3}" text-anchor="middle">${faultLabel} (P-${id})</text></g>`;
    } else if(!isLarge || r >= 0 || changed['flows:'+id] || state.selected?.id === id){
      svg+=`<g class="edge-callout" aria-hidden="true"><rect x="${mx-width/2}" y="${my-13}" width="${width}" height="20" rx="6"/><text x="${mx}" y="${my+1}" text-anchor="middle">${html(text)}</text></g>`;
    }
  }

  // Render nodes
  const nodeRadius = isLarge ? 13 : 21;
  const labelFontSize = isLarge ? 10 : 14;
  for(const node of state.topology.nodes){
    const {x,y}=nodes[node.data.id].p,id=node.data.id,kind=String(node.data.kind).toLowerCase();
    
    // Check if this junction is the active fault (leader) or runner
    const r=ranked.findIndex(item=>item.candidate.fault_type==='demand_surge'&&item.candidate.location_id===id);
    const isFaultLeader = (r === 0) || (activeFault && activeFault.fault_type === 'demand_surge' && String(activeFault.location_id) === String(id));
    const isRunner = r > 0 && r < 3;

    const cls=`${kind!=='junction'?'utility':''} ${isFaultLeader ? 'fault-highlight leader' : isRunner ? 'runner' : ''} ${changed['pressures:'+id] ? 'deviation' : ''}`;
    
    const nodeZoneId = nodeZone[id];
    const nodeZoneColor = (showZones && nodeZoneId) ? ZONE_COLORS[zoneIndex[nodeZoneId] % ZONE_COLORS.length] : '#406579';
    const nodeStroke = isFaultLeader ? '#d32f2f' : isRunner ? '#f59e0b' : nodeZoneColor;
    const nodeFill = isFaultLeader ? '#ffebee' : isRunner ? '#fff3e0' : '#ffffff';

    // Ripple ring for faulted junction
    if(isFaultLeader && kind === 'junction'){
      svg+=`<circle class="node-pulse-ring" cx="${x}" cy="${y}" r="${nodeRadius + 7}"/>`;
    }

    const shape=kind==='junction'?`<circle class="node-shape" cx="${x}" cy="${y}" r="${isFaultLeader ? nodeRadius + 2 : nodeRadius}" style="stroke:${nodeStroke} !important; stroke-width:${isFaultLeader?5:r>=0?4:3}px; fill:${nodeFill} !important;"/>`:kind==='tank'?`<path class="node-shape" d="M ${x} ${y-nodeRadius-2} L ${x+nodeRadius+2} ${y} L ${x} ${y+nodeRadius+2} L ${x-nodeRadius-2} ${y} Z" style="fill:#376d8d; stroke:#376d8d;"/>`:`<rect class="node-shape" x="${x-nodeRadius}" y="${y-nodeRadius}" width="${nodeRadius*2}" height="${nodeRadius*2}" rx="5" style="fill:#376d8d; stroke:#376d8d;"/>`;
    const value=state.readings.pressures[id],text=kind==='junction'?`J-${id}`:kind==='tank'?'Tank 2':'Reservoir 9';
    const nodeText = kind==='junction'?(isLarge?id:'J'+id):kind==='tank'?'T':'R';
    svg+=`<g class="map-node ${cls}" data-kind="${kind==='junction'?'pressure':kind}" data-id="${html(id)}">${shape}<text class="map-label" x="${x}" y="${y+(isLarge?3:4)}" text-anchor="middle" style="font-size:${labelFontSize}px; fill:${isFaultLeader ? '#b91c1c' : '#244c5c'}">${html(nodeText)}</text><title>${html(text)}${nodeZoneId?' · '+nodeZoneId:''}${Number.isFinite(value)?' · '+value.toFixed(2)+' m':''}${isFaultLeader?' [FAULT LOCATION]':''}</title></g>`;
    
    // Callouts for faulted junction
    if(isFaultLeader && kind === 'junction'){
      const badgeW = 160;
      svg+=`<g class="node-callout fault-callout" aria-hidden="true"><rect x="${x-badgeW/2}" y="${y-nodeRadius-28}" width="${badgeW}" height="22" rx="6"/><text x="${x}" y="${y-nodeRadius-13}" text-anchor="middle">🚨 FAULT: Surge (J-${id})</text></g>`;
    } else if(kind==='junction'&&Number.isFinite(value)&&(!isLarge || r >= 0 || changed['pressures:'+id] || state.selected?.id === id)){
      svg+=`<text class="node-reading" x="${x}" y="${y+nodeRadius+14}" text-anchor="middle" style="font-size:${isLarge?9:11}px">${value.toFixed(1)} m</text>`;
    } else if(kind!=='junction') {
      svg+=`<text class="node-reading" x="${x}" y="${y+nodeRadius+14}" text-anchor="middle" style="font-size:${isLarge?9:11}px">${html(text)}</text>`;
    }
  }
  svg+='</svg>';$('networkMap').innerHTML=svg;
  $('networkMap').querySelectorAll('[data-kind][data-id]').forEach(el=>el.addEventListener('click',()=>{state.selected={kind:el.dataset.kind,id:el.dataset.id};renderInspector();renderMap()}));
  renderInspector();
}
function renderInspector(){
  const el=$('networkInspector'),s=state.selected;if(!el)return;
  if(!s){el.innerHTML='<p class="eyebrow">NETWORK INSPECTOR</p><h3>Select a pipe or junction</h3><p>Its healthy model value, observed value, and role will appear here.</p>';return}
  const edge=state.topology?.edges?.find(e=>e.data.id===s.id),kind=s.kind;
  const base=state.baseline?.[kind==='pressure'?'pressures':'flows']?.[s.id];
  const value=state.readings[kind==='pressure'?'pressures':'flows']?.[s.id];
  const title=kind==='pressure'?`Junction J-${s.id}`:kind==='flow'?`Pipe P-${s.id}`:kind==='reservoir'?`Reservoir ${s.id}`:kind==='tank'?`Tank ${s.id}`:`Pump ${s.id}`;
  let explanation=kind==='pressure'?'A junction where pressure can be measured and water may be consumed.':kind==='flow'?'A pipe connecting two model nodes. Negative flow means the hydraulic result moves opposite the stored start-to-end direction.':kind==='tank'?'A storage element that affects pressure over time.':kind==='reservoir'?'A source boundary that supplies the model.':'A pump that moves water from the supply into the network.';
  let out=`<p class="eyebrow">NETWORK INSPECTOR</p><span class="kind-tag">${kind.toUpperCase()}</span><h3 style="margin-top:12px">${html(title)}</h3><p>${explanation}</p>`;
  if(edge)out+=`<div class="metric"><span>Connects</span><b>${html(edge.data.source)} → ${html(edge.data.target)}</b></div>`;
  if(Number.isFinite(base))out+=`<div class="metric"><span>Healthy baseline</span><b>${fmt(base,kind)}</b></div>`;
  if(Number.isFinite(value))out+=`<div class="metric"><span>Observed at alert</span><b>${fmt(value,kind)}</b></div><div class="metric"><span>Change from baseline</span><b>${fmt(value-base,kind)}</b></div>`;
  const matches=state.report?.ranked_faults?.filter(r=>r.candidate.location_id===s.id&&((kind==='pressure'&&r.candidate.fault_type==='demand_surge')||(kind==='flow'&&r.candidate.fault_type!=='demand_surge'))) || [];
  if(matches.length)out+=`<div class="note-box">Ranked scenario: ${cause(matches[0].candidate.fault_type)}. This is a tested hypothesis, not a confirmed field location.</div>`;
  el.innerHTML=out;
}
function drawProfile(container,ids,baseline,observed,kind){
  if(!container)return;
  const unit=kind==='pressure'?'m':'L/s',scale=kind==='pressure'?1:1000;
  const values=ids.flatMap(id=>[baseline[id]*scale,observed[id]*scale].filter(Number.isFinite));
  if(!values.length){container.innerHTML='<p class="empty">No baseline readings available.</p>';return}
  const width=900,height=315,left=66,right=23,top=25,bottom=57;
  let low=Math.min(...values),high=Math.max(...values);
  const padding=Math.max((high-low)*.15,kind==='pressure'?.5:.15);
  low-=padding;high+=padding;
  const x=i=>left+i*(width-left-right)/Math.max(ids.length-1,1);
  const y=v=>top+(high-v)/(high-low)*(height-top-bottom);
  let svg=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Healthy and observed ${kind} for all ${ids.length} ${state.networkName} ${kind==='pressure'?'junctions':'pipes'}">`;
  for(let i=0;i<=4;i++){
    const v=low+i*(high-low)/4,cy=y(v);
    svg+=`<line class="grid-line" x1="${left}" x2="${width-right}" y1="${cy}" y2="${cy}"/><text x="${left-10}" y="${cy+4}" text-anchor="end">${v.toFixed(kind==='pressure'?1:2)} ${unit}</text>`;
  }
  const bPts=ids.map((id,i)=>Number.isFinite(baseline[id])?[x(i),y(baseline[id]*scale)]:null).filter(Boolean);
  const oPts=ids.map((id,i)=>Number.isFinite(observed[id])?[x(i),y(observed[id]*scale)]:null).filter(Boolean);
  if(bPts.length)svg+=`<path class="baseline-path" d="M ${bPts.map(p=>p.join(',')).join(' L ')}"/>`;
  if(oPts.length)svg+=`<path class="fault-path" d="M ${oPts.map(p=>p.join(',')).join(' L ')}"/>`;
  ids.forEach((id,i)=>{
    const cx=x(i);
    if(Number.isFinite(baseline[id]))svg+=`<circle class="baseline-point" cx="${cx}" cy="${y(baseline[id]*scale)}" r="3"><title>Healthy ${kind==='pressure'?'J-':'P-'}${html(id)}: ${(baseline[id]*scale).toFixed(2)} ${unit}</title></circle>`;
    if(Number.isFinite(observed[id]))svg+=`<circle class="fault-point" cx="${cx}" cy="${y(observed[id]*scale)}" r="4"><title>Observed ${kind==='pressure'?'J-':'P-'}${html(id)}: ${(observed[id]*scale).toFixed(2)} ${unit}</title></circle>`;
    const fontSize = ids.length > 20 ? '8px' : '11px';
    svg+=`<text x="${cx}" y="${height-18}" text-anchor="middle" style="font-size:${fontSize}">${kind==='pressure'?'J-':'P-'}${html(id)}</text>`;
  });
  svg+='</svg>';container.innerHTML=svg;
}
function drawDeltaChart(container,ids,baseline,observed,kind){
  if(!container)return;
  const unit=kind==='pressure'?'m':'L/s',scale=kind==='pressure'?1:1000;
  const deltas=ids.map(id=>Number.isFinite(observed[id])&&Number.isFinite(baseline[id])?(observed[id]-baseline[id])*scale:null);
  const active=deltas.filter(Number.isFinite);
  if(!active.length){container.innerHTML='<p class="empty">No observed measurements to compare yet.</p>';return}
  const width=900,height=165,left=66,right=23,top=18,bottom=38;
  const max=Math.max(...active.map(Math.abs),kind==='pressure'?.3:.05);
  const zero=top+(height-top-bottom)/2,span=(height-top-bottom)/2;
  const x=i=>left+i*(width-left-right)/Math.max(ids.length-1,1);
  let svg=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Change from healthy model at each ${kind} sensor"><line class="delta-zero" x1="${left}" x2="${width-right}" y1="${zero}" y2="${zero}"/><text x="${left-10}" y="${zero+4}" text-anchor="end">0 ${unit}</text>`;
  svg+=`<text x="${left-10}" y="${top+8}" text-anchor="end">+${max.toFixed(kind==='pressure'?1:2)}</text><text x="${left-10}" y="${height-bottom}" text-anchor="end">-${max.toFixed(kind==='pressure'?1:2)}</text>`;
  ids.forEach((id,i)=>{
    const cx=x(i),d=deltas[i];
    if(Number.isFinite(d)){
      const end=zero-d/max*span;
      svg+=`<line class="delta-stem" x1="${cx}" x2="${cx}" y1="${zero}" y2="${end}"/><circle class="delta-point" cx="${cx}" cy="${end}" r="4"><title>${kind==='pressure'?'Junction J-':'Pipe P-'}${html(id)}: ${d>=0?'+':''}${d.toFixed(3)} ${unit}</title></circle>`;
    }
    const fontSize = ids.length > 20 ? '8px' : '11px';
    svg+=`<text x="${cx}" y="${height-14}" text-anchor="middle" style="font-size:${fontSize}">${kind==='pressure'?'J-':'P-'}${html(id)}</text>`;
  });
  svg+='</svg>';container.innerHTML=svg;
}
function renderCharts(){
  if(!state.baseline||!state.topology)return;
  const pressureIds=state.topology.junctions,flowIds=state.topology.pipes;
  drawProfile($('pressureChart'),pressureIds,state.baseline.pressures,state.readings.pressures,'pressure');
  drawProfile($('flowChart'),flowIds,state.baseline.flows,state.readings.flows,'flow');
  drawDeltaChart($('pressureDelta'),pressureIds,state.baseline.pressures,state.readings.pressures,'pressure');
  drawDeltaChart($('flowDelta'),flowIds,state.baseline.flows,state.readings.flows,'flow');
  const pressureCount=pressureIds.filter(id=>Number.isFinite(state.readings.pressures[id])).length;
  const flowCount=flowIds.filter(id=>Number.isFinite(state.readings.flows[id])).length;
  if($('pressureChartNote')) $('pressureChartNote').textContent=`${pressureCount}/${pressureIds.length} junction pressures observed. ${pressureCount<pressureIds.length?'Missing measurements are left blank.':'Blue is healthy; orange is the injected/observed alert snapshot.'}`;
  if($('flowChartNote')) $('flowChartNote').textContent=`${flowCount}/${flowIds.length} pipe flows observed. ${flowCount<flowIds.length?'Missing measurements are left blank.':'Values are in L/s; negative values indicate reverse flow.'}`;
}
function traceDescription(event){
  const n=event.node;
  if(n==='monitor')return ['Check the healthy baseline',event.anomaly?.anomalous?`Readings differ from normal (standardised score ${event.anomaly.standardised_residual.toFixed(3)}).`:'No detectable difference from the model baseline.'];
  if(n==='context')return ['Read operational context',`Maintenance ${event.sources?.maintenance||0}, demand schedule ${event.sources?.demand_schedule||0}, weather records ${event.sources?.weather||0}. Empty means no local record, not proof of no activity.`];
  if(n==='rank_zones'){
    const count=(event.zone_rankings||[]).length;
    const best=event.zone_rankings?.[0];
    return ['Rank topology zones',`${count} zones scored from stored signatures.${best?` Best zone: ${best.zone_id} (coarse score ${best.coarse_score?.toFixed(3)}).`:''}`];
  }
  if(n==='hypotheses')return ['Find candidate locations',`${event.shortlist_count||0} type-and-location candidates shortlisted from stored signatures. ${event.shortlist?.length?'First: '+event.shortlist.map(c=>`${cause(c.fault_type)} at ${formatLocation(c)}`).join('; ')+'.':''}`];
  if(n==='simulate')return ['Verify candidates with WNTR',`${event.simulation_count} fresh scenarios tested. ${event.latest?.map(r=>`${cause(r.candidate.fault_type)} at ${formatLocation(r.candidate)}: ${Number.isFinite(r.score)?'mismatch '+r.score.toFixed(3):'simulation failed'}`).join('; ')||''}`];
  if(n==='expand'){
    const expanded=event.expanded_zone_ids||[];
    return ['Expand zone search',`Opening search to ${expanded.length} additional zone(s): ${expanded.join(', ')||'none'}.`];
  }
  if(n==='evidence')return ['Compare measured and predicted values',event.leaders?.length?`Current best: ${cause(event.leaders[0].candidate.fault_type)} at ${formatLocation(event.leaders[0].candidate)} (mismatch ${event.leaders[0].score.toFixed(3)}). ${event.ambiguous?'Leading scenarios still overlap.':'Best match is distinct so far.'}`:'No successful simulations to rank.'];
  if(n==='diagnose')return ['Prepare operator diagnosis',`Recommended response: ${event.action||'check evidence'}.`];
  if(n==='sensor_planner')return ['Look for a useful next measurement',event.sensor_recommendation?`A pressure measurement at junction J-${event.sensor_recommendation.junction_id} could separate the two leaders by ${event.sensor_recommendation.predicted_difference_m} m.`:'No additional unmeasured junction was proposed for this result.'];
  if(n==='report')return ['Assemble the final report','Ranked causes, sensor evidence, uncertainty and human-facing advice are ready.'];
  if(n==='no_alert')return ['Stop after baseline check','The available readings do not trigger a fault investigation.'];
  return [n,'Workflow step completed.'];
}
function renderTrace(){
  const list=$('traceList');if(!list)return;
  if(!state.events.length){list.innerHTML='<li class="empty">The reasoning steps will appear here after you run an investigation.</li>';if($('traceState'))$('traceState').textContent='Not started';return}
  if($('traceState'))$('traceState').textContent=state.running?'In progress…':state.runError?'Failed':'Complete';
  list.innerHTML=state.events.map(event=>{
    const [title,summary]=traceDescription(event);
    return `<li><div><strong>${html(title)}</strong><p>${html(summary)}</p><small>${event.elapsed_ms} ms · step ${event.step}</small><details><summary>Recorded step data</summary><pre>${html(JSON.stringify(event,null,2))}</pre></details></div></li>`;
  }).join('');
}
function renderTelemetry(){
  const el=$('telemetryStatus');if(!el)return;const t=state.tracing;if(!t){el.textContent='Local timeline is active.';return}
  el.innerHTML=`<span class="trace-pill ${t.agentops_active?'on':'off'}">AgentOps: ${t.agentops_active?'connected':'local only'}</span><span class="trace-pill ${t.langsmith_active?'on':'off'}">LangSmith: ${t.langsmith_active?'connected':'local only'}</span>`;
}
function renderResult(){
  const el=$('resultCard');if(!el)return;const r=state.report;
  if(!r){
    if(state.runError)el.innerHTML=`<p class="eyebrow">DIAGNOSIS</p><div class="status-banner ambiguous"><strong>Investigation failed</strong><span>${html(state.runError)}</span></div><p>Ensure the WaterGuard backend is running, then try again.</p>`;
    else el.innerHTML='<p class="eyebrow">DIAGNOSIS</p><h3>No investigation yet</h3><p>Results will show the best tested causes, measured evidence, ambiguity, and operator recommendations.</p>';
    return;
  }
  const ranked=r.ranked_faults||[];
  let body='<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px"><p class="eyebrow">DIAGNOSIS</p><button class="text-button" id="exportReport" type="button">Download report JSON ⤓</button></div>';

  const headline = (r.ambiguous && ranked.length > 1)
    ? `Ambiguous: #${ranked[0]?.rank} ${cause(ranked[0]?.candidate.fault_type)} at ${formatLocation(ranked[0]?.candidate)} fits best, but #${ranked[1]?.rank} ${cause(ranked[1]?.candidate.fault_type)} at ${formatLocation(ranked[1]?.candidate)} remains plausible`
    : ranked.length
      ? `Leading hypothesis: ${cause(ranked[0].candidate.fault_type)} at ${formatLocation(ranked[0].candidate)}`
      : 'No fault candidates verified';

  body+=`<div class="status-banner ${r.ambiguous?'ambiguous':'good'}"><strong>${html(headline)}</strong><span>${html(r.reason||'')}</span></div>`;
  body+=`<div class="result-stats"><span class="stat-pill"><b>${r.simulation_count}</b>Online WNTR runs</span><span class="stat-pill"><b>${r.candidate_space_size}</b>Candidate combinations</span><span class="stat-pill"><b>${r.offline_simulation_count}</b>Stored catalog signatures</span><span class="stat-pill"><b>${r.elapsed_ms} ms</b>Elapsed time</span></div>`;

  if(!ranked.length){body+=`<p>None of the shortlisted candidates matched the observed sensor alert better than normal network conditions. ${html(r.reason||'')}</p>`}
  else{
    for(const item of ranked.slice(0,3)){
      body+=`<div class="rank-card ${item.rank===1?'lead-rank':''}"><div class="rank-top"><strong>#${item.rank} · ${cause(item.candidate.fault_type)} at ${formatLocation(item.candidate)}</strong><small>severity estimate ${item.candidate.severity.toFixed(2)}</small></div><p>Mismatch <b>${item.score.toFixed(3)}</b> (lower fits better). Relative weight among tested: <b>${Math.round(item.relative_weight*100)}%</b> — this is not a calibrated probability.</p><p>${html(item.explanation)}</p>`;
      if(item.rank===1&&item.evidence?.length){body+='<div class="table-scroll evidence-table"><table><thead><tr><th>Sensor</th><th>Healthy</th><th>Observed</th><th>Tested model</th></tr></thead><tbody>';
        for(const e of item.evidence){body+=`<tr><td>${label(e.kind,e.sensor_id)}</td><td>${num(e.baseline,e.kind)}</td><td>${num(e.observed,e.kind)}</td><td>${num(e.predicted,e.kind)}</td></tr>`}
        body+='</tbody></table></div><p>Pressure columns are in metres; flow columns are in L/s. All supplied sensors contribute to the score.</p>'}
      body+='</div>'}
  }
  const rec=r.recommendation||{};
  body+=`<div class="recommendation"><h4>Operator recommendation · ${html(rec.action||'REVIEW')}</h4><p>${html(rec.text||'')}</p><ol>${(rec.next_steps||[]).map(x=>`<li>${html(x)}</li>`).join('')}</ol><p><strong>${html(rec.caution||'No physical action is automated.')}</strong></p></div>`;
  if(rec.isolation_what_if)body+=`<div class="note-box"><strong>Separate isolation what-if:</strong> ${rec.isolation_what_if.simulated_safe?'The model had no junction below the 14 m demo threshold.':'The model shows possible low-pressure impact or the simulation could not confirm safety.'} ${html(rec.isolation_what_if.assumption)} This is ${r.isolation_simulations} extra simulation, separate from fault verification.</div>`;
  if(r.sensor_recommendation)body+=`<div class="note-box"><strong>Suggested next reading:</strong> pressure at junction J-${html(r.sensor_recommendation.junction_id)}. The two best tested scenarios differ there by ${r.sensor_recommendation.predicted_difference_m} m in the model. A real sensor or field measurement is required to gain new evidence.</div>`;
  el.innerHTML=body;
  if($('exportReport')) $('exportReport').addEventListener('click',()=>{
    const file=new Blob([JSON.stringify(r,null,2)],{type:'application/json'}),url=URL.createObjectURL(file),link=document.createElement('a');
    link.href=url;link.download='waterguard-report.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
}
async function runInvestigation(){
  if(!readingCount()||state.running)return;
  state.running=true;state.report=null;state.runError=null;state.events=[];state.zoneSearch=null;renderSummary();renderTrace();renderResult();
  if($('runTitle')) $('runTitle').textContent='Investigation running';
  if($('runSubtitle')) $('runSubtitle').textContent='Validating readings and comparing hydraulic scenarios…';
  try{
    const {session_id}=await json('/api/investigations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({network_name:state.networkName,observed:state.readings,budget:8})});
    await new Promise((resolve,reject)=>{
      const source=new EventSource(`/api/investigations/${session_id}/events`);
      source.onmessage=async message=>{
        const event=JSON.parse(message.data);

        if (event.type === 'node') {
          state.events.push(event);

          if (event.node === 'rank_zones') {
            state.zoneSearch = {
              ranked_zones: event.zone_rankings,
              initial_zones: (event.zone_rankings || [])
                .slice(0, 2)
                .map(row => row.zone_id),
              expanded_zones: [],
              tested_zones: [],
            };
          }

          if (event.node === 'expand') {
            if (state.zoneSearch) {
              state.zoneSearch.expanded_zones =
                event.expanded_zone_ids || [];
            }
          }

          if (event.node === 'simulate') {
            const tested = new Set(state.zoneSearch?.tested_zones || []);
            for (const row of event.latest || []) {
              if (row.zone_id) tested.add(row.zone_id);
            }
            if (state.zoneSearch) {
              state.zoneSearch.tested_zones = [...tested];
            }
          }

          renderTrace();
          renderMap();
          renderZoneLegend();
        }

        if(event.type==='complete'||event.type==='error'){
          source.close();if(event.type==='error'){reject(new Error(event.message));return}
          try{const finished=await json(`/api/investigations/${session_id}`);if(finished.status!=='complete'||!finished.result)throw new Error('Report was not returned');state.report=finished.result;
          state.zoneSearch = state.report.zone_search;
          syncQuantitativeImpact();
          resolve()}catch(e){reject(e)}
        }
      };
      source.onerror=()=>{source.close();reject(new Error(`Progress stream disconnected. Session ${session_id}`))};
    });
    renderAll();if($('runTitle')) $('runTitle').textContent='Investigation complete';if($('runSubtitle')) $('runSubtitle').textContent=`${state.report.simulation_count} online scenarios verified in ${state.report.elapsed_ms} ms. The timeline records actual steps; no delay was added.`;
  }catch(error){state.runError=error.message;toast(`Investigation failed: ${error.message}`)}
  finally{state.running=false;renderSummary();renderTrace();renderResult()}
}
function renderAll(){renderSummary();renderReadings();renderMap();renderCharts();renderTrace();renderResult();renderZoneLegend();}

document.querySelectorAll('.scenario-card').forEach(el=>el.addEventListener('click',()=>loadDemo(el.dataset.demo)));
if($('faultType')) $('faultType').addEventListener('change',populateFaultLocations);
if($('injectFault')) $('injectFault').addEventListener('click',loadCustomFault);
if($('inputKind')) $('inputKind').addEventListener('change',populateInputSensors);
if($('inputSensor')) $('inputSensor').addEventListener('change',showBaselineHint);
if($('addReading')) $('addReading').addEventListener('click',addReading);
if($('inputValue')) $('inputValue').addEventListener('keydown',event=>{if(event.key==='Enter')addReading()});
if($('clearReadings')) $('clearReadings').addEventListener('click',clearReadings);
if($('runButton')) $('runButton').addEventListener('click',runInvestigation);
init();
