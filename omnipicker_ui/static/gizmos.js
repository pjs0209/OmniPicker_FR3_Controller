import * as THREE from 'three';
import {AxisDrag} from './gizmo-drag.js';
const axes=[new THREE.Vector3(1,0,0),new THREE.Vector3(0,1,0),new THREE.Vector3(0,0,1)];
const colors=[0xff6868,0x58dc8a,0x68adff];
function material(color){return new THREE.MeshBasicMaterial({color,depthTest:false,depthWrite:false,transparent:true,opacity:.95});}
function label(text,color,position,size=.026){
  const canvas=document.createElement('canvas');canvas.width=256;canvas.height=64;
  const ctx=canvas.getContext('2d');ctx.font='bold 38px sans-serif';ctx.textAlign='center';ctx.fillStyle=color;ctx.fillText(text,128,47);
  const sprite=new THREE.Sprite(new THREE.SpriteMaterial({map:new THREE.CanvasTexture(canvas),depthTest:false,depthWrite:false}));
  sprite.position.copy(position);sprite.scale.set(size*4,size,1);sprite.renderOrder=30;return sprite;
}
function arrow(axis,sign,length,color,action){
  const group=new THREE.Group();group.userData.action=action;
  const dir=axes[axis].clone().multiplyScalar(sign);
  const shaft=new THREE.Mesh(new THREE.CylinderGeometry(.0024,.0024,length-.016,8),material(color));
  shaft.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0),dir);shaft.position.copy(dir).multiplyScalar(length/2);group.add(shaft);
  const tip=new THREE.Mesh(new THREE.ConeGeometry(.008,.023,12),material(color));tip.userData.action=action;tip.quaternion.copy(shaft.quaternion);tip.position.copy(dir).multiplyScalar(length);group.add(tip);
  group.traverse(node=>node.renderOrder=25);return group;
}
function rotation(axis,radius,color,action){
  const group=new THREE.Group();group.quaternion.setFromUnitVectors(axes[2],axes[axis]);
  const points=Array.from({length:49},(_,i)=>new THREE.Vector3(radius*Math.cos(-.8+i*1.6/48),radius*Math.sin(-.8+i*1.6/48),0));
  const arc=new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(points),48,.003,8,false),material(color));arc.userData.action=action;group.add(arc);
  for(const sign of [-1,1]){
    const angle=sign*.8,tip=new THREE.Mesh(new THREE.ConeGeometry(.008,.023,12),material(color));
    tip.position.set(radius*Math.cos(angle),radius*Math.sin(angle),0);
    tip.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0),new THREE.Vector3(-Math.sin(angle)*sign,Math.cos(angle)*sign,0));
    tip.userData.action={...action,sign};group.add(tip);
  }
  group.traverse(node=>node.renderOrder=25);return group;
}
export class Gizmos{
  constructor(viewer){
    this.viewer=viewer;this.joints=[];this.mode='joint';
    viewer.goalArm.spec.joints.filter(j=>j.type==='revolute').forEach((joint,index)=>{
      const group=new THREE.Group();group.name='J'+(index+1)+' axes';
      group.add(rotation(2,.047,colors[2],{kind:'joint',joint:index}));
      viewer.goalArm.joints.get(joint.name).pivot.add(group);this.joints.push(group);
    });
    this.tool=new THREE.Group();this.tool.name='TCP axes';viewer.scene.add(this.tool);
    this.tcpFrame=new THREE.Group();viewer.goalArm.links.get(viewer.goalArm.spec.flange).add(this.tcpFrame);this.offset=[0,0,0,0,0,0];
    axes.forEach((axis,i)=>{
      for(const sign of [-1,1])this.tool.add(arrow(i,sign,.14,colors[i],{kind:'tool',axis:i,sign}));
      this.tool.add(label('XYZ'[i],['#ff6868','#58dc8a','#68adff'][i],axis.clone().multiplyScalar(.165),.023));
      const turn=rotation(i,.095,colors[i],{kind:'tool',axis:i+3});this.tool.add(turn);
      // Place an explicit R/P/Y label on each rotation plane.
      const text=label(['Roll','Pitch','Yaw'][i],['#ff6868','#58dc8a','#68adff'][i],new THREE.Vector3(.11,0,0),.019);turn.add(text);
    });
    const canvas=viewer.renderer.domElement;this.ray=new THREE.Raycaster();
    this.emit=(action,phase,amount=0)=>viewer.container.dispatchEvent(new CustomEvent('gizmochange',{detail:{...action,phase,amount},cancelable:true}));
    this.pointer=event=>{
      if(event.button!==0||this.mode==='joint'||this.drag)return;
      const rect=canvas.getBoundingClientRect();this.ray.setFromCamera(new THREE.Vector2((event.clientX-rect.left)/rect.width*2-1,-(event.clientY-rect.top)/rect.height*2+1),viewer.camera);
      viewer.scene.updateMatrixWorld(true);
      const hits=this.ray.intersectObjects(this.mode==='tool'?[this.tool]:this.joints,true);
      for(const hit of hits){
        let node=hit.object;while(node&&!node.userData.action)node=node.parent;
        if(!node?.userData.action)continue;
        const action=node.userData.action;
        if(action.kind==='joint')this.selectJoint(action.joint);
        event.preventDefault();event.stopImmediatePropagation();
        if(action.kind==='select'){this.emit(action,'select');return;}
        if(!this.emit(action,'start'))return;
        const frame=action.kind==='joint'?this.joints[action.joint]:this.tool;
        this.drag=new AxisDrag(viewer,frame,action,event,hit.point);
        this.controlsEnabled=viewer.controls.enabled;viewer.controls.enabled=false;
        canvas.style.cursor='grabbing';canvas.setPointerCapture(event.pointerId);return;
      }
    };
    this.move=event=>{
      if(!this.drag||event.pointerId!==this.drag.pointerId)return;
      event.preventDefault();event.stopImmediatePropagation();
      this.emit(this.drag.action,'move',this.drag.update(event));
    };
    this.up=event=>{
      if(!this.drag||event.pointerId!==this.drag.pointerId)return;
      event.preventDefault();event.stopImmediatePropagation();this.finish('end');
    };
    this.cancel=()=>this.finish('cancel');
    canvas.addEventListener('pointerdown',this.pointer,true);
    canvas.addEventListener('pointermove',this.move,true);
    canvas.addEventListener('pointerup',this.up,true);
    canvas.addEventListener('pointercancel',this.cancel,true);
    canvas.addEventListener('lostpointercapture',this.cancel,true);
    window.addEventListener('blur',this.cancel);
    this.setMode('joint');
  }
  finish(phase){
    const drag=this.drag;if(!drag)return;this.drag=null;
    const canvas=this.viewer.renderer.domElement;
    this.viewer.controls.enabled=this.controlsEnabled;canvas.style.cursor='';
    if(canvas.hasPointerCapture(drag.pointerId))canvas.releasePointerCapture(drag.pointerId);
    this.emit(drag.action,phase,drag.amount);
  }
  setMode(mode){this.finish('cancel');this.mode=mode;for(const group of this.joints)group.visible=mode==='joint_axes';this.tool.visible=mode==='tool';}
  selectJoint(index){
    this.selectedJoint=index;
    this.joints.forEach((group,i)=>group.traverse(node=>{if(node.material?.color)node.material.color.setHex(i===index?0xf6b449:0x56b9ed);}));
    this.viewer.container.dispatchEvent(new CustomEvent('jointselected',{detail:index}));
  }
  tcp(offset){
    const key=offset.join(',');if(key!==this.offset.join(',')){this.referenceKey=null;this.offset=offset.slice();}
    this.tcpFrame.position.fromArray(offset.slice(0,3).map(x=>x/1000));
    this.tcpFrame.quaternion.setFromEuler(new THREE.Euler(...offset.slice(3).map(x=>x*Math.PI/180),'ZYX'));
  }
  draft(reference,delta){
    if(!reference||!delta)return;
    const key=reference.join(',')+'|'+this.offset.join(',');
    if(key!==this.referenceKey){
      this.viewer.goalArm.arm(reference);this.viewer.scene.updateMatrixWorld(true);
      this.referenceMatrix=this.tcpFrame.matrixWorld.clone();this.referenceKey=key;
      this.viewer.goalArm.arm(this.viewer.target||reference);
    }
    const transform=new THREE.Matrix4().compose(new THREE.Vector3(...delta.slice(0,3).map(x=>x/1000)),new THREE.Quaternion().setFromEuler(new THREE.Euler(...delta.slice(3).map(x=>x*Math.PI/180),'ZYX')),new THREE.Vector3(1,1,1));
    this.referenceMatrix.clone().multiply(transform).decompose(this.tool.position,this.tool.quaternion,this.tool.scale);
    this.tool.updateMatrixWorld(true);
  }
  dispose(){
    this.finish('cancel');this.tcpFrame.removeFromParent();const canvas=this.viewer.renderer.domElement;
    for(const [name,handler]of [['pointerdown',this.pointer],['pointermove',this.move],['pointerup',this.up],['pointercancel',this.cancel],['lostpointercapture',this.cancel]])canvas.removeEventListener(name,handler,true);
    window.removeEventListener('blur',this.cancel);
    for(const root of [...this.joints,this.tool]){root.removeFromParent();root.traverse(n=>{n.geometry?.dispose();if(n.material){n.material.map?.dispose();n.material.dispose();}});}
  }
}
