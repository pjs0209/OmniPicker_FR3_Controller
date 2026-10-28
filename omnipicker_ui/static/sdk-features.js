// Read-only API inventory. Device methods cannot be invoked from this panel.
const {$,api}=UI;
let entries=[],loaded=false;
function renderFeatures() {
  const text=$('sdkSearch').value.toLowerCase(),device=$('sdkDevice').value;
  const visible=entries.filter(row=>(!device || row.device===device) &&
    `${row.name} ${row.signature||''} ${row.requires?.join(' ')||''}`.toLowerCase().includes(text));
  $('sdkCount').textContent=`${visible.length} / ${entries.length}`;
  const fragment=document.createDocumentFragment();
  for(const row of visible) {
    const node=document.createElement('div');node.className='sdk-feature';
    const name=document.createElement('code');name.textContent=row.signature||row.name;
    const state=document.createElement('span');
    state.textContent=row.device==='fr3' ? 'FR3 원본 API' : row.available ? 'RS485 지원' : `필요: ${(row.requires||[]).join(' / ')}`;
    state.className=row.available?'supported':'unavailable';
    node.append(name,state);fragment.append(node);
  }
  $('sdkFeatureList').replaceChildren(fragment);
}
$('sdkFeatures').addEventListener('toggle',async()=>{
  if(!$('sdkFeatures').open || loaded)return;
  try {
    const data=await api('sdk/features');
    entries=[...data.fr3.methods.map(row=>({...row,device:'fr3',available:true})),...data.omnipicker.features];
    loaded=true;renderFeatures();
  } catch(error) {$('sdkCount').textContent=error.message;}
});
$('sdkSearch').addEventListener('input',renderFeatures);
$('sdkDevice').addEventListener('change',renderFeatures);
