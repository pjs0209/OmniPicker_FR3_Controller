import * as THREE from 'three';
const DEG=180/Math.PI;
const AXES=[new THREE.Vector3(1,0,0),new THREE.Vector3(0,1,0),new THREE.Vector3(0,0,1)];

// Geometry is frozen at pointer-down, so IK and ghost updates cannot move the drag reference.
export class AxisDrag {
  constructor(viewer,frame,action,event,hit){
    this.viewer=viewer;this.action=action;this.pointerId=event.pointerId;
    this.start=new THREE.Vector2(event.clientX,event.clientY);this.amount=0;
    this.centre=frame.getWorldPosition(new THREE.Vector3());
    const rotation=action.kind==='joint'||action.axis>=3;
    this.rotation=rotation;
    this.axis=AXES[action.kind==='joint'?2:action.axis%3].clone().applyQuaternion(frame.getWorldQuaternion(new THREE.Quaternion())).normalize();
    const view=viewer.camera.getWorldDirection(new THREE.Vector3());
    this.ray=new THREE.Raycaster();
    if(rotation){
      this.plane=new THREE.Plane().setFromNormalAndCoplanarPoint(this.axis,this.centre);
      const point=this.intersection(event);
      this.last=point?.sub(this.centre).normalize();
      this.planar=Math.abs(view.dot(this.axis))>.2&&point&&point.length()>.0001;
      let radial=hit.clone().sub(this.centre).addScaledVector(this.axis,-hit.clone().sub(this.centre).dot(this.axis));
      if(radial.length()<.001){
        radial=new THREE.Vector3(1,0,0).applyQuaternion(viewer.camera.quaternion);
        radial.addScaledVector(this.axis,-radial.dot(this.axis));
      }
      const radius=Math.max(radial.length(),action.kind==='joint'?.047:.095);
      radial.normalize();
      const tangent=new THREE.Vector3().crossVectors(this.axis,radial).multiplyScalar(radius);
      const edge=this.centre.clone().addScaledVector(radial,radius);
      const screen=this.project(edge),end=this.project(edge.clone().add(tangent));
      this.screenAxis=end.sub(screen);this.scale=Math.max(this.screenAxis.length(),35);
      if(this.screenAxis.length()<1)this.screenAxis.set(1,0);else this.screenAxis.normalize();
    }else{
      const normal=view.clone().addScaledVector(this.axis,-view.dot(this.axis));
      this.planar=normal.length()>.1;
      this.plane=new THREE.Plane().setFromNormalAndCoplanarPoint(normal.normalize(),this.centre);
      this.origin=this.planar?this.intersection(event):null;
      const up=new THREE.Vector3(0,1,0).applyQuaternion(viewer.camera.quaternion);
      this.scale=Math.max(this.project(this.centre.clone().add(up)).distanceTo(this.project(this.centre)),1);
      this.screenAxis=new THREE.Vector2(0,-1);
    }
  }
  project(point){
    const p=point.clone().project(this.viewer.camera),r=this.viewer.renderer.domElement.getBoundingClientRect();
    return new THREE.Vector2(r.left+(p.x+1)*r.width/2,r.top+(1-p.y)*r.height/2);
  }
  intersection(event){
    const r=this.viewer.renderer.domElement.getBoundingClientRect();
    this.ray.setFromCamera(new THREE.Vector2((event.clientX-r.left)/r.width*2-1,-(event.clientY-r.top)/r.height*2+1),this.viewer.camera);
    return this.ray.ray.intersectPlane(this.plane,new THREE.Vector3());
  }
  update(event){
    if(this.rotation&&this.planar){
      const point=this.intersection(event);if(!point)return this.amount;
      const next=point.sub(this.centre);if(next.length()<1e-5)return this.amount;next.normalize();
      const angle=Math.atan2(this.axis.dot(new THREE.Vector3().crossVectors(this.last,next)),this.last.dot(next));
      this.amount+=angle*DEG;this.last=next;
    }else if(!this.rotation&&this.planar&&this.origin){
      const point=this.intersection(event);if(point)this.amount=point.sub(this.origin).dot(this.axis)*1000;
    }else{
      const pixels=new THREE.Vector2(event.clientX,event.clientY).sub(this.start).dot(this.screenAxis);
      this.amount=pixels/this.scale*(this.rotation?DEG:1000);
    }
    return this.amount;
  }
}
