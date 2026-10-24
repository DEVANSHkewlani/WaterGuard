// Minimal DOM harness: catches startup errors and checks that the three entry paths are wired.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const elements = new Map();
function element(id) {
  if (!elements.has(id)) {
    let markup = '';
    const el = {
      id, dataset: {}, value: '', textContent: '', disabled: false, hidden: id === 'connectionBanner',
      classList: { add() {}, remove() {}, toggle() {} }, listeners: {},
      addEventListener(name, fn) { this.listeners[name] = fn; },
      querySelectorAll() { return []; },
      get innerHTML() { return markup; },
      set innerHTML(value) { markup = value; const found = value.match(/<option value="([^"]+)"/); if (found) this.value = found[1]; }
    };
    elements.set(id, el);
  }
  return elements.get(id);
}
const cards = ['leak', 'valve', 'surge'].map(kind => {
  const card = element('card-' + kind); card.dataset.demo = kind; return card;
});
const topology = {
  nodes: [{ data: { id:'10', kind:'Junction' }, position: {x:20,y:-70} },
          { data: { id:'11', kind:'Junction' }, position: {x:30,y:-70} }],
  edges: [{ data: {id:'11',source:'10',target:'11',kind:'Pipe'} }],
  junctions:['10','11'], pipes:['11']
};
const readings = { pressures: {'10':40,'11':38}, flows: {'11':.01} };
const times = Array.from({length:13},(_,i)=>i);
const series = {baseline:{hours:times,pressures:{'10':times.map(()=>40),'11':times.map(()=>38)},flows:{'11':times.map(()=>.01)}},
                fault:{hours:times,pressures:{'10':times.map(()=>39),'11':times.map(()=>35)},flows:{'11':times.map(()=>.011)}}};
const calls = [];
const context = vm.createContext({
  document: {getElementById:element,querySelectorAll:selector=>selector==='.scenario-card'?cards:[]},
  window: {location:{protocol:'http:'}},
  fetch: async (url,options) => {
    calls.push(url);
    let value;
    if(url==='/api/network/Net1') value=topology;
    else if(url==='/api/baseline/Net1') value={readings};
    else if(url==='/api/demo/custom') value={observed:readings,ground_truth:{fault_type:'pipe_leak',location_id:'11',severity:.55},series};
    else if(url.startsWith('/api/demo/')&&url.endsWith('/series')) value=series;
    else if(url.startsWith('/api/demo/')) value={observed:readings,ground_truth:{fault_type:'pipe_leak',location_id:'11',severity:.55}};
    else throw new Error('Unexpected request: '+url);
    return {ok:true,json:async()=>value};
  },
  setTimeout:()=>1,clearTimeout:()=>{},console
});
vm.runInContext(fs.readFileSync('frontend/app.js','utf8'),context);
(async()=>{
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(element('connectionBanner').hidden,true); // remains hidden when API works
  assert.ok(element('faultLocation').innerHTML.includes('Pipe P-11'));
  await cards[0].listeners.click();
  assert.ok(element('setupSummary').innerHTML.includes('Synthetic pipe leak alert'));
  assert.ok(element('readingRows').innerHTML.includes('Junction J-11'));
  assert.ok(element('pressureChart').innerHTML.includes('baseline-path'));
  assert.ok(element('pressureChart').innerHTML.includes('fault-path'));
  assert.ok(element('flowChart').innerHTML.includes('P-11'));
  assert.ok(element('networkMap').innerHTML.includes('L/s'));
  element('faultType').value='pipe_leak';element('faultLocation').value='11';element('faultSeverity').value='0.55';
  await element('injectFault').listeners.click();
  assert.ok(calls.includes('/api/demo/custom'));
  assert.ok(element('pressureChart').innerHTML.includes('<svg'));
  element('inputKind').value='pressures';element('inputSensor').value='11';element('inputValue').value='36';
  element('addReading').listeners.click();
  assert.ok(element('setupSummary').innerHTML.includes('Manual sensor alert'));
  assert.ok(element('readingRows').innerHTML.includes('36.00 m'));
  assert.equal(element('runButton').disabled,false);
  console.log('UI smoke: example, custom fault, and manual reading controls work');
})().catch(error=>{console.error(error);process.exitCode=1});
