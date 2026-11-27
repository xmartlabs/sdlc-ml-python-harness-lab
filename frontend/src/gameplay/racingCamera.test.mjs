import test from 'node:test';
import assert from 'node:assert/strict';
import {TRACK} from './racing.mjs';
import {fitTrackOverview,followCameraPose,layoutParticipantLabels,selectFollowTarget} from './racingCamera.mjs';

test('overview frames a known rectangle tightly and centers asymmetric track bounds',()=>{
 const fit=fitTrackOverview([{x:10,z:-20},{x:110,z:30}],0,1000,500);
 assert.equal(fit.centerX,60);assert.equal(fit.centerZ,5);
 assert.ok(Math.abs(fit.halfWidth-54)<1e-9);
 assert.ok(Math.abs(fit.halfHeight-27)<1e-9);
});
test('whole track and road shoulders remain inside landscape, square and portrait frusta',()=>{
 for(const [width,height] of [[1920,1080],[800,800],[390,844],[1600,350]]){
  const shoulder=TRACK.width/2+3;
  const fit=fitTrackOverview(TRACK.centerline,shoulder,width,height);
  assert.ok(Math.abs(fit.halfWidth/fit.halfHeight-width/height)<1e-9);
  for(const point of TRACK.centerline){
   assert.ok(Math.abs(point.x-fit.centerX)+shoulder<fit.halfWidth);
   assert.ok(Math.abs(point.z-fit.centerZ)+shoulder<fit.halfHeight);
  }
  const occupiedWidth=(Math.max(...TRACK.centerline.map(p=>p.x))-Math.min(...TRACK.centerline.map(p=>p.x))+shoulder*2)/(fit.halfWidth*2);
  const occupiedHeight=(Math.max(...TRACK.centerline.map(p=>p.z))-Math.min(...TRACK.centerline.map(p=>p.z))+shoulder*2)/(fit.halfHeight*2);
  assert.ok(Math.max(occupiedWidth,occupiedHeight)>.85,'fit must not leave avoidable empty margins');
 }
});
test('zero-sized initial canvas has a finite resize-safe fit',()=>{
 assert.ok(Object.values(fitTrackOverview(TRACK.centerline,9,0,0)).every(Number.isFinite));
});
test('follow selects the requested participant and computes chase pose without modifying state',()=>{
 const observations=[{x:1,z:2,heading:0},{x:20,z:30,heading:Math.PI/2}];
 const before=structuredClone(observations);
 const target=selectFollowTarget(observations,1),pose=followCameraPose(target);
 assert.deepEqual(target,observations[1]);
 assert.ok(Math.abs(pose.position.x-20)<1e-9);assert.ok(pose.position.z<30);
 assert.ok(pose.lookAt.z>30);
 assert.deepEqual(observations,before);
 assert.equal(selectFollowTarget([undefined,observations[1]],0),observations[1]);
 assert.equal(selectFollowTarget([],7),undefined);
});
test('eight overlapping labels receive distinct visible screen rectangles',()=>{
 for(const [width,height,x,y] of [[1000,400,500,200],[320,500,160,250],[320,200,0,0],[320,200,320,0],[320,200,320,200],[320,200,0,200]]){
  const positions=layoutParticipantLabels(Array.from({length:8},()=>({x,y})),width,height);
  for(let i=0;i<positions.length;i++){
   const a=positions[i];assert.ok(a.x>=13&&a.x<=width-13);assert.ok(a.y>=13&&a.y<=height-13);
   for(let j=0;j<i;j++){const b=positions[j];assert.ok(Math.abs(a.x-b.x)>=30||Math.abs(a.y-b.y)>=30);}
  }
 }
});
