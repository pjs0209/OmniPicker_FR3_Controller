(()=>{
  const {$,api,toast}=UI;let features=[],selected=null;
  const ranges={bool:'false / true',uint8:'최소 0 · 최대 255',uint32:'최소 0 · 최대 4294967295',int32:'최소 −2147483648 · 최대 2147483647',float:'최소 −3.4028234e38 · 최대 3.4028234e38 (float32)'};
  function field(name,type){
    const label=document.createElement('label');label.textContent=name+' · '+type;
    const small=document.createElement('small');small.textContent=ranges[type]||type;label.append(small);
    const input=document.createElement(type==='bool'?'select':'input');input.dataset.arg=name;input.dataset.type=type;
    if(type==='bool'){for(const b of ['false','true']){const o=document.createElement('option');o.textContent=b;input.append(o);}}
    else{input.type='number';input.step=type==='float'?'any':'1';input.value=0;const bounds={uint8:[0,255],uint32:[0,4294967295],int32:[-2147483648,2147483647],float:[-3.4028234e38,3.4028234e38]}[type];if(bounds){[input.min,input.max]=bounds;}}
    label.append(input);return label;
  }
  function choose(){
    selected=features.find(f=>f.name===$('usbEndpoint').value);$('endpointArgs').replaceChildren();if(!selected)return;
    $('endpointAccess').textContent=selected.type==='function'?'함수 · USB 제어 허용 후 실행':`${selected.access} · USB 제어 허용 후 쓰기`;
    for(const p of selected.type==='function'?selected.inputs:[{name:'value',type:selected.type}])$('endpointArgs').append(field(p.name,p.type));
    $('readEndpoint').disabled=selected.type==='function';$('executeEndpoint').disabled=selected.type!=='function'&&!selected.access?.includes('w');
  }
  $('loadEndpoints').onclick=async()=>{try{features=(await api('endpoints')).features;$('usbEndpoint').replaceChildren();for(const f of features){const o=document.createElement('option');o.value=f.name;o.textContent=f.name;$('usbEndpoint').append(o);}choose();}catch(e){toast(e.message,true);}};
  $('usbEndpoint').onchange=choose;
  $('readEndpoint').onclick=async()=>{if(!selected)return;try{const reply=await api('endpoint/read',{path:selected.name});$('endpointResult').textContent=JSON.stringify(reply,null,2);const input=$('endpointArgs').querySelector('input,select');if(input)input.value=String(reply.value);}catch(e){toast(e.message,true);}};
  $('executeEndpoint').onclick=async()=>{
    if(!selected)return;const args={};for(const input of $('endpointArgs').querySelectorAll('input,select')){if(!input.reportValidity())return;args[input.dataset.arg]=input.dataset.type==='bool'?input.value==='true':Number(input.value);}
    if(!confirm(selected.name+'\n'+JSON.stringify(args)+'\n선택한 USB 기능을 실행합니다.'))return;
    try{const result=await api('endpoint/execute',{path:selected.name,args,value:args.value,confirm:'EXECUTE_USB_ENDPOINT'});$('endpointResult').textContent=JSON.stringify(result,null,2);}catch(e){toast(e.message,true);}
  };
})();
