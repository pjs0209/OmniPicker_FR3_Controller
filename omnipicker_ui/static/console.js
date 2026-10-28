const {$,api,toast,download}=UI;
let current=null,points=[];
try{const saved=JSON.parse(localStorage.getItem('omni.points')||'[]');if(Array.isArray(saved))points=saved.slice(0,100).map(validPoint);}catch{}
function workspace(name){
  for(const button of document.querySelectorAll('[data-workspace]'))button.classList.toggle('active',button.dataset.workspace===name);
  for(const [key,id]of Object.entries({operate:'workspaceOperate',points:'workspacePoints',diagnostics:'workspaceDiagnostics',configuration:'workspaceConfiguration'}))$(id).hidden=key!==name;
  if(name==='operate')requestAnimationFrame(()=>window.OMNI_VIEW?.viewer?.resize());
}
function panel(name,changeMode=true){
  for(const button of document.querySelectorAll('[data-panel]'))button.classList.toggle('active',button.dataset.panel===name);
  for(const [key,id]of Object.entries({robot:'robotPanel',tcp:'tcpPanel',grip:'gripPanel'}))$(id).hidden=key!==name;
  $('robotActions').hidden=name==='grip';
  if(changeMode&&name!=='grip'){
    const mode=name==='tcp'?'tool':$('motionMode').value==='tool'?'joint':$('motionMode').value;
    if($('motionMode').value!==mode){$('motionMode').value=mode;$('motionMode').dispatchEvent(new Event('change'));}
  }
}
for(const b of document.querySelectorAll('[data-workspace]'))b.onclick=()=>workspace(b.dataset.workspace);
for(const b of document.querySelectorAll('[data-panel]'))b.onclick=()=>panel(b.dataset.panel);
$('motionMode').addEventListener('change',()=>{panel($('motionMode').value==='tool'?'tcp':'robot',false);$('selectedJoint').hidden=$('motionMode').value!=='joint_axes';});
$('robotViewer').addEventListener('jointselected',event=>{$('selectedJoint').textContent='J'+(event.detail+1)+' 회전';});
$('openGripperSettings').onclick=()=>$('gripperSettings').click();
window.addEventListener('controllerstate',event=>{
  current=event.detail;const r=current?.robot,g=current?.gripper;
  const metrics=[['FR3',r?.connected?'연결됨':'미연결'],['RS485',g?.feedback_valid?'연결됨':g?.reason||'미확인'],['툴 / 좌표계',`${r?.active_tool??'—'} / ${r?.active_user??'—'}`],['펌웨어',g?.firmware??'조회 대기'],['그리퍼 속도 / 토크',g?.feedback_valid?`${g.actual_speed} / ${g.actual_torque} RAW`:'—'],['제어',r?.armed?`${r.seconds}초 허용`:'잠금']];
  $('deviceMetrics').replaceChildren(...metrics.map(([label,value])=>{const node=document.createElement('article'),title=document.createElement('small'),text=document.createElement('strong');title.textContent=label;text.textContent=value;node.append(title,text);return node;}));
});
function persist(){localStorage.setItem('omni.points',JSON.stringify(points));renderPoints();}
function validPoint(p){
  if(!p||typeof p.name!=='string'||!p.name.trim()||p.name.length>80||!Array.isArray(p.joints)||p.joints.length!==6||!p.joints.every(x=>typeof x==='number'&&Number.isFinite(x)))throw Error('포인트 형식 오류');
  if(p.velocity!==undefined&&(!Number.isFinite(p.velocity)||p.velocity<1||p.velocity>100))throw Error('속도 1–100% 필요');
  for(const key of ['tool','user'])if(p[key]!==undefined&&(!Number.isInteger(p[key])||p[key]<0||p[key]>14))throw Error('툴·좌표계 0–14 필요');
  if(p.gripper){for(const key of ['position','speed','torque','acceleration','deceleration']){const v=p.gripper[key];if(!Number.isInteger(v)||v<(key==='acceleration'||key==='deceleration'?1:0)||v>255)throw Error('그리퍼 파라미터 범위 오류');}}
  return {name:p.name.trim(),joints:p.joints.slice(),velocity:p.velocity??5,tool:p.tool??0,user:p.user??0,...(p.gripper?{gripper:{...p.gripper}}:{})};
}
function renderPoints(){
  $('pointList').replaceChildren();if(!points.length){const e=document.createElement('div');e.className='empty-points';e.textContent='저장된 포인트가 없습니다.';$('pointList').append(e);return;}
  points.forEach((p,index)=>{
    const card=document.createElement('article');card.className='point-card';const title=document.createElement('h3');title.textContent=p.name;
    const values=document.createElement('code');values.textContent=p.joints.map((q,i)=>`J${i+1} ${q.toFixed(2)}°`).join(' · ');
    const meta=document.createElement('code');meta.textContent=`속도 ${p.velocity}% · 툴 ${p.tool} · 좌표계 ${p.user}`;
    const actions=document.createElement('div');actions.className='options';
    for(const [text,action]of [['목표로 불러오기',()=>{if(!current?.robot.connected)return toast('FR3 연결 필요',true);window.OMNI_EDIT.loadPoint(p);workspace('operate');panel('robot',false);}],['이름',()=>{const name=prompt('포인트 이름',p.name);if(name?.trim()){p.name=name.trim().slice(0,80);persist();}}],['삭제',()=>{points.splice(index,1);persist();}]]){const b=document.createElement('button');b.textContent=text;b.onclick=action;actions.append(b);}
    card.append(title,values,meta,actions);$('pointList').append(card);
  });
}
function savePoint(target){
  const joints=target?window.OMNI_VIEW?.preview:current?.robot.joints;if(!joints)return toast('저장할 자세 없음',true);
  if(points.length>=100)return toast('포인트 최대 100개',true);
  const name=prompt('포인트 이름',`Point ${String(points.length+1).padStart(2,'0')}`);if(!name?.trim())return;
  try{points.push(validPoint({name,joints,velocity:Number($('velocity').value),tool:Number($('tool').value),user:Number($('user').value),gripper:{position:Number($('position').value),speed:Number($('gripSpeed').value),torque:Number($('gripTorque').value),acceleration:Number($('gripAcceleration').value),deceleration:Number($('gripDeceleration').value)}}));persist();}catch(e){toast(e.message,true);}
}
$('saveCurrentPoint').onclick=()=>savePoint(false);$('saveTargetPoint').onclick=()=>savePoint(true);
$('exportPoints').onclick=()=>download('OmniPicker_FR3_points.json',{version:1,points});
$('importPoints').onclick=()=>$('pointFile').click();$('pointFile').onchange=async()=>{
  const file=$('pointFile').files[0];if(!file)return;
  try{if(file.size>256000)throw Error('파일 크기 초과');const value=JSON.parse(await file.text());if(value.version!==1||!Array.isArray(value.points)||value.points.length>100)throw Error('지원하지 않는 포인트 파일');points=value.points.map(validPoint);persist();}catch(e){toast(e.message,true);}finally{$('pointFile').value='';}
};
renderPoints();
