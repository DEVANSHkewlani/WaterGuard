// Household onboarding uses the same WaterGuard model and investigation endpoints.
'use strict';
const onboard = {draft:null, initial:null, undo:[], selected:null, connecting:null, path:null, committed:false, modelId:null, sensorTimer:null};
const ob = id => document.getElementById(id);
const cloneDraft = x => JSON.parse(JSON.stringify(x));
function note(message, bad=false){ob('onboardMessage').textContent=message;ob('onboardMessage').className=bad?'onboard-error':''}
function snapshot(){if(onboard.draft){onboard.undo.push(cloneDraft(onboard.draft));if(onboard.undo.length>40)onboard.undo.shift();onboard.committed=false;onboard.modelId=null;ob('readyPanel').hidden=true}}
function showPanel(name){for(const id of ['diagramPanel','guidedPanel','reviewPanel'])ob(id).hidden=id!==name}
function draftId(prefix){const used=new Set([...onboard.draft.nodes,...onboard.draft.links].map(x=>x.id));let i=1;while(used.has(prefix+i))i++;return prefix+i}
async function openPath(path){
  if(path===onboard.path&&onboard.draft){ob('onboardWorkspace').hidden=false;showPanel('reviewPanel');renderDraft();return}
  onboard.path=path; onboard.selected=null; onboard.connecting=null; onboard.undo=[];onboard.committed=false;onboard.modelId=null;
  ob('onboardWorkspace').hidden=false;ob('readyPanel').hidden=true;ob('validationFeedback').textContent='';
  ob('onboardBack').textContent='Choose another path';
  ob('estimatedMode').checked=false;ob('diagramPanel').append(ob('diagramPreview'));ob('diagramPreview').innerHTML='';ob('diagramPreview').hidden=true;
  ob('onboardHeading').textContent=path==='diagram'?'Trace your diagram':path==='guided'?'Describe your plumbing':'Draw your water network';
  ob('onboardStep').textContent='STEP 1 · CREATE'; note('');
  if(path==='diagram'){showPanel('diagramPanel');return}
  if(path==='guided'){showPanel('guidedPanel');ob('guideBasics').hidden=false;ob('guideDetails').hidden=true;return}
  try{const data=await json('/api/onboarding/new',{method:'POST'});setDraft(data.draft)}
  catch(e){note(e.message,true)}
}
function setDraft(draft){onboard.draft=draft;onboard.initial=cloneDraft(draft);onboard.undo=[];onboard.selected=null;showPanel('reviewPanel');ob('onboardStep').textContent='STEP 2 · REVIEW CONNECTIONS';renderDraft();note('Review the map and fill in measurements you know. Unknown values remain visible.')}
function renderDraft(){
  const d=onboard.draft;if(!d)return;
  ob('draftName').value=d.name;
  const byId=Object.fromEntries(d.nodes.map(n=>[n.id,n]));
  const svg=ob('draftMap');
  svg.innerHTML=d.links.map(e=>{
    const a=byId[e.start],b=byId[e.end];if(!a||!b)return '';
    return '<g class="draft-link" data-id="'+html(e.id)+'"><line x1="'+a.x+'" y1="'+a.y+'" x2="'+b.x+'" y2="'+b.y+'"></line><text x="'+((a.x+b.x)/2)+'" y="'+((a.y+b.y)/2-8)+'">'+html(e.name)+'</text></g>';
  }).join('')+d.nodes.map(n=>'<g class="draft-node '+(onboard.selected?.id===n.id?'selected':'')+'" data-id="'+html(n.id)+'" transform="translate('+n.x+','+n.y+')"><circle r="22"></circle><text text-anchor="middle" y="5">'+html(n.kind==='source'?'S':n.kind==='tank'?'T':n.kind==='outlet'?'O':'B')+'</text><text class="node-label" text-anchor="middle" y="40">'+html(n.name)+'</text></g>').join('');
  svg.querySelectorAll('.draft-link').forEach(el=>el.addEventListener('click',()=>{onboard.selected={type:'link',id:el.dataset.id};renderDraft()}));
  svg.querySelectorAll('.draft-node').forEach(el=>{
    el.addEventListener('click',()=>{
      if(onboard.connecting){
        if(!onboard.connecting.start){onboard.connecting.start=el.dataset.id;ob('draftMapHint').textContent='Select the other point.'}
        else if(onboard.connecting.start!==el.dataset.id){
          snapshot();const kind=onboard.connecting.kind;const id=draftId(kind==='pipe'?'Pipe':kind==='pump'?'Pump':'Valve');
          d.links.push({id,name:id,kind,start:onboard.connecting.start,end:el.dataset.id,length_m:null,diameter_mm:null,material:null,pump_power_kw:null,valve_setting:null,provenance:{start:'user',end:'user',kind:'user'}});
          onboard.connecting=null;ob('draftMapHint').textContent='Connection added. Select it to enter measurements.';renderDraft();
        }
      }else{onboard.selected={type:'node',id:el.dataset.id};renderDraft()}
    });
    el.addEventListener('pointerdown',ev=>{
      if(onboard.connecting)return;
      const n=d.nodes.find(x=>x.id===el.dataset.id);if(!n)return;let moved=false;
      const move=event=>{if(!moved){snapshot();moved=true}const box=svg.getBoundingClientRect();n.x=Math.max(25,Math.min(775,(event.clientX-box.left)*800/box.width));n.y=Math.max(25,Math.min(455,(event.clientY-box.top)*480/box.height));el.setAttribute('transform','translate('+n.x+','+n.y+')');d.links.forEach(link=>{if(link.start===n.id||link.end===n.id){const line=svg.querySelector('.draft-link[data-id="'+link.id+'"] line');const a=byId[link.start],b=byId[link.end];if(line){line.setAttribute('x1',a.x);line.setAttribute('y1',a.y);line.setAttribute('x2',b.x);line.setAttribute('y2',b.y)}}})};
      const end=()=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',end);if(moved)renderDraft()};
      window.addEventListener('pointermove',move);window.addEventListener('pointerup',end);
    });
  });
  renderInspector();
}
function field(label,key,value,type='text',choices=null){
  const v=value??'';
  if(choices)return '<label>'+html(label)+'<select data-field="'+key+'">'+choices.map(c=>'<option value="'+html(c)+'" '+(c===v?'selected':'')+'>'+html(c)+'</option>').join('')+'</select></label>';
  return '<label>'+html(label)+'<input data-field="'+key+'" type="'+type+'" '+(type==='number'?'step="any" ':'')+'value="'+html(v)+'" placeholder="'+(type==='number'?'Unknown':'')+'"></label>';
}
function renderInspector(){
  const box=ob('draftInspector'),sel=onboard.selected,d=onboard.draft;
  if(!sel){box.innerHTML='<p>Select a point or connection to edit its details. Empty numbers mean unknown.</p>';return}
  const item=(sel.type==='node'?d.nodes:d.links).find(x=>x.id===sel.id);
  if(!item){onboard.selected=null;renderInspector();return}
  let fields=field('Friendly name','name',item.name);
  if(sel.type==='node'){
    fields+=field('Type','kind',item.kind,'text',['source','tank','junction','outlet'])+
      field('Elevation (m)','elevation_m',item.elevation_m,'number');
    if(item.kind==='source')fields+=field('Supply head (m)','head_m',item.head_m,'number');
    if(item.kind==='tank')fields+=field('Water level (m)','tank_level_m',item.tank_level_m,'number')+field('Tank diameter (m)','tank_diameter_m',item.tank_diameter_m,'number');
    if(item.kind==='junction'||item.kind==='outlet')fields+=field('Expected water use (L/s)','demand_lps',item.demand_lps,'number');
  }else{
    const ids=d.nodes.map(x=>x.id);
    fields+=field('Type','kind',item.kind,'text',['pipe','pump','valve'])+
      field('From','start',item.start,'text',ids)+field('To','end',item.end,'text',ids);
    if(item.kind==='pipe')fields+=field('Length (m)','length_m',item.length_m,'number')+field('Inside diameter (mm)','diameter_mm',item.diameter_mm,'number')+field('Material','material',item.material);
    if(item.kind==='pump')fields+=field('Pump power (kW)','pump_power_kw',item.pump_power_kw,'number');
    if(item.kind==='valve')fields+=field('Diameter (mm)','diameter_mm',item.diameter_mm,'number')+field('Valve setting','valve_setting',item.valve_setting,'number');
  }
  box.innerHTML='<strong>'+html(item.name)+'</strong><small>Stable ID: '+html(item.id)+'</small><div class="inspector-fields">'+fields+'</div><p class="input-hint">Changed fields are recorded as supplied by you. Other blanks remain unknown.</p><button class="text-button" type="button" id="removeDraftElement">Delete '+(sel.type==='node'?'point':'connection')+'</button>';
  box.querySelectorAll('[data-field]').forEach(input=>{const tag=document.createElement('small');tag.textContent='Source: '+(item[input.dataset.field]===null||item[input.dataset.field]===''?'unknown':item.provenance?.[input.dataset.field]||'unknown');input.parentElement.append(tag)});
  box.querySelectorAll('[data-field]').forEach(input=>input.addEventListener('change',()=>{
    snapshot();const k=input.dataset.field;item[k]=input.type==='number'?(input.value===''?null:Number(input.value)):input.value;item.provenance??={};item.provenance[k]='user';renderDraft();
  }));
  ob('removeDraftElement').onclick=()=>{snapshot();if(sel.type==='node'){d.nodes=d.nodes.filter(x=>x.id!==sel.id);d.links=d.links.filter(x=>x.start!==sel.id&&x.end!==sel.id)}else d.links=d.links.filter(x=>x.id!==sel.id);onboard.selected=null;renderDraft()};
}
async function saveCurrent(){if(!onboard.draft)return;onboard.draft.name=ob('draftName').value.trim()||'My water network';await json('/api/onboarding/draft',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(onboard.draft)});note('Draft saved in this browser.')}
async function validateCurrent(){
  try{
    await saveCurrent();const estimated=ob('estimatedMode').checked;
    const result=await json('/api/onboarding/validate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({draft:onboard.draft,estimated})});
    const feedback=ob('validationFeedback');
    feedback.innerHTML='<strong>'+html(result.hydraulic_valid?'Hydraulic check passed':result.structural_valid?'Connections look valid; hydraulic check failed':'Please correct these items')+'</strong>'+
      (result.errors.length?'<ul>'+result.errors.map(x=>'<li>'+html(x)+'</li>').join('')+'</ul>':'')+
      (result.assumptions.length?'<details open><summary>Unknown values ('+result.assumptions.length+')</summary><ul>'+result.assumptions.map(x=>'<li>'+html(x)+'</li>').join('')+'</ul></details>':'')+
      (result.assumed_values?.length?'<details open><summary>Values used by the model ('+result.assumed_values.length+')</summary><ul>'+result.assumed_values.map(x=>'<li>'+html(x.element)+': '+html(x.field.replaceAll('_',' '))+' = '+html(x.value)+(x.note?' · '+html(x.note):' · demo assumption')+'</li>').join('')+'</ul></details>':'');
    if(!result.hydraulic_valid)return;
    feedback.innerHTML+='<p>Preparing this exact network’s healthy baseline and fault catalog…</p>';
    const response=await json('/api/onboarding/commit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({draft:onboard.draft,estimated})});
    ob('onboardStep').textContent='STEP 3 · PREPARE MODEL';
    for(let attempt=0;attempt<120;attempt++){
      const status=await json('/api/onboarding/catalog/'+encodeURIComponent(response.network_name));
      feedback.querySelector('p').textContent=status.status==='building'?'Building fault catalog: '+status.done+' / '+status.total+' scenarios…':status.status==='error'?'Catalog failed: '+status.error:'Healthy baseline and catalog are ready.';
      if(status.status==='error')throw new Error(status.error);
      if(status.status==='ready')break;
      await new Promise(resolve=>setTimeout(resolve,750));
      if(attempt===119)throw new Error('Catalog is still building. Check again in a moment.');
    }
    onboard.committed=true;onboard.modelId=response.network_name;await loadAvailableNetworks(response.network_name);await switchNetwork(response.network_name);
    ob('onboardStep').textContent='STEP 4 · TEST OR MEASURE';
    ob('readyPanel').hidden=false;ob('readyStatus').textContent=estimated?'Exploratory demo model. Estimated measurements are assumptions, not field observations. Results cannot localize a real fault.':'Structural and hydraulic checks passed. This model is not calibrated against real measurements.';
    note('Your network is active. Choose a simulated test or enter actual measurements.');
  }catch(e){note(e.message,true)}
}
async function startHouseholdTest(){
  if(!onboard.committed)return;
  try{
    const fault_type=ob('faultType').value,location_id=ob('faultLocation').value;
    const data=await json('/api/demo/custom',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({network_name:onboard.modelId,fault_type,location_id,severity:.55})});
    state.scenario='custom';state.truth=data.ground_truth;state.readings=data.observed;state.series=data.series;clearResult();renderAll();
    document.getElementById('investigation').scrollIntoView({behavior:'smooth'});
    await runInvestigation();
  }catch(e){note('Test failed: '+e.message,true)}
}
document.querySelectorAll('[data-path]').forEach(button=>button.addEventListener('click',()=>openPath(button.dataset.path)));
ob('onboardBack').onclick=()=>{
  if(!ob('reviewPanel').hidden&&onboard.draft&&onboard.path!=='manual'){
    showPanel(onboard.path==='guided'?'guidedPanel':'diagramPanel');
    ob('onboardBack').textContent='Return to review';
  }else if(ob('reviewPanel').hidden&&onboard.draft){showPanel('reviewPanel');renderDraft();ob('onboardBack').textContent='Choose another path'}
  else{ob('onboardWorkspace').hidden=true}
};
ob('diagramUpload').onclick=async()=>{
  const file=ob('diagramFile').files[0];if(!file){note('Choose a diagram first.',true);return}
  const form=new FormData();form.append('file',file);form.append('consent_external',ob('externalVisionConsent').checked?'true':'false');
  try{const result=await json('/api/onboarding/diagram',{method:'POST',body:form});setDraft(result.draft);
    const url='/api/onboarding/document/'+encodeURIComponent(result.draft.id);
    ob('diagramPreview').innerHTML=result.draft.document.type==='pdf'?'<object data="'+url+'" type="application/pdf" aria-label="Uploaded drawing"></object>':'<img src="'+url+'" alt="Uploaded drawing">';
    ob('diagramPreview').hidden=false;ob('reviewPanel').prepend(ob('diagramPreview'));note(result.message);
  }catch(e){note(e.message,true)}
};
ob('guideCreate').onclick=async()=>{
  const answers={source:ob('guideSource').value,pump:ob('guidePump').value==='yes',floors:ob('guideFloors').value,outlets:ob('guideOutlets').value,issue:ob('guideIssue').value,sensors:ob('guideSensors').value,
    length_m:ob('guideLength').value||null,diameter_mm:ob('guideDiameter').value||null,head_m:ob('guideHead').value||null};
  if(!answers.outlets.trim()){note('Name at least one outlet or room.',true);return}
  try{const result=await json('/api/onboarding/guided',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(answers)});setDraft(result.draft);note('Connections are proposed from your answers. Confirm each route and fill in what you know.')}
  catch(e){note(e.message,true)}
};
ob('guideNext').onclick=()=>{ob('guideBasics').hidden=true;ob('guideDetails').hidden=false};
ob('guidePrevious').onclick=()=>{ob('guideBasics').hidden=false;ob('guideDetails').hidden=true};
ob('addNode').onclick=()=>{if(!onboard.draft)return;snapshot();const kind=ob('addNodeKind').value,id=draftId(kind==='source'?'Source':kind==='tank'?'Tank':kind==='outlet'?'Outlet':'Branch');onboard.draft.nodes.push({id,name:kind==='source'?'Main supply':kind==='outlet'?'Outlet':id,kind,x:120+Math.random()*500,y:90+Math.random()*260,elevation_m:null,demand_lps:null,head_m:null,tank_level_m:null,tank_diameter_m:null,provenance:{kind:'user',name:'user'}});onboard.selected={type:'node',id};renderDraft()};
ob('connectMode').onclick=()=>{onboard.connecting={kind:ob('addLinkKind').value,start:null};ob('draftMapHint').textContent='Select the first point, then the second point.'};
ob('undoDraft').onclick=()=>{const prior=onboard.undo.pop();if(prior){onboard.draft=prior;onboard.selected=null;onboard.committed=false;onboard.modelId=null;ob('readyPanel').hidden=true;renderDraft()}};
ob('resetDraft').onclick=()=>{if(onboard.initial){snapshot();onboard.draft=cloneDraft(onboard.initial);onboard.selected=null;renderDraft()}};
ob('saveDraft').onclick=()=>saveCurrent().catch(e=>note(e.message,true));
ob('validateDraft').onclick=validateCurrent;
ob('testNetwork').onclick=startHouseholdTest;
function sensorFields(){
  const pressure=ob('registeredSensorKind').value==='pressure';
  const ids=pressure?state.topology?.junctions||[]:state.topology?.pipes||[];
  ob('registeredSensorElement').innerHTML=ids.map(id=>'<option value="'+html(id)+'">'+html(id)+'</option>').join('');
  ob('registeredSensorUnit').innerHTML=(pressure?['m','kPa']:['L/s','m3/s']).map(unit=>'<option value="'+unit+'">'+unit+'</option>').join('');
}
ob('configureSensors').onclick=()=>{ob('sensorSetup').hidden=false;sensorFields()};
ob('registeredSensorKind').onchange=sensorFields;
ob('registerSensor').onclick=async()=>{
  try{
    const name=onboard.modelId;
    const existing=await json('/api/onboarding/sensors/'+name);
    const entry={sensor_id:ob('registeredSensorId').value.trim(),kind:ob('registeredSensorKind').value,
      element_id:ob('registeredSensorElement').value,unit:ob('registeredSensorUnit').value,
      interval_s:Number(ob('registeredSensorInterval').value)};
    const sensors=[...existing.sensors.filter(x=>x.sensor_id!==entry.sensor_id),entry];
    const result=await json('/api/onboarding/sensors/'+name,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({sensors})});
    ob('sensorStreamStatus').textContent=result.sensors.length+' simulated sensor(s) registered. No physical devices are connected.';
  }catch(e){ob('sensorStreamStatus').textContent=e.message}
};
async function sampleStream(){
  if(!onboard.modelId||state.networkName!==onboard.modelId){clearInterval(onboard.sensorTimer);onboard.sensorTimer=null;return}
  try{
    const data=await json('/api/onboarding/sensors/'+onboard.modelId+'/sample');
    const readings={pressures:{},flows:{}};
    for(const row of data.readings){const isPressure=row.kind==='pressure';readings[isPressure?'pressures':'flows'][row.element_id]=row.value/(row.unit==='kPa'?9.80665:row.unit==='L/s'?1000:1)}
    state.readings=readings;state.scenario='sensor-simulated';state.truth=null;clearResult();renderAll();
    ob('sensorStreamStatus').innerHTML='<strong>SIMULATED · '+html(data.readings[0]?.measured_at||'')+'</strong><p>'+data.readings.map(x=>html(x.sensor_id)+': '+Number(x.value).toFixed(2)+' '+html(x.unit)).join(' · ')+'</p><button type="button" class="button primary" id="investigateSample">Investigate these simulated readings</button>';
    ob('investigateSample').onclick=()=>{document.getElementById('investigation').scrollIntoView({behavior:'smooth'});runInvestigation()};
  }catch(e){ob('sensorStreamStatus').textContent='Simulated stream error: '+e.message}
}
ob('startSensorStream').onclick=async()=>{clearInterval(onboard.sensorTimer);await sampleStream();const interval=Math.max(5,Math.min(3600,Number(ob('registeredSensorInterval').value)||10));onboard.sensorTimer=setInterval(sampleStream,interval*1000)};
ob('stopSensorStream').onclick=()=>{clearInterval(onboard.sensorTimer);onboard.sensorTimer=null;ob('sensorStreamStatus').textContent='Simulated stream stopped.'};
ob('deleteNetwork').onclick=async()=>{if(!onboard.modelId)return;try{clearInterval(onboard.sensorTimer);await json('/api/onboarding/network/'+onboard.modelId,{method:'DELETE'});ob('onboardWorkspace').hidden=true;onboard.draft=null;onboard.committed=false;onboard.modelId=null;await loadAvailableNetworks('Net2');await switchNetwork('Net2');note('Network deleted.')}catch(e){note(e.message,true)}};
ob('draftName').onchange=()=>{if(onboard.draft){snapshot();onboard.draft.name=ob('draftName').value}};
