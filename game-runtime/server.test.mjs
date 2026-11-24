import test from 'node:test';
import assert from 'node:assert/strict';
import { request } from 'node:http';
import { createGameServer } from './server.mjs';
import { createRace, stepRace, observeRace, raceMetrics } from '../frontend/src/gameplay/racing.mjs';

async function withServer(fn, options={}) {
  const server = createGameServer(options); await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const base=`http://127.0.0.1:${server.address().port}`;
  try { await fn(base); } finally { server.closeAllConnections(); await new Promise(resolve=>server.close(resolve)); }
}
async function call(base,path,body,method='POST') {
 const response=await fetch(base+path,{method,headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
 return {status:response.status,body:await response.json()};
}
test('HTTP observations, events and metrics match the exact browser rules',()=>withServer(async base=>{
 const created=await call(base,'/v1/sessions',{seed:7}); assert.equal(created.status,201);
 const id=created.body.session_id; const state=createRace(7);
 const controls={throttle:1,brake:0,steer:.2,reset:0};
 for(let i=0;i<20;i++) {
  const events=stepRace(state,controls,12);
  const result=await call(base,`/v1/sessions/${id}/step`,{controls,ticks:12});
  assert.equal(result.status,200); assert.deepEqual(result.body.state,JSON.parse(JSON.stringify(observeRace(state))));
  assert.deepEqual(result.body.events,events);assert.deepEqual(result.body.metrics,raceMetrics(state));
 }
 assert.equal((await call(base,`/v1/sessions/${id}`,undefined,'DELETE')).status,200);
 assert.equal((await call(base,`/v1/sessions/${id}`,undefined,'GET')).status,404);
}));
test('rejects malformed/unbounded requests before changing state',()=>withServer(async base=>{
 const id=(await call(base,'/v1/sessions',{seed:7})).body.session_id;
 for(const body of [{controls:{throttle:99},ticks:12},{controls:{},ticks:99999},{controls:{reset:true},ticks:12},{controls:{move:1},ticks:12}])
  assert.equal((await call(base,`/v1/sessions/${id}/step`,body)).status,400);
 assert.equal((await call(base,`/v1/sessions/${id}`,undefined,'GET')).body.state.tick,0);
 assert.equal((await call(base,'/v1/sessions',{seed:-1})).status,400);
}));
test('caps concurrent sessions and expires abandoned games',()=>{ let clock=0; return withServer(async base=>{
 assert.equal((await call(base,'/v1/sessions',{seed:1})).status,201);
 assert.equal((await call(base,'/v1/sessions',{seed:2})).status,429);
 clock=26;
 assert.equal((await call(base,'/v1/sessions',{seed:3})).status,201);
},{maxSessions:1,ttlMs:25,now:()=>clock}); });

test('static routes cannot expose source files or the internal API through /game',()=>withServer(async base=>{
 for(const path of ['/game/server.mjs','/game/.env','/game/%2e%2e%2fserver.mjs','/game/v1/sessions'])assert.equal((await fetch(base+path)).status,404);
}));

// An invalid absolute request target must not reject the async HTTP callback.
test('malformed URL returns 400 and leaves the runtime alive',()=>withServer(async base=>{
 const result=await new Promise((resolve,reject)=>{
  const req=request({hostname:'127.0.0.1',port:new URL(base).port,path:'http://[',method:'GET'},response=>{response.resume();response.on('end',()=>resolve(response.statusCode));});
  req.on('error',reject);req.end();
 });
 assert.equal(result,400);
 assert.equal((await fetch(base+'/health')).status,200);
}));
