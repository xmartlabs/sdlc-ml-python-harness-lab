import * as THREE from 'three';
import {TRACK} from './racing.mjs';
import type {RaceObservation} from './racing.mjs';
import {COMPARISON_COLORS} from './comparisonGhost.mjs';

/** Overview spectator: display only, never changes any participant's simulation. */
export function createComparisonScene(canvas:HTMLCanvasElement) {
 const renderer=new THREE.WebGLRenderer({canvas,antialias:true});
 renderer.setPixelRatio(Math.min(window.devicePixelRatio,1.5));
 const scene=new THREE.Scene();scene.background=new THREE.Color('#163842');
 scene.add(new THREE.HemisphereLight('#ffffff','#213c3c',3));
 const camera=new THREE.PerspectiveCamera(48,1,.1,500);camera.position.set(0,125,102);camera.lookAt(0,0,0);
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
  const sprite=new THREE.Sprite(sm);sprite.scale.set(2.8,2.8,1);sprite.position.set(0,4+index*3.2,0);group.add(sprite);
  return group;
 });
 const resize=()=>{const w=canvas.clientWidth,h=canvas.clientHeight;renderer.setSize(w,h,false);camera.aspect=w/Math.max(h,1);camera.updateProjectionMatrix();};
 const observer=new ResizeObserver(resize);observer.observe(canvas);resize();
 return {
  render(observations:(RaceObservation|undefined)[]){cars.forEach((car,index)=>{const s=observations[index];car.visible=!!s;if(s){car.position.set(s.x,.15,s.z);car.rotation.y=-s.heading;}});renderer.render(scene,camera);},
  dispose(){observer.disconnect();scene.traverse(o=>{if(o instanceof THREE.Mesh)o.geometry.dispose();});materials.forEach(m=>m.dispose());textures.forEach(t=>t.dispose());renderer.dispose();}
 };
}
