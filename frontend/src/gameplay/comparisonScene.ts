import * as THREE from 'three';
import {TRACK} from './racing.mjs';
import type {RaceObservation} from './racing.mjs';
import {COMPARISON_COLORS} from './comparisonGhost.mjs';
import {fitTrackOverview,followCameraPose,layoutParticipantLabels,selectFollowTarget} from './racingCamera.mjs';
import type {CameraOptions,ScreenPoint} from './racingCamera.mjs';

/** Overview spectator: display only, never changes any participant's simulation. */
export function createComparisonScene(canvas:HTMLCanvasElement) {
 const renderer=new THREE.WebGLRenderer({canvas,antialias:true});
 renderer.setPixelRatio(Math.min(window.devicePixelRatio,1.5));
 const scene=new THREE.Scene();scene.background=new THREE.Color('#163842');
 scene.add(new THREE.HemisphereLight('#ffffff','#213c3c',3));
 const overviewCamera=new THREE.OrthographicCamera(-80,80,60,-60,.1,500);
 overviewCamera.up.set(0,0,-1);
 const followCamera=new THREE.PerspectiveCamera(55,1,.1,500);
 let width=1,height=1,previousMode='overview',previousTarget=-1;
 const badges:{sprite:THREE.Sprite;line:THREE.Line}[]=[];
 const materials:THREE.Material[]=[],textures:THREE.Texture[]=[];
 const mat=(color:string)=>{const m=new THREE.MeshStandardMaterial({color,roughness:.9,side:THREE.DoubleSide});materials.push(m);return m;};
 const mesh=(geometry:THREE.BufferGeometry,material:THREE.Material,parent:THREE.Object3D=scene)=>{const m=new THREE.Mesh(geometry,material);parent.add(m);return m;};
 mesh(new THREE.CylinderGeometry(77,78,1,96),mat('#456951')).position.y=-.7;
 const road:number[]=[],indices:number[]=[];
 TRACK.centerline.forEach((p,i)=>{
  const before=TRACK.centerline[(i+TRACK.centerline.length-1)%TRACK.centerline.length],after=TRACK.centerline[(i+1)%TRACK.centerline.length];
  const dx=after.x-before.x,dz=after.z-before.z,len=Math.hypot(dx,dz);
  for(const side of [-1,1])road.push(p.x-dz/len*TRACK.width/2*side,.08,p.z+dx/len*TRACK.width/2*side);
  const a=i*2,b=((i+1)%TRACK.centerline.length)*2;indices.push(a,b,a+1,b,b+1,a+1);
 });
 const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(road,3));geometry.setIndex(indices);geometry.computeVertexNormals();mesh(geometry,mat('#26323b'));
 for(const [i,p] of TRACK.checkpoints.entries()){
  const marker=mesh(new THREE.BoxGeometry(.5,.15,TRACK.width),mat(i===11?'#ffffff':'#78929c'));marker.position.set(p.x,.16,p.z);marker.rotation.y=-p.heading;
 }
 const cars=COMPARISON_COLORS.map((color,index)=>{
  const group=new THREE.Group();scene.add(group);group.visible=false;
  const bodyMat=new THREE.MeshStandardMaterial({color,transparent:true,opacity:.72,depthWrite:false});materials.push(bodyMat);
  mesh(new THREE.BoxGeometry(3.5,.9,1.8),bodyMat,group).position.y=.8;
  mesh(new THREE.BoxGeometry(1.5,.6,1.4),mat('#14222c'),group).position.set(-.2,1.5,0);
  const nose=mesh(new THREE.BoxGeometry(.3,.2,1.5),mat(color),group);nose.position.set(1.7,1,0);
  const label=document.createElement('canvas');label.width=128;label.height=128;
  const ctx=label.getContext('2d')!;ctx.fillStyle=color;ctx.beginPath();ctx.arc(64,64,53,0,Math.PI*2);ctx.fill();ctx.fillStyle='#10212b';ctx.font='bold 78px sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(String(index+1),64,69);
  const texture=new THREE.CanvasTexture(label);textures.push(texture);
  const sm=new THREE.SpriteMaterial({map:texture,depthTest:false});materials.push(sm);
  const sprite=new THREE.Sprite(sm);sprite.renderOrder=10;scene.add(sprite);
  const lineMaterial=new THREE.LineBasicMaterial({color,transparent:true,opacity:.7,depthTest:false});materials.push(lineMaterial);
  const line=new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(),new THREE.Vector3()]),lineMaterial);line.renderOrder=9;scene.add(line);
  badges.push({sprite,line});
  return group;
 });
 const resize=()=>{
  width=Math.max(1,canvas.clientWidth);height=Math.max(1,canvas.clientHeight);renderer.setSize(width,height,false);
  const fit=fitTrackOverview(TRACK.centerline,TRACK.width/2+3,width,height);
  Object.assign(overviewCamera,{left:-fit.halfWidth,right:fit.halfWidth,top:fit.halfHeight,bottom:-fit.halfHeight});
  overviewCamera.position.set(fit.centerX,180,fit.centerZ);overviewCamera.lookAt(fit.centerX,0,fit.centerZ);overviewCamera.updateProjectionMatrix();
  followCamera.aspect=width/height;followCamera.updateProjectionMatrix();
 };
 const observer=new ResizeObserver(resize);observer.observe(canvas);resize();
 return {
  render(observations:(RaceObservation|undefined)[],options:CameraOptions={mode:'overview'}){
   cars.forEach((car,index)=>{const s=observations[index];car.visible=!!s;if(s){car.position.set(s.x,.15,s.z);car.rotation.y=-s.heading;}});
   const targetIndex=options.target??0,target=selectFollowTarget(observations,targetIndex);
   let camera:THREE.Camera=overviewCamera;
   if(options.mode==='follow'&&target){
    const pose=followCameraPose(target),position=new THREE.Vector3(pose.position.x,pose.position.y,pose.position.z);
    followCamera.position.lerp(position,previousMode==='follow'&&previousTarget===targetIndex ? .12 : 1);
    followCamera.lookAt(pose.lookAt.x,pose.lookAt.y,pose.lookAt.z);camera=followCamera;
   }
   previousMode=options.mode;previousTarget=targetIndex;camera.updateMatrixWorld();
   const projections=observations.slice(0,cars.length).map(s=>s?new THREE.Vector3(s.x,1.8,s.z).project(camera):undefined);
   const anchors:(ScreenPoint|undefined)[]=badges.map((_,index)=>{
    const p=projections[index];return p&&p.z>=-1&&p.z<=1&&Math.abs(p.x)<=1.05&&Math.abs(p.y)<=1.05?{x:(p.x+1)*width/2,y:(1-p.y)*height/2}:undefined;
   });
   const labels=layoutParticipantLabels(anchors,width,height);
   badges.forEach(({sprite,line},index)=>{
    const p=labels[index],s=observations[index],depth=projections[index]?.z;
    sprite.visible=line.visible=!!p&&!!s&&depth!==undefined;
    if(!p||!s||depth===undefined)return;
    const nx=p.x/width*2-1,ny=1-p.y/height*2;
    sprite.position.copy(new THREE.Vector3(nx,ny,depth).unproject(camera));
    const left=new THREE.Vector3(nx-26/width,ny,depth).unproject(camera),right=new THREE.Vector3(nx+26/width,ny,depth).unproject(camera);
    const size=left.distanceTo(right);sprite.scale.set(size,size,1);
    line.geometry.setFromPoints([new THREE.Vector3(s.x,1.8,s.z),sprite.position]);
   });
   renderer.render(scene,camera);
  },
  dispose(){observer.disconnect();scene.traverse(o=>{if(o instanceof THREE.Mesh||o instanceof THREE.Line)o.geometry.dispose();});materials.forEach(m=>m.dispose());textures.forEach(t=>t.dispose());renderer.dispose();}
 };
}
