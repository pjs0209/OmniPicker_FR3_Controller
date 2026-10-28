// Local QA against two --demo servers on 8098/8099 and dedicated Chrome on 9224.
// No packages needed: Node 22+ provides fetch and WebSocket.
import {writeFile, mkdir} from 'node:fs/promises';
import assert from 'node:assert/strict';

await mkdir(new URL('./screenshots/', import.meta.url), {recursive: true});
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function page(port,path="") {
  const target = await (await fetch(`http://127.0.0.1:9224/json/new?${encodeURIComponent('http://127.0.0.1:' + port+path)}`, {method: 'PUT'})).json();
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  let id = 0;
  const pending = new Map(), errors = [];
  socket.onmessage = message => {
    const data = JSON.parse(message.data);
    if (data.method === 'Runtime.exceptionThrown') errors.push(data.params.exceptionDetails.text + ': ' + (data.params.exceptionDetails.exception?.description || ''));
    if (data.id) {
      const promise = pending.get(data.id);
      if (!promise) return;
      pending.delete(data.id);
      if (data.error) promise.reject(Error(data.error.message)); else promise.resolve(data.result);
    }
  };
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const requestId = ++id;
    pending.set(requestId, {resolve, reject}); socket.send(JSON.stringify({id: requestId, method, params}));
  });
  await send('Runtime.enable'); await send('Page.enable');
  const evaluate = async expression => {
    const response = await send('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
    if (response.exceptionDetails) throw Error(response.exceptionDetails.exception?.description || response.exceptionDetails.text);
    return response.result.value;
  };
  const wait = async (expression, label) => {
    for (let attempt = 0; attempt < 100; attempt++) {
      if (await evaluate(expression)) return;
      await pause(100);
    }
    throw Error('Timed out: ' + label + '\n' + await evaluate('document.body.innerText'));
  };
  const click = selector => evaluate(`(() => {const element = document.querySelector(${JSON.stringify(selector)}); if (!element) throw Error('missing element'); if (element.disabled) throw Error('disabled: ' + ${JSON.stringify(selector)}); element.click();})()`);
  const screenshot = async name => {
    const {cssContentSize} = await send('Page.getLayoutMetrics');
    const {data} = await send('Page.captureScreenshot', {format: 'png', captureBeyondViewport: true, clip: {x: 0, y: 0, width: cssContentSize.width, height: cssContentSize.height, scale: 1}});
    await writeFile(new URL(`./screenshots/${name}.png`, import.meta.url), Buffer.from(data, 'base64'));
  };
  const viewport = async width => {
    await send('Emulation.setDeviceMetricsOverride', {width, height: 1050, deviceScaleFactor: 1, mobile: false}); await pause(250);
  };
  await viewport(1440);
  await wait('document.readyState === "complete" && !!window.UI && !!document.querySelector("#events .event")', 'application load');
  assert.equal(await evaluate('fetch("/api/status").then(r=>r.json()).then(s=>s.mode)'), 'demo', 'QA refuses hardware mode');
  return {send, evaluate, wait, click, screenshot, viewport, errors, close: () => socket.close()};
}

