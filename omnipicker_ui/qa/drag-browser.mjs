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
  await c.click('#connect');await c.wait('!!OMNI_VIEW.viewer?.gizmos&&document.querySelector("#robotBadge").textContent==="연결됨"','ready');
  assert.equal((await robot()).armed,false);
  await set('step',25);await set('rotationStep',45);
  assert.equal(await c.evaluate('document.querySelector("#step").max'),'', 'no joint increment cap');
  assert.equal(await c.evaluate('document.querySelector("#rotationStep").max'),'', 'no angular increment cap');
  await set('motionMode','joint_axes');await c.click('#viewHome');await pause(300);
  const camera=await c.evaluate('OMNI_VIEW.viewer.camera.position.toArray()');
  await press('joint',0);const p0=await c.evaluate('window.__dragQA.start');await release(p0);
  assert.equal(await c.evaluate('OMNI_VIEW.preview[0]'),0,'click without drag has no increment');
  await press('joint',0);let p;
  for(const angle of [2.3,7.7,16.4]){p=await move(angle);await pause(70);assert.ok(Math.abs(await c.evaluate('OMNI_VIEW.preview[0]')-angle)<.15,'continuous unsnapped rotation');}
  assert.ok((await c.evaluate('OMNI_VIEW.viewer.camera.position.toArray()')).every((v,i)=>Math.abs(v-camera[i])<1e-8),'drag does not orbit camera');
  assert.equal((await robot()).joints[0],0,'drag does not command hardware');await release(p);
  await c.wait('document.querySelector("#planStatus").textContent.includes("적용 대기")','joint preview validated');
  await set('step',.0005);await press('joint',0);p=await move(-4.7);await release(p);
  assert.ok(Math.abs(await c.evaluate('OMNI_VIEW.preview[0]')-11.7)<.2,'drag independent of numeric increment');
  await c.screenshot('free-joint-drag');
  // A large drag clamps at the controller joint limit, without sending motion.
  await press('joint',0);for(const angle of [60,120,180,240])p=await move(angle);await release(p);
  const limit=(await robot()).limits.joints[0][1];assert.ok(Math.abs(await c.evaluate('OMNI_VIEW.preview[0]')-limit)<1e-6);
  await c.wait('document.querySelector("#planStatus").textContent.includes("적용 대기")','clamped limit remains a valid target');
  // Explicit numeric jog accepts more than 2 degrees, and fractional increments below .01.
  await set('motionMode','joint');await set('step',15);await c.click('#areaClear');await c.click('#enable');
  await c.wait('!document.querySelector(".jog.plus").disabled','jog armed');await c.click('.jog.plus');
  await c.wait('document.querySelector("#j0").textContent==="15.00"&&!document.querySelector(".jog.plus").disabled','15 degree numeric jog');
  await set('step',.0005);await c.click('.jog.plus');await c.wait('!document.querySelector(".jog.plus").disabled','tiny jog done');
  await c.wait('fetch("/api/status").then(r=>r.json()).then(s=>Math.abs(s.robot.joints[0]-15.0005)<1e-6)','tiny jog measured');
  assert.ok(Math.abs((await robot()).joints[0]-15.0005)<1e-6);
  await set('motionMode','tool');await c.click('#viewHome');await pause(300);
  const actual=(await robot()).joints;
  await press('tool',1);
  for(const distance of [3.456,7.891,12.345]){p=await move(distance);await pause(180);assert.ok(Math.abs(await c.evaluate('Number(document.querySelector("#delta1").value)')-distance)<.001,'continuous local-axis translation');}
  await release(p);await c.wait('document.querySelector("#planStatus").textContent.includes("적용 대기")','tool translation IK');
  assert.deepEqual((await robot()).joints,actual,'translation is preview only');
  await press('tool',3);
  for(const angle of [1.1,3.4,6.7]){p=await move(angle);await pause(180);assert.ok(Math.abs(await c.evaluate('Number(document.querySelector("#delta3").value)')-angle)<.15,'continuous TCP roll');}
  await release(p);await c.wait('document.querySelector("#planStatus").textContent.includes("적용 대기")','tool rotation IK');
  await c.screenshot('free-tool-drag');
  // Cancel restores the starting ghost/fields and camera, with no robot command.
  const baseline=await c.evaluate('Number(document.querySelector("#delta1").value)');
  await press('tool',1);p=await move(8);
  await c.evaluate('(()=>{const v=OMNI_VIEW.viewer;v.renderer.domElement.dispatchEvent(new PointerEvent("pointercancel",{pointerId:v.gizmos.drag.pointerId}));})()');
  assert.equal(await c.evaluate('OMNI_VIEW.viewer.controls.enabled'),true);
  assert.equal(await c.evaluate('Number(document.querySelector("#delta1").value)'),baseline);await release(p);
  assert.deepEqual((await robot()).joints,actual);
  await c.click('#copyCurrent');await set('delta5',45);
  await c.wait('document.querySelector("#planStatus").textContent.includes("적용 대기")','45 degree typed tool target accepted');
  assert.equal(await c.evaluate('Number(document.querySelector("#delta5").value)'),45);
  const cameraBefore=await c.evaluate('OMNI_VIEW.viewer.camera.position.toArray()');
  const rect=await c.evaluate('(()=>{const r=document.querySelector("#robotViewer").getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()');
  await c.send('Input.dispatchMouseEvent',{type:'mousePressed',...rect,button:'right',buttons:2,clickCount:1});
  await c.send('Input.dispatchMouseEvent',{type:'mouseMoved',x:rect.x+65,y:rect.y-20,button:'right',buttons:2});
  await c.send('Input.dispatchMouseEvent',{type:'mouseReleased',x:rect.x+65,y:rect.y-20,button:'right',buttons:0,clickCount:1});
  await pause(300);
  assert.ok((await c.evaluate('OMNI_VIEW.viewer.camera.position.toArray()')).some((v,i)=>Math.abs(v-cameraBefore[i])>.01),'right orbit works after cancelled drag');
  if(!(await robot()).armed){await c.click('#areaClear');await c.click('#enable');}
  await c.wait('!document.querySelector("#applyTarget").disabled','apply after drag enabled');
  const goal=await c.evaluate('OMNI_VIEW.preview.slice()');await c.click('#applyTarget');
  await c.wait(`fetch('/api/status').then(r=>r.json()).then(s=>!s.robot.busy&&s.robot.joints.every((v,i)=>Math.abs(v-${JSON.stringify(goal)}[i])<1e-6))`,'explicit apply after free drag and numeric edit');
  await c.click('#lock');await c.viewport(390);assert.equal(await c.evaluate('document.documentElement.scrollWidth<=390'),true);
  await c.screenshot('free-drag-mobile');assert.deepEqual(c.errors,[]);
  process.stdout.write('PASS free drag: unsnapped joint rotation, local XYZ/TCP rotation, step independence, click no-op, soft limits, capture/release/cancel, no motion until apply, 15°/0.0005° input, 45° tool target, explicit apply, right orbit, mobile\n');
}finally{c.close();}
