// Local QA against two --demo servers on 8098/8099 and dedicated Chrome on 9224.
// No packages needed: Node 22+ provides fetch and WebSocket.
import {writeFile, mkdir} from 'node:fs/promises';
import assert from 'node:assert/strict';

await mkdir(new URL('./screenshots/', import.meta.url), {recursive: true});
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function page(port) {
  const target = await (await fetch(`http://127.0.0.1:9224/json/new?${encodeURIComponent('http://127.0.0.1:' + port)}`, {method: 'PUT'})).json();
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

const controller = await page(Number(process.env.QA_CONTROLLER_PORT || 8098));
try {
  assert.equal(await controller.evaluate('document.querySelector(".jog").disabled'), true);
  await controller.click('#connect');
  await controller.wait('document.querySelector("#robotBadge").textContent === "연결됨" && document.querySelector("#gripBadge").textContent === "연결됨"', 'demo robot + direct RS485 connection');
  await controller.wait('document.querySelector("#robotViewer").dataset.loaded === "true" && window.OMNI_VIEW.viewer.renderer.info.render.triangles > 100000', 'manufacturer STL models rendered');
  await controller.click('#areaClear'); await controller.click('#enable');
  await controller.wait('!document.querySelector(".jog").disabled', 'armed');
  await controller.click('.jog.plus'); await controller.click('#sendGrip');
  await controller.wait('document.querySelector("#j0").textContent === "0.50" && document.querySelector("#gripLast").textContent.includes("속도 40")', 'parallel robot and gripper commands');
  await controller.evaluate('document.querySelector("#position").value=140;document.querySelector("#gripSpeed").value=20;document.querySelector("#gripTorque").value=30;document.querySelector("#gripAcceleration").value=50;document.querySelector("#gripDeceleration").value=60');
  await controller.click('#together'); await controller.click('.jog.plus');
  await controller.wait('document.querySelector("#j0").textContent === "1.00" && document.querySelector("#gripLast").textContent.includes("토크 30")', 'coordinated SDK command');
  await controller.click('#together');
  assert.deepEqual(await controller.evaluate('fetch("/api/status").then(r=>r.json()).then(s=>[s.gripper.acceleration,s.gripper.deceleration])'),[50,60]);
  await controller.evaluate('document.querySelector("#sdkFeatures").open=true');
  await controller.wait('document.querySelectorAll(".sdk-feature").length >= 690','full SDK API inventory');
  await controller.evaluate('document.querySelector("#sdkSearch").value="StartMotorCalibration";document.querySelector("#sdkSearch").dispatchEvent(new Event("input"))');
  assert.equal(await controller.evaluate('document.querySelectorAll(".sdk-feature").length'),1);
  assert.ok(await controller.evaluate('document.querySelector(".sdk-feature .unavailable").textContent.includes("usb")'));
  await controller.evaluate('document.querySelector("#sdkSearch").value="MoveL";document.querySelector("#sdkSearch").dispatchEvent(new Event("input"))');
  assert.ok(await controller.evaluate('Array.from(document.querySelectorAll(".sdk-feature code")).some(n=>n.textContent.startsWith("MoveL("))'));
  await controller.evaluate('document.querySelector("#target1").value=-105;document.querySelector("#target1").dispatchEvent(new Event("input"))');
  assert.equal(await controller.evaluate('window.OMNI_VIEW.preview[1]'), -105, 'editable target preview');
  assert.equal(await controller.evaluate('fetch("/api/status").then(r=>r.json()).then(s=>s.robot.joints[1])'), -65, 'preview never commands motion');
  const before = await controller.evaluate('window.OMNI_VIEW.viewer.camera.position.toArray()');
  const rect = await controller.evaluate('(()=>{const r=document.querySelector("#robotViewer").getBoundingClientRect();return {x:r.x+r.width*.5,y:r.y+r.height*.5}})()');
  await controller.send('Input.dispatchMouseEvent', {type:'mousePressed', x:rect.x, y:rect.y, button:'right', buttons:2, clickCount:1});
  await controller.send('Input.dispatchMouseEvent', {type:'mouseMoved', x:rect.x+100, y:rect.y-35, button:'right', buttons:2});
  await controller.send('Input.dispatchMouseEvent', {type:'mouseReleased', x:rect.x+100, y:rect.y-35, button:'right', buttons:0, clickCount:1});
  await pause(500);
  const after = await controller.evaluate('window.OMNI_VIEW.viewer.camera.position.toArray()');
  assert.ok(before.some((value,index)=>Math.abs(value-after[index])>.03), 'right mouse rotates camera');
  await controller.click('#viewHome');
  await controller.screenshot('controller-desktop');
  await controller.click('#lock');
  await controller.wait('document.querySelector(".jog").disabled && document.querySelector("#sendGrip").disabled', 'lock disables both channels');
  await controller.viewport(390);
  assert.equal(await controller.evaluate('document.documentElement.scrollWidth <= 390'), true, 'controller mobile overflow');
  await controller.screenshot('controller-mobile');
  const response = await controller.evaluate('fetch("/api/joint", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({axis:0,delta:1})}).then(r=>r.status)');
  assert.equal(response, 403, 'control token required');
  assert.deepEqual(controller.errors, [], 'controller JavaScript errors');
  process.stdout.write('PASS controller: manufacturer STLs, live joints, ghost preview, right-drag orbit, parallel commands, lock, auth, desktop/mobile\n');
} finally { controller.close(); }

const setting = await page(Number(process.env.QA_SETTING_PORT || 8099));
try {
  await setting.click('#scan');
  await setting.wait('document.querySelector("#usbViewer").dataset.loaded === "true"', 'USB 3D model');
  await setting.wait('document.querySelector("#device").value.includes("bus")', 'USB scan');
  await setting.click('#connect');
  await setting.wait('!document.querySelector("#nodeId").disabled', 'USB connection');
  await setting.evaluate('document.querySelector("#nodeId").value=7; document.querySelector("#baudrate").value="230400"');
  await setting.click('#applySettings'); await setting.click('#dialogConfirm');
  await setting.wait('!document.querySelector("#applySettings").disabled && document.querySelector("#saveBadge").textContent.includes("미저장")', 'RAM apply');
  await setting.click('#saveFlash'); await setting.click('#dialogConfirm');
  await setting.wait('!document.querySelector("#connect").disabled && document.querySelector("#nodeId").disabled', 'flash save disconnect');
  await setting.click('#connect');
  await setting.wait('!document.querySelector("#nodeId").disabled', 'USB reconnect');
  assert.equal(await setting.evaluate('document.querySelector("#nodeId").value'), '7', 'saved node id readback');
  assert.equal(await setting.evaluate('document.querySelector("#baudrate").value'), '230400', 'saved baudrate readback');
  await setting.click('#fr3Preset'); await setting.click('#applySettings'); await setting.click('#dialogConfirm');
  await setting.wait('!document.querySelector("#enable").disabled', 'preset apply');
  await setting.click('#areaClear'); await setting.click('#enable');
  await setting.wait('!document.querySelector("#sendMove").disabled', 'USB test armed');
  await setting.evaluate('document.querySelector("#position").value=60;document.querySelector("#position").dispatchEvent(new Event("input"))');
  await setting.click('#sendMove');
  await setting.wait('document.querySelector("#lastTarget").textContent.includes("60%")', 'USB MIT command');
  await setting.screenshot('setting-desktop');
  await setting.viewport(390);
  assert.equal(await setting.evaluate('document.documentElement.scrollWidth <= 390'), true, 'setting mobile overflow');
  await setting.screenshot('setting-mobile');
  assert.deepEqual(setting.errors, [], 'setting JavaScript errors');
  process.stdout.write('PASS setting: discover, apply, save/reconnect/readback, MIT command, desktop/mobile screenshots\n');
} finally { setting.close(); }