const c=await page(8098);
const set=(id,value)=>c.evaluate(`(()=>{const e=document.getElementById(${JSON.stringify(id)});e.value=${JSON.stringify(value)};e.dispatchEvent(new Event(e.tagName==='SELECT'?'change':'input'));})()`);
const robot=()=>c.evaluate('fetch("/api/status").then(r=>r.json()).then(s=>s.robot)');
async function press(kind,axis){
  const p=await c.evaluate(`(()=>{
    const v=OMNI_VIEW.viewer;v.scene.updateMatrixWorld(true);let tip;
    const group=${kind==='joint'?'v.gizmos.joints['+axis+']':'v.gizmos.tool'};
    group.traverse(n=>{const a=n.userData.action;if(a?.kind===${JSON.stringify(kind)}&&a.sign===1&&${kind==='joint'?'a.joint':'a.axis'}===${axis})tip=n;});
    if(!tip)throw Error('missing handle');const world=tip.getWorldPosition(v.camera.position.clone()),r=v.renderer.domElement.getBoundingClientRect(),p=world.project(v.camera);
    return {x:r.left+(p.x+1)*r.width/2,y:r.top+(1-p.y)*r.height/2};
  })()`);
  await c.send('Input.dispatchMouseEvent',{type:'mousePressed',...p,button:'left',buttons:1,clickCount:1});
  assert.equal(await c.evaluate('!!OMNI_VIEW.viewer.gizmos.drag'),true,'pointer captures selected handle');
  assert.equal(await c.evaluate('OMNI_VIEW.viewer.controls.enabled'),false,'camera suspended while dragging');
  await c.evaluate(`(()=>{const d=OMNI_VIEW.viewer.gizmos.drag;window.__dragQA={start:${JSON.stringify(p)},centre:d.centre.clone(),axis:d.axis.clone(),last:d.last?.clone(),origin:d.origin?.clone(),planar:d.planar,rotation:d.rotation,screen:d.screenAxis.clone(),scale:d.scale,amount:0};})()`);
}
async function move(amount){
  const p=await c.evaluate(`(()=>{
    const d=window.__dragQA,v=OMNI_VIEW.viewer;let world;
    if(d.planar&&d.rotation)world=d.last.clone().applyAxisAngle(d.axis,${amount}*Math.PI/180).multiplyScalar(.07).add(d.centre);
    else if(d.planar)world=d.origin.clone().addScaledVector(d.axis,${amount}/1000);
    if(world){const p=world.project(v.camera),r=v.renderer.domElement.getBoundingClientRect();return {x:r.left+(p.x+1)*r.width/2,y:r.top+(1-p.y)*r.height/2};}
    const pixels=${amount}/(d.rotation?180/Math.PI:1000)*d.scale;return {x:d.start.x+pixels*d.screen.x,y:d.start.y+pixels*d.screen.y};
  })()`);
  await c.send('Input.dispatchMouseEvent',{type:'mouseMoved',...p,button:'left',buttons:1});
  return p;
}
async function release(p){
  await c.send('Input.dispatchMouseEvent',{type:'mouseReleased',...p,button:'left',buttons:0,clickCount:1});
  assert.equal(await c.evaluate('OMNI_VIEW.viewer.controls.enabled'),true,'camera restored on release');
  assert.equal(await c.evaluate('!!OMNI_VIEW.viewer.gizmos.drag'),false);
}
try{
  await c.click('#connect');await c.wait('!!OMNI_VIEW.viewer?.gizmos&&document.querySelector("#robotBadge").textContent==="연결됨"','connected');
  assert.equal(await c.evaluate('getComputedStyle(document.querySelector("#robotPanel")).display!=="none"'),true);
  await set('motionMode','joint_axes');await c.click('#viewHome');await pause(300);
  assert.equal(await c.evaluate('OMNI_VIEW.viewer.gizmos.joints.every(g=>g.children.length===1)'),true,'only pickable rotation controls remain');
  const q=(await robot()).joints;
  await press('joint',0);let p=await move(8.345);await release(p);
  await c.wait('document.querySelector("#planStatus").textContent.includes("적용 대기")','joint drag goal');
  assert.ok(Math.abs(await c.evaluate('OMNI_VIEW.preview[0]')-8.345)<.02);assert.deepEqual((await robot()).joints,q);
  await c.screenshot('console-joint-desktop');
  await set('motionMode','tool');await c.click('#viewHome');await pause(300);
  await c.wait('!document.querySelector("#tcpPanel").hidden','TCP tab');
  const before=await c.evaluate('OMNI_VIEW.goalPose.slice()');
  await c.evaluate('window.originalConsoleFetch=window.fetch;window.fetch=async(...args)=>{if(String(args[0]).includes("/api/robot/preview"))await new Promise(r=>setTimeout(r,700));return window.originalConsoleFetch(...args);}');
  await press('tool',1);
  const beforeMarker=await c.evaluate('OMNI_VIEW.viewer.gizmos.tool.position.toArray()');
  p=await move(6.123);
  const draft=await c.evaluate('OMNI_VIEW.goalPose.slice()');
  const marker=await c.evaluate('OMNI_VIEW.viewer.gizmos.tool.position.toArray()');
  assert.ok(Math.hypot(...draft.slice(0,3).map((v,i)=>v-before[i]))>6,'TCP numbers update immediately while IK is delayed');
  assert.ok(Math.hypot(...marker.map((v,i)=>v-beforeMarker[i]))>.006,'target gizmo updates immediately while IK is delayed');
  assert.ok(Math.abs(await c.evaluate('Number(document.querySelector("#delta1").value)')-6.123)<.001);
  await release(p);await c.wait('document.querySelector("#planStatus").textContent.includes("적용 대기")','IK updates joints after delayed response');
  assert.ok(await c.evaluate('OMNI_VIEW.preview.some((v,i)=>Math.abs(v-OMNI_VIEW.state.robot.joints[i])>.1)'));
  assert.ok(await c.evaluate('Array.from({length:3},(_,i)=>document.getElementById("goalTcp"+i).textContent!==document.getElementById("tcp"+i).textContent).some(Boolean)'));
  assert.deepEqual((await robot()).joints,q,'goal editing has no physical motion');
  await c.evaluate('window.fetch=window.originalConsoleFetch');
  await c.screenshot('console-tcp-desktop');
  // A late reply from a previous mode must never overwrite the newly selected goal.
  await set('delta0',3);await set('motionMode','joint');await set('target0',12.5);
  await c.wait('document.querySelector("#planStatus").textContent.includes("적용 대기")','mode change latest target');
  assert.equal(await c.evaluate('OMNI_VIEW.preview[0]'),12.5);
  await c.click('[data-workspace="points"]');await c.evaluate('window.prompt=()=>"검증 포인트"');await c.click('#saveTargetPoint');
  await c.wait('document.querySelectorAll(".point-card").length===1','saved target waypoint');
  await c.click('[data-workspace="operate"]');await set('target0',4.5);
  await c.click('[data-workspace="points"]');await c.click('.point-card button');
  await c.wait('!document.querySelector("#workspaceOperate").hidden&&OMNI_VIEW.preview[0]===12.5','load waypoint as preview');
  assert.deepEqual((await robot()).joints,q,'loading waypoint never moves robot');
  await c.click('[data-workspace="points"]');
  await c.evaluate(`(()=>{const data={version:1,points:[{name:'Imported',joints:[1,-65,100,-125,-90,0],velocity:7,tool:0,user:0}]};const files=new DataTransfer();files.items.add(new File([JSON.stringify(data)],'points.json',{type:'application/json'}));const input=document.querySelector('#pointFile');input.files=files.files;input.dispatchEvent(new Event('change'));})()`);
  await c.wait('document.querySelector(".point-card h3").textContent==="Imported"','import waypoints');
  await c.click('[data-workspace="diagnostics"]');await c.wait('document.querySelectorAll("#deviceMetrics article").length===6','device diagnostics');
  await c.click('[data-workspace="configuration"]');await c.evaluate('document.querySelector("#sdkFeatures").open=true');
  await c.wait('document.querySelectorAll(".sdk-feature").length>=690','SDK feature inventory retained');
  await c.click('[data-workspace="operate"]');await c.click('[data-panel="grip"]');
  await c.click('#recoverGripper');await c.wait('document.querySelector("#gripBadge").textContent==="연결됨"&&!document.querySelector("#recoverGripper").disabled','RS485 recovery');
  assert.equal((await robot()).armed,false);assert.deepEqual((await robot()).joints,q);
  await c.screenshot('console-gripper-desktop');
  await c.viewport(390);assert.equal(await c.evaluate('document.documentElement.scrollWidth<=390'),true,'mobile has no overflow');
  await c.screenshot('console-mobile');assert.deepEqual(c.errors,[]);
  process.stdout.write('PASS console: only pickable joint gizmos, immediate TCP target/marker before IK, IK ghost updates, latest-mode replies, waypoint save/load/import, diagnostics, SDK inventory, motion-free bus recovery, desktop/mobile\n');
}finally{c.close();}
