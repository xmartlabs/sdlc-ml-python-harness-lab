/** Display-only geometry. These helpers never mutate a simulation or advance time. */
export function fitTrackOverview(points,shoulder,width,height,margin=.04){
 const minX=Math.min(...points.map(p=>p.x))-shoulder,maxX=Math.max(...points.map(p=>p.x))+shoulder;
 const minZ=Math.min(...points.map(p=>p.z))-shoulder,maxZ=Math.max(...points.map(p=>p.z))+shoulder;
 const aspect=Math.max(1,width)/Math.max(1,height),padding=1+margin*2;
 const halfHeight=Math.max((maxZ-minZ)/2,(maxX-minX)/2/aspect)*padding;
 return {centerX:(minX+maxX)/2,centerZ:(minZ+maxZ)/2,halfWidth:halfHeight*aspect,halfHeight};
}
export function followCameraPose(state){
 const dx=Math.cos(state.heading),dz=Math.sin(state.heading);
 return {position:{x:state.x-dx*10,y:8,z:state.z-dz*10},lookAt:{x:state.x+dx*5,y:.6,z:state.z+dz*5}};
}
export function selectFollowTarget(observations,target=0){
 return observations[target]??observations.find(Boolean);
}
/** Pack number badges in screen space, including cars sharing the starting grid. */
export function layoutParticipantLabels(points,width,height,size=26,gap=4){
 const placed=[],half=size/2,pitch=size+gap;
 const clamp=(value,min,max)=>Math.max(min,Math.min(max,value));
 return points.map(point=>{
  if(!point)return undefined;
  const candidates=[];
  for(let ring=0;ring<=8;ring++)for(let dx=-ring;dx<=ring;dx++)for(let dy=-ring;dy<=ring;dy++){
   if(Math.max(Math.abs(dx),Math.abs(dy))!==ring)continue;
   candidates.push({x:clamp(point.x+dx*pitch,half,width-half),y:clamp(point.y-22+dy*pitch,half,height-half),cost:dx*dx+dy*dy});
  }
  candidates.sort((a,b)=>a.cost-b.cost);
  const selected=candidates.find(a=>placed.every(b=>Math.abs(a.x-b.x)>=pitch||Math.abs(a.y-b.y)>=pitch))??candidates[0];
  placed.push(selected);
  return {x:selected.x,y:selected.y};
 });
}
