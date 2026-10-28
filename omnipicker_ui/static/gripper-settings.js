(()=>{
  const {$,api,toast,events,errorBanner}=UI;let state=null,busy=false,filled=false;
  const fields=[['position','위치 RAW',0,255,127],['speed','속도 RAW',10,60,40],['torque','토크 RAW',10,60,40],['acceleration','가속도 RAW',1,255,40],['deceleration','감속도 RAW',1,255,40],['timeout','도달 대기 s',.1,120,8],['tolerance','위치 허용 오차 RAW',0,10,3]];
  function field(id,title,lo,hi,value,container){
    const label=document.createElement('label');label.textContent=title;
    const small=document.createElement('small');small.id=id+'Bounds';small.textContent=`최소 ${lo} · 최대 ${hi}`;
    const input=document.createElement('input');Object.assign(input,{id,required:true,type:'number',min:lo,max:hi,step:id==='timeout'?.1:1,value});label.append(small,input);$(container).append(label);
  }
  fields.forEach(f=>field(...f,'values'));
  ['speed_min','speed_max','torque_min','torque_max'].forEach((key,i)=>field(key,['속도 최소','속도 최대','토크 최소','토크 최대'][i],0,255,i%2?60:10,'limitValues'));
  function options(){
    const values={};for(const [id]of fields){if(!$(id).reportValidity())throw Error('입력 범위 확인');values[id]=Number($(id).value);}
    values.accept_contact=$('acceptContact').checked;return values;
  }
  async function run(path,data={}){
    if(busy)return;busy=true;render();
    try{
      const result=await api(path,data),r=result.result;
      $('result').textContent=path==='gripper/limits'?'입력 범위 적용 · 제어 잠금':
        r?.staged?(r.address!==undefined?`레지스터 ${r.address} = ${r.value} 기록 완료`:'파라미터 기록 완료 · 미실행'):
        r?.reached?`도달 완료 · 위치 ${r.actual_position} RAW`:r?.contact?'접촉 완료':
        path==='enable'?'60초 제어 허용':'처리 완료';
    }catch(e){toast(e.message,true);}finally{busy=false;await refresh();}
  }
  function render(){
    const robot=state?.robot,grip=state?.gripper;$('status').textContent=grip?.reason||'미연결';$('armState').textContent=robot?.armed?`제어 허용 · ${robot.seconds}초`:'잠금';
    const locked=busy||!robot?.armed||!grip?.feedback_valid||grip?.busy||robot?.busy;
    for(const id of ['send','stage','trigger'])$(id).disabled=!!locked;
    $('readRegisters').disabled=busy||!robot?.connected||!!robot?.busy||!!grip?.busy||!!robot?.usb_settings_active;$('wait').disabled=busy||!grip?.feedback_valid;
    $('enable').disabled=busy||!robot?.connected||robot?.armed||robot?.busy||grip?.busy||robot?.usb_settings_active;
    $('setLimits').disabled=busy||robot?.busy||grip?.busy;
    if(robot?.limits){
      for(const id of ['speed','torque']){const [lo,hi]=robot.limits['gripper_'+id];$(id).min=lo;$(id).max=hi;$(id+'Bounds').textContent=`최소 ${lo} · 최대 ${hi}`;if(!filled){$(id+'_min').value=lo;$(id+'_max').value=hi;}}
      filled=true;
    }
    document.querySelectorAll('[data-write]').forEach(b=>b.disabled=!!locked);events(state?.events||[]);errorBanner(robot?.error||'');
  }
  async function refresh(){try{state=await api('status');render();}catch(e){errorBanner(e.message);}}
  $('send').onclick=()=>{try{run('gripper',options());}catch(e){toast(e.message,true);}};
  $('stage').onclick=()=>{try{run('gripper/stage',options());}catch(e){toast(e.message,true);}};
  $('trigger').onclick=()=>run('gripper/trigger',{accept_contact:$('acceptContact').checked});
  $('wait').onclick=()=>{try{const o=options();run('gripper/wait',{timeout:o.timeout,tolerance:o.tolerance,accept_contact:o.accept_contact});}catch(e){toast(e.message,true);}};
  $('enable').onclick=()=>{if(!$('areaClear').checked)return toast('작업영역 확인 필요',true);$('areaClear').checked=false;run('enable',{confirm:'PHYSICAL_AREA_CLEAR'});};
  $('lock').onclick=()=>api('lock',{}).then(refresh).catch(e=>toast(e.message,true));$('stop').onclick=()=>api('stop',{}).then(refresh).catch(e=>toast(e.message,true));
  $('setLimits').onclick=()=>{const values={};for(const id of ['speed_min','speed_max','torque_min','torque_max']){if(!$(id).reportValidity())return;values[id]=Number($(id).value);}run('gripper/limits',values);};
  function preset(lo,hi){for(const id of ['speed','torque']){$(id+'_min').value=lo;$(id+'_max').value=hi;}}
  $('fullRange').onclick=()=>preset(0,255);$('defaultRange').onclick=()=>preset(10,60);
  $('readRegisters').onclick=async()=>{
    if(busy)return;busy=true;render();try{
      const {registers}=await api('gripper/registers',undefined,15000);$('registers').replaceChildren();
      for(const r of registers){
        const tr=document.createElement('tr');for(const text of [r.address,r.name,r.value]){const td=document.createElement('td');td.textContent=text;tr.append(td);}
        const edit=document.createElement('td');if(r.access==='rw'){
          const input=document.createElement('input');Object.assign(input,{type:'number',min:r.address===13||r.address===14?1:0,max:255,step:1,value:r.value});
          if(r.address===15)input.max=0;
          if(r.address===11||r.address===12){const bounds=state.robot.limits[r.address===11?'gripper_speed':'gripper_torque'];[input.min,input.max]=bounds;}
          input.title=`최소 ${input.min} · 최대 ${input.max}`;
          const button=document.createElement('button');button.textContent=r.address===15?'트리거 초기화':'쓰기';button.dataset.write=r.name;
          button.onclick=()=>{if(input.reportValidity())run('gripper/parameter',{name:r.name,value:Number(input.value)});};edit.append(input,button);
        }else edit.textContent='읽기 전용';tr.append(edit);$('registers').append(tr);
      }
    }catch(e){toast(e.message,true);}finally{busy=false;render();}
  };
  function tab(usb){$('rsPanel').hidden=usb;$('usbPanel').hidden=!usb;if(usb&&!$('usbFrame').src)$('usbFrame').src='/gripper/usb/';}
  $('rsTab').onclick=()=>tab(false);$('usbTab').onclick=$('openUSB').onclick=()=>tab(true);
  api('sdk/features').then(c=>{for(const f of c.omnipicker.features.filter(f=>!f.available)){const row=document.createElement('div');const name=document.createElement('code');name.textContent=f.name;const reason=document.createElement('span');reason.textContent=f.requires.join(' / ');row.append(name,reason);$('otherFeatures').append(row);}}).catch(e=>toast(e.message,true));
  refresh();setInterval(refresh,750);
})();
