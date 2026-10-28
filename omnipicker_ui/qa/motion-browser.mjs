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

const controller=await page(8098);
const value=(client,id,v)=>client.evaluate(`(()=>{const e=document.getElementById(${JSON.stringify(id)});e.value=${JSON.stringify(v)};e.dispatchEvent(new Event(e.tagName==='SELECT'?'change':'input'));})()`);
const status=()=>controller.evaluate('fetch("/api/status").then(r=>r.json())');
async function arrowDrag(group,kind,axis,sign){
  const point=await controller.evaluate(`(()=>{
    const v=window.OMNI_VIEW.viewer;v.scene.updateMatrixWorld(true);let target;
    ${group}.traverse(n=>{const a=n.userData.action;if(a?.kind===${JSON.stringify(kind)}&&a.sign===${sign}&&(a.axis===${axis}||a.joint===${axis}))target=n;});
    if(!target)throw Error('Missing gizmo');
    const p=target.getWorldPosition(v.camera.position.clone()).project(v.camera),r=v.renderer.domElement.getBoundingClientRect();
    return {x:r.left+(p.x+1)*r.width/2,y:r.top+(1-p.y)*r.height/2};
  })()`);
  await controller.send('Input.dispatchMouseEvent',{type:'mousePressed',...point,button:'left',buttons:1,clickCount:1});
  const end=await controller.evaluate(`(()=>{
    const v=OMNI_VIEW.viewer,d=v.gizmos.drag,amount=${kind==='joint'?.37:1};if(!d)throw Error('handle not captured');
    let world;
    if(d.planar&&d.rotation)world=d.last.clone().applyAxisAngle(d.axis,amount*Math.PI/180).multiplyScalar(.07).add(d.centre);
    else if(d.planar)world=d.origin.clone().addScaledVector(d.axis,amount/1000);
    if(world){const p=world.project(v.camera),r=v.renderer.domElement.getBoundingClientRect();return {x:r.left+(p.x+1)*r.width/2,y:r.top+(1-p.y)*r.height/2};}
    const pixels=amount/(d.rotation?180/Math.PI:1000)*d.scale;return {x:${point.x}+pixels*d.screenAxis.x,y:${point.y}+pixels*d.screenAxis.y};
  })()`);
  await controller.send('Input.dispatchMouseEvent',{type:'mouseMoved',...end,button:'left',buttons:1});
  await controller.send('Input.dispatchMouseEvent',{type:'mouseReleased',...end,button:'left',buttons:0,clickCount:1});
}
try{
  await controller.click('#connect');
  await controller.wait('document.querySelector("#robotBadge").textContent==="연결됨"&&!!window.OMNI_VIEW.viewer?.gizmos','connect and 3D');
  await controller.click('#areaClear');await controller.click('#enable');
  await controller.wait('fetch("/api/status").then(r=>r.json()).then(s=>s.robot.armed)','armed');
  await value(controller,'step',.37);await value(controller,'velocity',17);await value(controller,'target0',.83);
  await controller.wait('document.querySelector("#planStatus").textContent.includes("적용 대기")','joint plan');
  assert.equal((await status()).robot.joints[0],0,'numeric preview does not move');
  await controller.click('#applyTarget');await controller.wait('document.querySelector("#j0").textContent==="0.83"&&!document.querySelector("#applyTarget").disabled','apply numeric target');
  await value(controller,'motionMode','joint_axes');await controller.click('#viewHome');await pause(500);
  assert.equal(await controller.evaluate('window.OMNI_VIEW.viewer.gizmos.joints.filter(g=>g.visible).length'),6);
  await arrowDrag('v.gizmos.joints[0]','joint',0,1);
  await controller.wait('Math.abs(window.OMNI_VIEW.preview[0]-1.2)<.02&&document.querySelector("#planStatus").textContent.includes("적용 대기")','3D joint drag');
  assert.ok(Math.abs((await status()).robot.joints[0]-.83)<1e-8,'3D arrow is preview only');
  await controller.screenshot('joint-axes-desktop');await controller.click('#applyTarget');
  await controller.wait('document.querySelector("#j0").textContent==="1.20"&&!document.querySelector("#applyTarget").disabled','apply 3D joint preview');
  await value(controller,'motionMode','tool');await controller.click('#viewHome');await pause(400);
  const start=(await status()).robot.pose;
  await value(controller,'delta0',2);
  await controller.wait('document.querySelector("#planStatus").textContent.includes("적용 대기")','tool target IK');
  assert.deepEqual((await status()).robot.pose,start,'tool preview does not move');
  await arrowDrag('v.gizmos.tool','tool',1,1);
  await controller.wait('Math.abs(Number(document.querySelector("#delta1").value)-1)<.001&&document.querySelector("#planStatus").textContent.includes("적용 대기")','3D local Y drag');
  await controller.screenshot('tool-axes-desktop');
  if(!(await status()).robot.armed){await controller.click('#areaClear');await controller.click('#enable');await controller.wait('!document.querySelector("#applyTarget").disabled','renewed arm gate');}
  await controller.click('#applyTarget');
  await controller.wait('!document.querySelector("#applyTarget").disabled&&document.querySelector("#j0").textContent!=="1.20"','apply tool linear motion');
  const end=(await status()).robot.pose;assert.ok(Math.hypot(...start.slice(0,3).map((v,i)=>v-end[i]))>2);
  await controller.viewport(390);assert.equal(await controller.evaluate('document.documentElement.scrollWidth<=390'),true);await controller.screenshot('tool-axes-mobile');
  assert.deepEqual(controller.errors,[]);
  process.stdout.write('PASS motion: custom increment/speed, numeric preview/apply, 6 selectable joint rotation handles, continuous joint/tool arrow drags, local TCP IK/MoveL, mobile\n');
}finally{controller.close();}
const settings=await page(8098,'/gripper/settings');
try{
  await settings.wait('document.querySelectorAll("#otherFeatures code").length>70','USB/CAN features inventory');
  if(!await settings.evaluate('fetch("/api/status").then(r=>r.json()).then(s=>s.robot.armed)')){await settings.click('#areaClear');await settings.click('#enable');await settings.wait('!document.querySelector("#stage").disabled','gripper arm');}
  await value(settings,'position',140);await value(settings,'speed',20);await value(settings,'torque',30);await settings.click('#stage');
  await settings.wait('document.querySelector("#result").textContent.includes("기록 완료")','stage params');
  assert.equal(await settings.evaluate('fetch("/api/status").then(r=>r.json()).then(s=>s.gripper.actual_position)'),127);
  await settings.click('#trigger');await settings.wait('document.querySelector("#result").textContent.includes("도달 완료")','trigger staged command');
  assert.equal(await settings.evaluate('fetch("/api/status").then(r=>r.json()).then(s=>s.gripper.actual_position)'),140);
  await settings.click('#readRegisters');await settings.wait('document.querySelectorAll("#registers tr").length===12','12 modbus registers');
  await settings.click('#fullRange');await settings.click('#setLimits');await settings.wait('document.querySelector("#speed").max==="255"','custom input limits');
  assert.equal(await settings.evaluate('fetch("/api/status").then(r=>r.json()).then(s=>s.robot.armed)'),false,'changing ranges locks motion');
  await settings.screenshot('integrated-gripper-settings');
  await settings.click('#usbTab');await settings.wait('!!document.querySelector("#usbFrame").contentDocument?.querySelector("#scan")','embedded USB UI');
  const prefix=await settings.evaluate('document.querySelector("#usbFrame").contentWindow.API_BASE');assert.equal(prefix,'/gripper/usb/api/');
  assert.equal(await settings.evaluate('document.querySelector("#usbFrame").contentWindow.UI.api("status").then(s=>s.connected)'),false,'open settings never claims USB');
  await settings.evaluate('document.querySelector("#usbFrame").contentDocument.querySelector("#scan").click()');
  await settings.wait('document.querySelector("#usbFrame").contentDocument.querySelector("#device").value!==""','embedded demo USB discovery');
  await settings.evaluate('document.querySelector("#usbFrame").contentDocument.querySelector("#connect").click()');
  await settings.wait('document.querySelector("#usbFrame").contentDocument.querySelector("#connectionLabel").textContent==="USB 연결됨"','embedded USB connection');
  await settings.evaluate('document.querySelector("#usbFrame").contentDocument.querySelector("#loadEndpoints").click()');
  await settings.wait('document.querySelector("#usbFrame").contentDocument.querySelectorAll("#usbEndpoint option").length===75','all USB feature controls');
  assert.deepEqual(settings.errors,[]);
  process.stdout.write('PASS settings: stage/trigger, 12 registers, custom 0–255 limits + lock, separate settings window, embedded USB prefix, 75 USB endpoints\n');
}finally{settings.close();}
