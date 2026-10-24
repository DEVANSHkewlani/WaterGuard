'use strict';

const state = {
  topology: null, baseline: null, readings: {pressures:{},flows:{}},
  scenario: null, series: null, report: null, truth: null, events: [],
  selected: null, running: false, runError: null, connected: false, tracing: null
};
const $ = id => document.getElementById(id);
const html = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const label = (kind,id) => `${kind==='pressure'?'Junction':kind==='flow'?'Pipe':kind==='reservoir'?'Reservoir':kind==='tank'?'Tank':'Pump'} ${kind==='pressure'?'J-':kind==='flow'?'P-':''}${id}`;
const cause = type => ({pipe_leak:'Pipe leak',valve_closure:'Pipe restriction',demand_surge:'Demand surge'})[type] || type;
const formatLocation = c => `${c.fault_type==='demand_surge'?'Junction J-':'Pipe P-'}${c.location_id}`;
const fmt = (v,kind) => Number.isFinite(v) ? (kind==='flow' ? `${(v*1000).toFixed(2)} L/s` : `${v.toFixed(2)} m`) : '—';
const num = (v,kind) => Number.isFinite(v) ? (kind==='flow' ? (v*1000).toFixed(2) : v.toFixed(2)) : '—';
let toastTimer;
function toast(message){const el=$('toast');el.textContent=message;el.classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove('visible'),4000)}
async function json(url,options){const response=await fetch(url,options);if(!response.ok){let body=await response.text();try{body=JSON.parse(body).detail||body}catch{}throw new Error(body)}return response.json()}
function connectionHelp(){
  $('connectionBanner').hidden=false;
  $('setupSummary').innerHTML='<div><strong>Connect the WaterGuard server first</strong><p>The fault simulator and sensor baseline run in Python. Use the command shown above, then reload this page at http://127.0.0.1:8000/.</p></div>';
  $('runButton').disabled=true;
}

