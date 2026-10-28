import * as THREE from 'three';
import {RobotScene} from './robot-scene.js';

const {$, api, toast, badge, events, errorBanner, download} = UI;
let state = null, polling = false, pendingRobot = false, pendingGrip = false, pendingControl = false;
let preview = null, logs = [], viewer = null;
let reference=null,plan=null,planVersion=0,previewTimer=null,previewPending=false;
let activeDrag=null,editSession=0,previewAgain=false,lastPreviewAt=0;
let referencePose=null,dirty=false,previewError='',goalPose=null;
const initial = [0, -90, 90, -90, -90, 0];
const savedModel = localStorage.getItem('omni.robotModel');
if (['FR3WMS', 'FR3WML'].includes(savedModel)) $('robotModel').value = savedModel;

for (let axis = 0; axis < 6; axis++) {
  const row = document.createElement('div'); row.className = 'joint-row';
  row.innerHTML = `<span class="joint-id">J${axis+1}<small class="joint-range" id="limit${axis}">—</small></span><span class="joint-value" id="j${axis}">—</span><input class="target-angle" id="target${axis}" required type="number" step="any" value="${initial[axis]}" aria-label="J${axis+1} 목표 미리보기"><button class="jog" aria-label="J${axis+1} 음의 방향" disabled>−</button><button class="jog plus" aria-label="J${axis+1} 양의 방향" disabled>+</button>`;
  row.querySelectorAll('button').forEach((button, direction) => button.onclick = () => jog(axis, direction ? 1 : -1));
  row.querySelector('input').addEventListener('input', () => {
    const value = Number($('target'+axis).value);
    if ($('target'+axis).value===''||!Number.isFinite(value)){schedulePreview();return;}
    preview ||= state?.robot.joints?.slice() || initial.slice(); preview[axis] = value; updateView(); schedulePreview();
  });
  $('joints').append(row);
}
$('tcp').innerHTML='<div class="tcp-table-head"><span>기준 좌표</span><span>현재</span><span>목표</span></div>';
['X mm','Y mm','Z mm','Roll °','Pitch °','Yaw °'].forEach((label,index)=>{
  const row=document.createElement('div');row.className='tcp-pose-row';
  row.innerHTML=`<small>${label}</small><b id="tcp${index}">—</b><b id="goalTcp${index}">—</b>`;$('tcp').append(row);
});
function mount() {
  const xyz = ['X','Y','Z'].map(axis => Number($('mount'+axis).value)/1000);
  const rpy = ['Rx','Ry','Rz'].map(axis => Number($('mount'+axis).value)*Math.PI/180);
  if ([...xyz,...rpy].every(Number.isFinite)) {
    viewer?.mount(xyz,rpy); localStorage.setItem('omni.mount', JSON.stringify([...xyz,...rpy]));
  }
}
try {
  const values = JSON.parse(localStorage.getItem('omni.mount'));
  if (Array.isArray(values) && values.length === 6) ['X','Y','Z','Rx','Ry','Rz'].forEach((axis,index) => {
    if (Number.isFinite(values[index])) $('mount'+axis).value = index < 3 ? values[index]*1000 : values[index]*180/Math.PI;
  });
} catch {}
function createViewer() {
  viewer?.dispose(); $('robotViewer').replaceChildren(); delete $('robotViewer').dataset.loaded;
  $('modelStatus').textContent = 'STL 로딩';
  try { viewer = new RobotScene($('robotViewer'), {model: $('robotModel').value}); viewer.setMode($('motionMode').value);updateView(); }
  catch (error) { $('modelStatus').textContent = 'WebGL 오류'; toast(error.message, true); }
}
$('robotViewer').addEventListener('modelloaded', event => { $('modelStatus').textContent = event.detail.model+' · STL'; mount();viewer?.tcp(state?.robot.tcp_offset||[0,0,0,0,0,0]); updateView(); });
$('robotViewer').addEventListener('modelerror', event => { $('modelStatus').textContent = '모델 오류'; toast(event.detail, true); });
$('robotModel').onchange = () => { localStorage.setItem('omni.robotModel', $('robotModel').value); createViewer(); };
['X','Y','Z','Rx','Ry','Rz'].forEach(axis => $('mount'+axis).oninput = mount);
$('viewHome').onclick = () => viewer?.home();
$('focusGripper').onclick = () => viewer?.focusGripper();
$('viewFullscreen').onclick = () => {
  if (document.fullscreenElement) document.exitFullscreen(); else $('robotViewer').parentElement.requestFullscreen();
};
function updateView() {
  const target = preview || state?.robot.target_joints || state?.robot.joints || initial;
  viewer?.update(state, target, Number($('position').value)/255);
  if($('motionMode').value==='tool'){
    const delta=Array.from({length:6},(_,i)=>Number($('delta'+i).value));
    if(delta.every(Number.isFinite)){
      viewer?.draft(reference,delta);
      if(referencePose)goalPose=poseOf(matrixForDelta(referencePose).multiply(matrixForDelta(delta)));
    }
  }
  if(goalPose)goalPose.forEach((value,i)=>$('goalTcp'+i).textContent=value.toFixed(i<3?2:3));
  $('goalSummary').textContent=goalPose?`목표 ${goalPose.slice(0,3).map(v=>v.toFixed(1)).join(' / ')} mm`:'목표 —';
  target.forEach((value,index)=>{
    if(document.activeElement===$('target'+index))return;
    const rounded=Number(Number(value).toFixed(4)),range=state?.robot.limits?.joints[index];
    // Display rounding must not make a valid limit pose fail HTML range validation.
    $('target'+index).value=range&&(rounded<range[0]||rounded>range[1])?String(value):String(rounded);
  });
}
function copyCurrent(){
  viewer?.gizmos?.finish('cancel');editSession++;
  reference=state?.robot.joints?.slice()||null;referencePose=state?.robot.pose?.slice()||null;preview=reference?.slice()||null;goalPose=referencePose?.slice()||null;
  document.querySelectorAll('.tool-delta').forEach(input=>input.value=0);updateView();schedulePreview(false);
}
$('copyCurrent').onclick=copyCurrent;
function render() {
  const robot = state?.robot, grip = state?.gripper;
  const connected = !!robot?.connected, armed = !!robot?.armed;
  if(robot?.joints&&(!reference||(!dirty&&!activeDrag&&!pendingRobot))){
    reference=robot.joints.slice();referencePose=robot.pose.slice();preview=reference.slice();goalPose=referencePose.slice();
    if(!robot.connecting&&!dirty){$('tool').value=robot.active_tool;$('user').value=robot.active_user;}
  }
  if(robot?.limits){
    robot.limits.joints.forEach(([lo,hi],i)=>{
      $('target'+i).min=lo;$('target'+i).max=hi;$('limit'+i).textContent=`${lo.toFixed(1)} ~ ${hi.toFixed(1)}°`;
    });
    $('jointLimitSource').textContent='관절 한계: '+robot.limits.joint_source;
    for(const [id,key,label] of [['gripSpeed','gripper_speed','speedBounds'],['gripTorque','gripper_torque','torqueBounds']]){
      const [lo,hi]=robot.limits[key];$(id).min=lo;$(id).max=hi;$(label).textContent=`최소 ${lo} · 최대 ${hi}`;
    }
    viewer?.tcp(robot.tcp_offset);
  }
  $('applyTarget').disabled=!connected||!armed||!!robot?.busy||pendingRobot||pendingControl||previewPending||!!activeDrag||!plan||!dirty;
  const toolMode=$('motionMode').value==='tool';
  for(let i=0;i<6;i++){$('target'+i).disabled=toolMode||!!activeDrag;$('delta'+i).disabled=!!activeDrag;}
  $('connectionDot').className = 'dot'+(connected ? ' online' : '');
  $('connectionLabel').textContent = robot?.connecting ? '연결 중' : connected ? 'FR3 연결됨' : '미연결';
  $('connect').disabled = pendingControl || connected || !!robot?.connecting;
  $('ip').disabled = connected || !!robot?.connecting || pendingControl;
  badge('robotBadge', robot?.fault ? '응답 불확실' : connected ? (robot.busy || pendingRobot ? '이동 중' : '연결됨') : '미연결', connected && !robot?.fault);
  const valid = !!grip?.feedback_valid;
  const blocked = valid && [2,3].includes(grip.motion_state) && !grip.contact_completed;
  badge('gripBadge', grip?.busy || pendingGrip ? '전송 중' : valid && grip.error_code ? `오류 ${grip.error_code}` : grip?.contact_completed ? '접촉 완료' : blocked ? (grip.motion_state === 2 ? '접촉/정체' : '낙하') : valid ? '연결됨' : 'RS485 응답 없음', valid && !grip.error_code && !blocked);
  $('gripperStatus').textContent=grip?.feedback_valid&&grip.motion_state===2?'접촉/정체':grip?.reason||'RS485 미확인';
  $('probeGripper').disabled = !connected || pendingControl || !!grip?.busy;
  $('recoverGripper').disabled=!connected||pendingControl||!!robot?.busy||!!grip?.busy||!!robot?.usb_settings_active;
  $('recoverGripper').textContent=grip?.recovering?'연결 복구 중':'연결 복구';
  for (let axis = 0; axis < 6; axis++) {
    $('j'+axis).textContent = robot?.joints ? Number(robot.joints[axis]).toFixed(2) : '—';
    $('tcp'+axis).textContent = robot?.pose ? Number(robot.pose[axis]).toFixed(1) : '—';
  }
  const canGrip = connected && armed && valid && grip?.writable && grip.error_code === 0 && !grip?.fault && !grip?.busy && !pendingGrip && !pendingControl;
  document.querySelectorAll('.jog').forEach(button => button.disabled = !connected || ($('motionMode').value==='joint' && !armed) || $('motionMode').value==='tool' || !!robot?.busy || pendingRobot || pendingControl || ($('together').checked && !canGrip));
  $('sendGrip').disabled = !canGrip;
  $('together').disabled = pendingRobot || pendingGrip || !!robot?.busy || !!grip?.busy;
  ['position','gripSpeed','gripTorque','gripAcceleration','gripDeceleration','acceptContact'].forEach(id => $(id).disabled = !!grip?.busy || pendingGrip);
  $('enable').disabled = !connected || pendingControl || !!robot?.busy || !!robot?.fault || !!grip?.busy || !!grip?.fault || !!robot?.usb_settings_active || armed;
  $('gripResult').textContent = grip?.contact_completed ? '접촉 완료' : grip?.reached ? '완료' : '—';
  $('actualGrip').textContent = Number.isFinite(grip?.actual_position) ? (grip.actual_position/255*100).toFixed(1)+'%' : '—';
  $('feedbackNote').textContent = valid ? `그리퍼 ${(grip.actual_position/255*100).toFixed(1)}%` : '그리퍼 피드백 없음';
  $('gripReason').textContent = grip?.reason || 'RS485 미확인';
  $('loadedProgram').textContent = grip?.program ? `${grip.program} · ${grip.program_state === 1 ? '정지' : grip.program_state === 2 ? '실행' : grip.program_state ?? '—'}` : '—';
  $('serialProtocol').textContent = grip ? `${grip.slave_id} / ${grip.protocol === 4098 ? 'Modbus Master' : grip.protocol ?? '—'}` : '—';
  $('gripMotion').textContent = valid ? `${grip.error_code} / ${['도달','이동','접촉/정체','낙하'][grip.motion_state] || grip.motion_state}` : '—';
  $('gripLast').textContent = grip?.last || (connected && !grip?.writable ? grip?.transport_error || 'TCP 쓰기 연결 확인 필요' : '');
  $('armLamp').className='dot'+(armed?' online':'');
  $('gripTabDot').className=valid&&!grip?.error_code?'online':'';
  $('poseStatus').className='pose-status '+(previewError?'invalid':previewPending||activeDrag?'pending':plan?'valid':'');
  $('poseStatus').textContent=previewError?'목표 불가':previewPending||activeDrag?'계산 중':plan?'목표 확인':'대기';
  $('armState').textContent = armed ? `제어 허용 · ${robot.seconds}초` : '잠금';
  errorBanner(robot?.error || (robot?.fault ? '이동 응답 불확실 · 실제 정지 확인 후 서버 재시작' : ''));
  if (state?.events) { logs = state.events; events(logs); }
  updateView();
  window.dispatchEvent(new CustomEvent('controllerstate',{detail:state}));
}
async function refresh() {
  if (polling) return;
  polling = true;
  try { state = await api('status'); render(); }
  catch { state = null; render(); errorBanner('서버 응답 없음'); }
  finally { polling = false; }
}
async function command(path, data = {}, channel = 'control') {
  if (channel === 'both' && (pendingRobot || pendingGrip)) return;
  if ((channel === 'robot' && pendingRobot) || (channel === 'grip' && pendingGrip) || (channel === 'control' && pendingControl)) return;
  if (channel === 'robot' || channel === 'both') pendingRobot = true;
  if (channel === 'grip' || channel === 'both') pendingGrip = true;
  if (channel === 'control') pendingControl = true;
  render();
  try {
    const result = await api(path,data);
    if (channel === 'robot' && result.result?.target) {preview=result.result.target.slice();if(path!=='robot/apply')reference=preview.slice();}
    if (channel === 'both' && result.result?.robot?.target) {preview = result.result.robot.target.slice();reference=preview.slice();}
    return result;
  } catch(error) { toast(error.message,true); }
  finally {
    if (channel === 'robot' || channel === 'both') pendingRobot = false;
    if (channel === 'grip' || channel === 'both') pendingGrip = false;
    if (channel === 'control') pendingControl = false;
    await refresh(); render();
  }
}
function jog(axis,direction){
  if($('motionMode').value!=='joint'){
    if($('motionMode').value==='joint_axes')jointPreview(axis,direction);
    return;
  }
  if (!state?.robot.armed || pendingRobot || activeDrag) return;
  const step=positive('step');if(step===null)return;
  const delta = step*direction;
  dirty=false;editSession++;planVersion++;clearTimeout(previewTimer);
  plan=null;reference=state.robot.joints.slice();preview = reference.slice(); preview[axis] += delta;
  const data = {axis,delta,vel:Number($('velocity').value),tool:Number($('tool').value),user:Number($('user').value)};
  if ($('together').checked) {
    Object.assign(data,gripperOptions());
    command('together',data,'both');
  } else command('joint',data,'robot');
}
function gripperOptions() {
  return {position:Number($('position').value),speed:Number($('gripSpeed').value),torque:Number($('gripTorque').value),
    acceleration:Number($('gripAcceleration').value),deceleration:Number($('gripDeceleration').value),
    accept_contact:$('acceptContact').checked};
}
$('together').onchange = render;
function position() {
  const value = Number($('position').value);
  $('positionOut').textContent = Math.round(value/255*100); $('rawPosition').textContent = `${value} / 255`;
  document.querySelectorAll('[data-position]').forEach(button => button.classList.toggle('selected',Number(button.dataset.position) === value));
  updateView();
}
$('position').oninput = position;
document.querySelectorAll('[data-position]').forEach(button => button.onclick = () => { $('position').value = button.dataset.position; position(); });
$('connect').onclick = () => {
  const ip = $('ip').value.trim();
  if (!/^(\d{1,3}\.){3}\d{1,3}$/.test(ip) || ip.split('.').some(value => Number(value)>255)) return toast('IP 주소 오류',true);
  command('connect',{ip});
};
$('probeGripper').onclick = () => command('gripper/probe');
$('recoverGripper').onclick=()=>command('gripper/recover',{confirm:'REOPEN_RS485'});
$('enable').onclick = () => {
  if (!$('areaClear').checked) return toast('작업영역 확인 필요',true);
  $('areaClear').checked = false; command('enable',{confirm:'PHYSICAL_AREA_CLEAR'});
};
$('lock').onclick = () => {viewer?.gizmos?.finish('cancel');command('lock',{},'lock');};
let stopping = false;
async function stop() {
  viewer?.gizmos?.finish('cancel');
  if (stopping) return; stopping = true; $('stop').disabled = true;
  if (state) state.robot.armed = false; render();
  try { await command('stop',{},'stop'); } finally { stopping = false; $('stop').disabled = false; }
}
$('stop').onclick = stop;
document.addEventListener('keydown',event => { if (event.key==='Escape' && !event.repeat && !document.fullscreenElement) {event.preventDefault();viewer?.gizmos?.finish('cancel');stop();} });
$('sendGrip').onclick = () => command('gripper',gripperOptions(),'grip');
$('exportLog').onclick = () => download('OmniPicker_FR3_log.json',logs);
function bounded(id,min,max){
  const input=$(id),value=Number(input.value);
  if(input.value===''||!Number.isFinite(value)||value<min||value>max){toast(`${min} ~ ${max} 범위 입력 필요`,true);input.focus();return null;}
  return value;
}
function positive(id){
  const input=$(id),value=Number(input.value);
  if(input.value===''||!Number.isFinite(value)||value<=0){toast('0보다 큰 유한한 증분 입력 필요',true);input.focus();return null;}
  return value;
}
function motionData(){
  const mode=$('motionMode').value;
  const inputs=[...Array.from({length:6},(_,i)=>$(mode==='tool'?'delta'+i:'target'+i)),$('tool'),$('user')];
  for(const input of inputs)if(input.value===''||!input.checkValidity())throw Error('목표 입력값·최소/최대 범위 확인 필요');
  return {kind:$('motionMode').value==='tool'?'tool':'joint',joints:preview||state?.robot.joints,
    delta:Array.from({length:6},(_,i)=>Number($('delta'+i).value)),reference,
    tool:Number($('tool').value),user:Number($('user').value)};
}
function schedulePreview(markDirty=true){
  dirty=markDirty!==false;previewError='';
  plan=null;planVersion++;clearTimeout(previewTimer);
  $('planStatus').textContent=activeDrag?'목표 계산 중':markDirty?'미적용 목표':'현재 자세';
  updateView();
  if(!state?.robot.connected)return;
  if(activeDrag){
    if(activeDrag.kind==='tool')previewTimer=setTimeout(()=>refreshPlan(),Math.max(0,150-(performance.now()-lastPreviewAt)));
  }else previewTimer=setTimeout(()=>refreshPlan(),350);
}
async function refreshPlan(){
  if(previewPending){previewAgain=true;return null;}
  const version=planVersion,session=editSession,drag=activeDrag;
  previewPending=true;lastPreviewAt=performance.now();render();
  try{
    const reply=await api('robot/preview',motionData());
    if(session!==editSession)return null;
    if(version===planVersion){
      previewError='';referencePose=reply.result.reference_pose?.slice()||referencePose;goalPose=reply.result.pose.slice();
      preview=reply.result.joints.slice();
      if(!activeDrag){plan=reply.result;$('planStatus').textContent='미리보기 확인 · 적용 대기';}
      updateView();return activeDrag?null:plan;
    }
    // Show intermediate IK solutions while newer pointer positions wait; never apply these plans.
    if(drag&&drag===activeDrag&&drag.kind==='tool'){preview=reply.result.joints.slice();updateView();}
    return null;
  }catch(e){if(version===planVersion&&session===editSession){plan=null;previewError=e.message;$('planStatus').textContent=e.message;}return null;}
  finally{
    previewPending=false;render();
    if(previewAgain||version!==planVersion){
      previewAgain=false;clearTimeout(previewTimer);
      if(state?.robot.connected&&(!activeDrag||activeDrag.kind==='tool'))previewTimer=setTimeout(()=>refreshPlan(),activeDrag?Math.max(0,150-(performance.now()-lastPreviewAt)):0);
    }
  }
}
function jointPreview(axis,sign){
  const step=positive('step');if(step===null)return;
  preview||=state?.robot.joints?.slice()||initial.slice();
  const value=preview[axis]+sign*step,range=state?.robot.limits?.joints[axis];
  if(range&&(value<range[0]||value>range[1]))return toast(`J${axis+1}: ${range.join(' ~ ')}°`,true);
  preview[axis]=value;updateView();schedulePreview();
}
function poseOf(matrix){
  const pos=new THREE.Vector3().setFromMatrixPosition(matrix),e=new THREE.Euler().setFromRotationMatrix(matrix,'ZYX');
  return [...pos.toArray(),e.x*180/Math.PI,e.y*180/Math.PI,e.z*180/Math.PI];
}
function matrixForDelta(delta){
  return new THREE.Matrix4().compose(new THREE.Vector3(...delta.slice(0,3)),new THREE.Quaternion().setFromEuler(new THREE.Euler(...delta.slice(3).map(x=>x*Math.PI/180),'ZYX')),new THREE.Vector3(1,1,1));
}
function composedDelta(base,axis,amount){
  const increment=[0,0,0,0,0,0];increment[axis]=amount;
  const matrix=matrixForDelta(base).multiply(matrixForDelta(increment));
  const pos=new THREE.Vector3().setFromMatrixPosition(matrix),rot=new THREE.Euler().setFromRotationMatrix(matrix,'ZYX');
  return [...pos.toArray(),rot.x*180/Math.PI,rot.y*180/Math.PI,rot.z*180/Math.PI];
}
function writeDelta(values){values.forEach((v,i)=>$('delta'+i).value=Number(v.toFixed(6)));}
function toolPreview(axis,sign){
  const step=axis<3?bounded('translationStep',.1,10):positive('rotationStep');if(step===null)return;
  const delta=Array.from({length:6},(_,i)=>Number($('delta'+i).value));
  writeDelta(composedDelta(delta,axis,sign*step));schedulePreview();
}
['X mm','Y mm','Z mm','Roll °','Pitch °','Yaw °'].forEach((label,i)=>{
  const row=document.createElement('div');row.className='tool-row';
  row.innerHTML=`<label>${label}<small>IK·관절 한계 적용</small></label><input id="delta${i}" class="tool-delta" required type="number" step="any" value="0"><button aria-label="${label} 감소">−</button><button aria-label="${label} 증가">+</button>`;
  row.querySelector('input').oninput=schedulePreview;row.querySelectorAll('button').forEach((b,j)=>b.onclick=()=>toolPreview(i,j?1:-1));$('toolDelta').append(row);
});
$('motionMode').onchange=()=>{
  const mode=$('motionMode').value;$('toolControls').hidden=mode!=='tool';$('axisNote').hidden=mode!=='joint_axes';
  $('together').checked=false;viewer?.setMode(mode);copyCurrent();render();
};
$('robotViewer').addEventListener('gizmochange',event=>{
  const action=event.detail;
  if(action.phase==='select')return;
  if(action.phase==='start'){
    if(!state?.robot.connected||state.robot.busy||pendingRobot||pendingControl||activeDrag){event.preventDefault();return;}
    editSession++;
    activeDrag={kind:action.kind,joints:(preview||state.robot.joints).slice(),delta:Array.from({length:6},(_,i)=>Number($('delta'+i).value))};
    schedulePreview();render();return;
  }
  if(!activeDrag)return;
  if(action.phase==='move'){
    if(state?.robot.busy||pendingRobot||!state?.robot.connected){viewer?.gizmos?.finish('cancel');return;}
    if(action.kind==='joint'){
      const range=state.robot.limits.joints[action.joint];
      preview=activeDrag.joints.slice();preview[action.joint]=THREE.MathUtils.clamp(preview[action.joint]+action.amount,range[0],range[1]);
      updateView();
    }else writeDelta(composedDelta(activeDrag.delta,action.axis,action.amount));
    schedulePreview();return;
  }
  if(action.phase==='cancel'){
    preview=activeDrag.joints.slice();writeDelta(activeDrag.delta);editSession++;
  }
  activeDrag=null;schedulePreview();render();
});
$('tool').oninput=schedulePreview;$('user').oninput=schedulePreview;
$('applyTarget').onclick=async()=>{
  if(!state?.robot.armed||pendingRobot||activeDrag)return;
  const velocity=bounded('velocity',1,100);if(velocity===null)return;
  clearTimeout(previewTimer);const ready=await refreshPlan();if(!ready)return;
  plan=null;const result=await command('robot/apply',{plan_id:ready.id,vel:velocity},'robot');
  if(result?.ok){
    reference=result.result.target.slice();referencePose=result.result.pose.slice();goalPose=referencePose.slice();preview=reference.slice();dirty=false;previewError='';plan=null;
    if(ready.kind==='tool')document.querySelectorAll('.tool-delta').forEach(input=>input.value=0);
    $('planStatus').textContent='목표 적용 완료';updateView();
  }else $('planStatus').textContent=state?.robot.fault?'이동 응답 불확실':'적용 실패 · 목표 갱신 필요';
};
$('gripperSettings').onclick=()=>window.open('/gripper/settings','OmniPickerSettings','width=1050,height=900,noopener');
createViewer(); position(); render(); refresh(); setInterval(refresh,500);
// Read-only hooks used by the browser QA; no device command is exposed here.
window.OMNI_VIEW={get viewer(){return viewer;},get preview(){return preview;},get state(){return state;},get goalPose(){return goalPose;}};
window.OMNI_EDIT={loadPoint(point){
  $('motionMode').value='joint';$('motionMode').onchange();preview=point.joints.slice();
  $('velocity').value=point.velocity??5;$('tool').value=point.tool??0;$('user').value=point.user??0;
  if(point.gripper){const names={position:'position',speed:'gripSpeed',torque:'gripTorque',acceleration:'gripAcceleration',deceleration:'gripDeceleration'};for(const [key,id]of Object.entries(names))if(Number.isFinite(point.gripper[key]))$(id).value=point.gripper[key];position();}
  updateView();schedulePreview();
}};