async function init(){
  if(window.location.protocol==='file:'){connectionHelp();return}
  try{
    const [topology,baseline]=await Promise.all([json('/api/network/Net1'),json('/api/baseline/Net1')]);
    state.topology=topology;state.baseline=baseline.readings;state.connected=true;
    populateInputSensors();populateFaultLocations();renderAll();
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
function clearResult(){state.report=null;state.events=[];state.runError=null;$('traceState').textContent='Not started';renderResult();renderTrace();}
async function loadDemo(kind){
  if(!state.connected){connectionHelp();return}
  document.querySelectorAll('.scenario-card').forEach(el=>el.classList.toggle('active',el.dataset.demo===kind));
  $('setupSummary').innerHTML='<div class="status-dot"></div><div><strong>Generating synthetic sensor readings…</strong></div>';
  try{
    const demo=await json(`/api/demo/${kind}`);
    state.scenario=kind;state.series=null;state.truth=demo.ground_truth;
    state.readings=demo.observed;clearResult();
    renderAll();
    toast('Synthetic readings loaded. Ground truth stays outside the investigation request.');
  }catch(error){toast(`Could not load example: ${error.message}`)}
}
async function loadCustomFault(){
  if(!state.connected){connectionHelp();return}
  const fault_type=$('faultType').value,location_id=$('faultLocation').value,severity=Number($('faultSeverity').value);
  if(!Number.isFinite(severity)||severity<.05||severity>1){toast('Choose a strength from 0.05 to 1.00.');return}
  $('injectFault').disabled=true;
  $('setupSummary').innerHTML='<div class="status-dot"></div><div><strong>Simulating your custom fault…</strong></div>';
  try{
    const demo=await json('/api/demo/custom',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({fault_type,location_id,severity})});
    state.scenario='custom';state.series=demo.series;state.truth=demo.ground_truth;state.readings=demo.observed;
    document.querySelectorAll('.scenario-card').forEach(el=>el.classList.remove('active'));
    clearResult();renderAll();
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
  if(kind==='flows'&&Math.abs(value)>Math.max(1,Math.abs(normal||0)*10))toast('That flow is far above the Net1 baseline. Check the L/s unit.');
  if(state.scenario){state.readings={pressures:{},flows:{}};toast('Started a manual alert; the example readings were cleared.');}
  state.readings[kind][id]=value;state.scenario=null;state.truth=null;state.series=null;
  document.querySelectorAll('.scenario-card').forEach(el=>el.classList.remove('active'));
  clearResult();renderAll();$('inputValue').value='';toast('Reading added. Compare it with the baseline below.');
}
function removeReading(kind,id){delete state.readings[kind][id];state.scenario=null;state.truth=null;state.series=null;clearResult();renderAll()}
function clearReadings(){state.readings={pressures:{},flows:{}};state.scenario=null;state.truth=null;state.series=null;clearResult();renderAll();document.querySelectorAll('.scenario-card').forEach(el=>el.classList.remove('active'))}
function readingCount(){return Object.keys(state.readings.pressures).length+Object.keys(state.readings.flows).length}
function renderSummary(){
  const n=readingCount(), el=$('setupSummary');el.classList.toggle('ready',n>0);
  if(!state.connected){connectionHelp();return}
  if(!n){el.innerHTML='<div class="status-dot"></div><div><strong>No readings loaded yet</strong><p>Choose an example, inject a custom fault, or add a manual measurement.</p></div>'}
  else{el.innerHTML=`<div class="status-dot"></div><div><strong>${state.scenario?'Synthetic '+cause(state.truth?.fault_type).toLowerCase()+' alert':'Manual sensor alert'} ready</strong><p>${Object.keys(state.readings.pressures).length} junction pressures and ${Object.keys(state.readings.flows).length} pipe flows. The investigation receives measurements, not the hidden cause.</p></div>`}
  $('runButton').disabled=!n||state.running||!state.connected;
  $('runTitle').textContent=state.running?'Investigation running':state.runError?'Investigation could not finish':state.report?'Investigation complete':n?'Sensor evidence is ready':'Ready when you are';
  $('runSubtitle').textContent=state.running?'Validating readings and comparing hydraulic scenarios…':state.runError?state.runError:state.report?`${state.report.simulation_count} online scenarios verified in ${state.report.elapsed_ms} ms. The timeline records actual steps; no delay was added.`:n?`${n} readings will be checked against the healthy model, then the most plausible causes will be verified.`:'Load sensor values to enable the investigation.';
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
  $('readingRows').innerHTML=rows.length?rows.map(r=>`<tr><td><strong>${label(r.type,r.id)}</strong></td><td>${fmt(r.base,r.type)}</td><td>${fmt(r.value,r.type)}</td><td class="delta">${r.delta>=0?'+':''}${fmt(r.delta,r.type)}</td><td>${r.type==='flow'?(r.value<0?'Reverse direction relative to pipe ID':'Pipe flow'):'Junction pressure'}</td><td><button class="remove" data-kind="${r.kind}" data-id="${html(r.id)}" aria-label="Remove ${label(r.type,r.id)}">×</button></td></tr>`).join(''):'<tr><td colspan="6" class="empty">Load a scenario or add a reading.</td></tr>';
  $('readingRows').querySelectorAll('.remove').forEach(btn=>btn.addEventListener('click',()=>removeReading(btn.dataset.kind,btn.dataset.id)));
}
function renderMap(){
  if(!state.topology)return;
  const raw=state.topology.nodes.map(n=>n.position),minX=Math.min(...raw.map(p=>p.x)),maxX=Math.max(...raw.map(p=>p.x)),minY=Math.min(...raw.map(p=>p.y)),maxY=Math.max(...raw.map(p=>p.y));
  const place=p=>({x:82+(p.x-minX)/Math.max(1,maxX-minX)*736,y:88+(p.y-minY)/Math.max(1,maxY-minY)*452});
  const nodes=Object.fromEntries(state.topology.nodes.map(n=>[n.data.id,{...n,p:place(n.position)}]));
  const ranked=state.report?.ranked_faults||[];
  const changed=Object.fromEntries(readingList().map(r=>[r.kind+':'+r.id,r.impact>.12]));
  let svg='<svg viewBox="0 0 900 625" role="img" aria-label="Complete Net1 network: 9 junctions, 12 pipes, tank, reservoir and pump. Sensor values appear on nodes and links."><rect x="0" y="0" width="900" height="625" fill="#eef7f8"/>';
  for(const edge of state.topology.edges){
    const a=nodes[edge.data.source]?.p,b=nodes[edge.data.target]?.p,id=edge.data.id;
    if(!a||!b)continue;
    const pump=String(edge.data.kind).toLowerCase()==='pump',r=ranked.findIndex(x=>x.candidate.fault_type!=='demand_surge'&&x.candidate.location_id===id);
    const cls=`${r===0?'leader':r>0&&r<3?'runner':''} ${changed['flows:'+id]?'changed':''}`;
    const vertical=Math.abs(a.x-b.x)<10;
    const mx=(a.x+b.x)/2+(vertical?(a.x>715?-45:39):0),my=(a.y+b.y)/2+(vertical?0:-24);
    const reading=state.readings.flows[id],text=pump?'Pump 9':`P-${id}${Number.isFinite(reading)?' · '+(reading*1000).toFixed(1)+' L/s':''}`;
    const width=Math.max(56,text.length*6.3+14);
    svg+=`<g class="map-pipe ${cls}" data-kind="${pump?'pump':'flow'}" data-id="${html(id)}"><line class="pipe-line" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"/><line class="hit" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"/><title>${html(text)} · ${html(edge.data.source)} to ${html(edge.data.target)}</title></g>`;
    svg+=`<g class="edge-callout" aria-hidden="true"><rect x="${mx-width/2}" y="${my-13}" width="${width}" height="20" rx="6"/><text x="${mx}" y="${my+1}" text-anchor="middle">${html(text)}</text></g>`;
  }
  for(const node of state.topology.nodes){
    const {x,y}=nodes[node.data.id].p,id=node.data.id,kind=String(node.data.kind).toLowerCase();
    const r=ranked.findIndex(item=>item.candidate.fault_type==='demand_surge'&&item.candidate.location_id===id);
    const cls=`${kind!=='junction'?'utility':''} ${r===0?'leader':r>0&&r<3?'runner':''} ${changed['pressures:'+id]?'deviation':''}`;
    const shape=kind==='junction'?`<circle class="node-shape" cx="${x}" cy="${y}" r="21"/>`:kind==='tank'?`<path class="node-shape" d="M ${x} ${y-23} L ${x+23} ${y} L ${x} ${y+23} L ${x-23} ${y} Z"/>`:`<rect class="node-shape" x="${x-20}" y="${y-20}" width="40" height="40" rx="6"/>`;
    const value=state.readings.pressures[id],text=kind==='junction'?`J-${id}`:kind==='tank'?'Tank 2':'Reservoir 9';
    svg+=`<g class="map-node ${cls}" data-kind="${kind==='junction'?'pressure':kind}" data-id="${html(id)}">${shape}<text class="map-label" x="${x}" y="${y+4}" text-anchor="middle">${kind==='junction'?'J'+html(id):kind==='tank'?'T':'R'}</text><title>${html(text)}${Number.isFinite(value)?' · '+value.toFixed(2)+' m':''}</title></g>`;
    if(kind==='junction'&&Number.isFinite(value))svg+=`<text class="node-reading" x="${x}" y="${y+39}" text-anchor="middle">${value.toFixed(1)} m</text>`;
    else if(kind!=='junction')svg+=`<text class="node-reading" x="${x}" y="${y+39}" text-anchor="middle">${html(text)}</text>`;
  }
  svg+='</svg>';$('networkMap').innerHTML=svg;
  $('networkMap').querySelectorAll('[data-kind][data-id]').forEach(el=>el.addEventListener('click',()=>{state.selected={kind:el.dataset.kind,id:el.dataset.id};renderInspector()}));
  renderInspector();
}
function renderInspector(){
  const el=$('networkInspector'),s=state.selected;
  if(!s){el.innerHTML='<p class="eyebrow">NETWORK INSPECTOR</p><h3>Select a pipe or junction</h3><p>Its healthy model value, observed value, and role will appear here.</p>';return}
  const edge=state.topology.edges.find(e=>e.data.id===s.id),kind=s.kind;
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
  const unit=kind==='pressure'?'m':'L/s',scale=kind==='pressure'?1:1000;
  const values=ids.flatMap(id=>[baseline[id]*scale,observed[id]*scale].filter(Number.isFinite));
  if(!values.length){container.innerHTML='<p class="empty">No baseline readings available.</p>';return}
  const width=900,height=315,left=66,right=23,top=25,bottom=57;
  let low=Math.min(...values),high=Math.max(...values);
  const padding=Math.max((high-low)*.15,kind==='pressure'?.5:.15);
  low-=padding;high+=padding;
  const x=i=>left+i*(width-left-right)/Math.max(ids.length-1,1);
  const y=v=>top+(high-v)/(high-low)*(height-top-bottom);
  const baselinePath=ids.map((id,i)=>`${i?'L':'M'}${x(i).toFixed(1)},${y(baseline[id]*scale).toFixed(1)}`).join(' ');
  // A missing manual reading is not invented: terminate each observed segment at the gap.
  let observedPath='',drawing=false;
  ids.forEach((id,i)=>{const value=observed[id];if(!Number.isFinite(value)){drawing=false;return}observedPath+=`${drawing?'L':'M'}${x(i).toFixed(1)},${y(value*scale).toFixed(1)} `;drawing=true});
  let svg=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Healthy and observed ${kind} for all ${ids.length} Net1 ${kind==='pressure'?'junctions':'pipes'}">`;
  for(let i=0;i<5;i++){const v=low+(high-low)*(4-i)/4,py=y(v);svg+=`<line class="grid-line" x1="${left}" x2="${width-right}" y1="${py}" y2="${py}"/><text x="${left-9}" y="${py+4}" text-anchor="end">${v.toFixed(1)}</text>`}
  svg+=`<path d="${baselinePath}" class="baseline-path"/><path d="${observedPath}" class="fault-path"/>`;
  ids.forEach((id,i)=>{
    const base=baseline[id]*scale,actual=observed[id]*scale;
    svg+=`<circle class="baseline-point" cx="${x(i)}" cy="${y(base)}" r="4"><title>${kind==='pressure'?'Junction J-':'Pipe P-'}${html(id)} healthy: ${base.toFixed(2)} ${unit}</title></circle>`;
    if(Number.isFinite(actual))svg+=`<circle class="fault-point" cx="${x(i)}" cy="${y(actual)}" r="5"><title>${kind==='pressure'?'Junction J-':'Pipe P-'}${html(id)} observed: ${actual.toFixed(2)} ${unit}; change ${(actual-base).toFixed(2)} ${unit}</title></circle>`;
    svg+=`<text x="${x(i)}" y="${height-19}" text-anchor="middle">${kind==='pressure'?'J-':'P-'}${html(id)}</text>`;
  });
  svg+='</svg>';container.innerHTML=svg;
}
function drawDeltaChart(container,ids,baseline,observed,kind){
  const scale=kind==='pressure'?1:1000,unit=kind==='pressure'?'m':'L/s';
  const deltas=ids.map(id=>Number.isFinite(observed[id])?(observed[id]-baseline[id])*scale:null);
  const max=Math.max(.001,...deltas.filter(Number.isFinite).map(Math.abs));
  const width=900,height=174,left=66,right=23,zero=73,span=55;
  const x=i=>left+i*(width-left-right)/Math.max(ids.length-1,1);
  let svg=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Observed minus healthy ${kind}, using its own change scale"><line class="delta-zero" x1="${left}" x2="${width-right}" y1="${zero}" y2="${zero}"/><text x="${left-9}" y="${zero+4}" text-anchor="end">0</text>`;
  ids.forEach((id,i)=>{
    const d=deltas[i],cx=x(i);
    if(Number.isFinite(d)){
      const end=zero-d/max*span;
      svg+=`<line class="delta-stem" x1="${cx}" x2="${cx}" y1="${zero}" y2="${end}"/><circle class="delta-point" cx="${cx}" cy="${end}" r="5"><title>${kind==='pressure'?'Junction J-':'Pipe P-'}${html(id)}: ${d>=0?'+':''}${d.toFixed(3)} ${unit}</title></circle>`;
    }
    svg+=`<text x="${cx}" y="${height-14}" text-anchor="middle">${kind==='pressure'?'J-':'P-'}${html(id)}</text>`;
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
  $('pressureChartNote').textContent=`${pressureCount}/${pressureIds.length} junction pressures observed. ${pressureCount<pressureIds.length?'Missing measurements are left blank.':'Blue is healthy; orange is the injected/observed alert snapshot.'}`;
  $('flowChartNote').textContent=`${flowCount}/${flowIds.length} pipe flows observed. ${flowCount<flowIds.length?'Missing measurements are left blank.':'Values are in L/s; negative values indicate reverse flow.'}`;
}
function traceDescription(event){
  const n=event.node;
  if(n==='monitor')return ['Check the healthy baseline',event.anomaly?.anomalous?`Readings differ from normal (standardised score ${event.anomaly.standardised_residual.toFixed(3)}).`:'No detectable difference from the model baseline.'];
  if(n==='context')return ['Read operational context',`Maintenance ${event.sources?.maintenance||0}, demand schedule ${event.sources?.demand_schedule||0}, weather records ${event.sources?.weather||0}. Empty means no local record, not proof of no activity.`];
  if(n==='hypotheses')return ['Find candidate locations',`${event.shortlist_count||0} type-and-location candidates shortlisted from 99 stored signatures. ${event.shortlist?.length?'First: '+event.shortlist.map(c=>`${cause(c.fault_type)} at ${formatLocation(c)}`).join('; ')+'.':''}`];
  if(n==='simulate')return ['Verify candidates with WNTR',`${event.simulation_count} fresh scenarios tested. ${event.latest?.map(r=>`${cause(r.candidate.fault_type)} at ${formatLocation(r.candidate)}: ${Number.isFinite(r.score)?'mismatch '+r.score.toFixed(3):'simulation failed'}`).join('; ')||''}`];
  if(n==='evidence')return ['Compare measured and predicted values',event.leaders?.length?`Current best: ${cause(event.leaders[0].candidate.fault_type)} at ${formatLocation(event.leaders[0].candidate)} (mismatch ${event.leaders[0].score.toFixed(3)}). ${event.ambiguous?'Leading scenarios still overlap.':'Best match is distinct so far.'}`:'No successful simulations to rank.'];
  if(n==='diagnose')return ['Prepare operator diagnosis',`Recommended response: ${event.action||'check evidence'}.`];
  if(n==='sensor_planner')return ['Look for a useful next measurement',event.sensor_recommendation?`A pressure measurement at junction J-${event.sensor_recommendation.junction_id} could separate the two leaders by ${event.sensor_recommendation.predicted_difference_m} m.`:'No additional unmeasured junction was proposed for this result.'];
  if(n==='report')return ['Assemble the final report','Ranked causes, sensor evidence, uncertainty and human-facing advice are ready.'];
  if(n==='no_alert')return ['Stop after baseline check','The available readings do not trigger a fault investigation.'];
  return [n,'Workflow step completed.'];
}
function renderTrace(){
  const events=state.events.filter(e=>e.type==='node');
  $('traceList').innerHTML=events.length?events.map(event=>{const [title,description]=traceDescription(event);return `<li><div><strong>${html(title)}</strong><p>${html(description)}</p><small>${event.elapsed_ms??0} ms since start</small><details><summary>Inspect recorded step</summary><pre>${html(JSON.stringify(event,null,2))}</pre></details></div></li>`}).join(''):'<li class="empty">The reasoning steps will appear here after you run an investigation.</li>';
  $('traceState').textContent=state.running?'Running':events.length?'Recorded run':'Not started';
}
function renderTelemetry(){
  const t=state.tracing;
  $('telemetryStatus').innerHTML=`<span class="trace-pill on">Local step log: active</span><span class="trace-pill ${t?.langsmith_configured?'on':'off'}">LangSmith: ${t?.langsmith_configured?'configured':'off'}</span><span class="trace-pill ${t?.agentops_configured?'on':'off'}">AgentOps: ${t?.agentops_configured?'configured':'off'}</span>${t?.langsmith_project?`<span>Project: ${html(t.langsmith_project)}</span>`:''}`;
}
function renderResult(){
  const el=$('resultCard'),r=state.report;
  if(!r){el.innerHTML='<p class="eyebrow">DIAGNOSIS</p><h3>No investigation yet</h3><p>Results will show the best tested causes, measured evidence, ambiguity, and operator recommendations.</p>';return}
  const ranked=r.ranked_faults||[],lead=ranked[0],truth=state.truth;
  let body=`<p class="eyebrow">DIAGNOSIS REPORT · ${html(r.network_name)}</p><button type="button" id="exportReport" class="text-button">Download report JSON</button><h3>${lead?`${cause(lead.candidate.fault_type)} near ${formatLocation(lead.candidate)}`:'No fault candidate tested'}</h3>`;
  body+=`<div class="status-banner ${r.ambiguous?'ambiguous':'good'}"><strong>${r.ambiguous?'Ambiguous · do not treat rank 1 as confirmed':'Distinct best tested match · field verification required'}</strong><span>${html(r.reason)}</span></div>`;
  body+=`<div class="result-stats"><div class="stat-pill"><b>${r.simulation_count}</b>online verification runs</div><div class="stat-pill"><b>${r.offline_simulation_count}</b>one-time catalog runs</div><div class="stat-pill"><b>${r.elapsed_ms} ms</b>actual graph runtime</div></div>`;
  const changes=readingList().slice(0,3);
  body+='<div class="note-box"><strong>What changed in the measured network</strong>';
  body+=changes.length?`<ul>${changes.map(e=>`<li>${html(label(e.type,e.id))}: ${fmt(e.base,e.type)} healthy → ${fmt(e.value,e.type)} observed (${e.delta>=0?'+':''}${fmt(e.delta,e.type)})</li>`).join('')}</ul>`:'<p>No measured change was available.</p>';
  body+='<p>These are sensor changes. The cause below is a tested model hypothesis, not a field-confirmed event.</p></div>';
  const context=r.context||{};
  body+=`<p class="context-summary">Operational records supplied: ${context.maintenance?.length||0} maintenance, ${context.demand_schedule?.length||0} demand schedule, ${context.weather?.length||0} weather. Empty records mean no information was supplied.</p>`;
  if(truth){const correct=lead&&lead.candidate.fault_type===truth.fault_type&&lead.candidate.location_id===truth.location_id;body+=`<div class="note-box"><strong>Demo ground truth, revealed after investigation:</strong> ${cause(truth.fault_type)} at ${formatLocation(truth)}. ${correct?'The first-ranked type and location match.':'The first-ranked type and location do not match.'} The hidden cause was not sent to the investigation request.</div>`}
  if(lead){body+='<h3 style="margin-top:20px">Ranked explanations</h3>';
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
  $('exportReport').addEventListener('click',()=>{
    const file=new Blob([JSON.stringify(r,null,2)],{type:'application/json'}),url=URL.createObjectURL(file),link=document.createElement('a');
    link.href=url;link.download='waterguard-report.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
}
async function runInvestigation(){
  if(!readingCount()||state.running)return;
  state.running=true;state.report=null;state.runError=null;state.events=[];renderSummary();renderTrace();renderResult();
  $('runTitle').textContent='Investigation running';$('runSubtitle').textContent='Validating readings and comparing hydraulic scenarios…';
  try{
    const {session_id}=await json('/api/investigations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({network_name:'Net1',observed:state.readings,budget:8})});
    await new Promise((resolve,reject)=>{
      const source=new EventSource(`/api/investigations/${session_id}/events`);
      source.onmessage=async message=>{
        const event=JSON.parse(message.data);
        if(event.type==='node'){state.events.push(event);renderTrace()}
        if(event.type==='complete'||event.type==='error'){
          source.close();if(event.type==='error'){reject(new Error(event.message));return}
          try{const finished=await json(`/api/investigations/${session_id}`);if(finished.status!=='complete'||!finished.result)throw new Error('Report was not returned');state.report=finished.result;resolve()}catch(e){reject(e)}
        }
      };
      source.onerror=()=>{source.close();reject(new Error(`Progress stream disconnected. Session ${session_id}`))};
    });
    renderAll();$('runTitle').textContent='Investigation complete';$('runSubtitle').textContent=`${state.report.simulation_count} online scenarios verified in ${state.report.elapsed_ms} ms. The timeline records actual steps; no delay was added.`;
  }catch(error){state.runError=error.message;toast(`Investigation failed: ${error.message}`)}
  finally{state.running=false;renderSummary();renderTrace();renderResult()}
}
function renderAll(){renderSummary();renderReadings();renderMap();renderCharts();renderTrace();renderResult()}

document.querySelectorAll('.scenario-card').forEach(el=>el.addEventListener('click',()=>loadDemo(el.dataset.demo)));
$('faultType').addEventListener('change',populateFaultLocations);
$('injectFault').addEventListener('click',loadCustomFault);
$('inputKind').addEventListener('change',populateInputSensors);
$('inputSensor').addEventListener('change',showBaselineHint);
$('addReading').addEventListener('click',addReading);
$('inputValue').addEventListener('keydown',event=>{if(event.key==='Enter')addReading()});
$('clearReadings').addEventListener('click',clearReadings);
$('runButton').addEventListener('click',runInvestigation);
init();
