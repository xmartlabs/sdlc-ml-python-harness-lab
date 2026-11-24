/** Baked fixed-game executable: static WebGL build + bounded headless sessions. */
import { createServer } from 'node:http';
import { randomUUID, createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { resolve, extname, sep } from 'node:path';
import { createRace, stepRace, observeRace, raceMetrics, isTerminal, baselineControls, TRACK } from '../frontend/src/gameplay/racing.mjs';

const engineHash=createHash('sha256').update(readFileSync(new URL('../frontend/src/gameplay/racing.mjs',import.meta.url))).digest('hex');
export function createGameServer({maxSessions=32,ttlMs=900_000,staticRoot=fileURLToPath(new URL('./public/',import.meta.url)),now=Date.now}={}) {
 const sessions=new Map();
 const snapshot=(state,events=[])=>({state:observeRace(state),metrics:raceMetrics(state),terminal:isTerminal(state),events});
 const json=(response,status,body)=>{response.writeHead(status,{'Content-Type':'application/json','Cache-Control':'no-store'});response.end(JSON.stringify(body));};
 const prune=()=>{for(const [id,session] of sessions)if(now()-session.touched>ttlMs)sessions.delete(id);};
 const timer=setInterval(prune,30_000);timer.unref();
 const server=createServer(async(request,response)=>{
  prune();
  try {
   // WHATWG URL normalization treats a raw backslash as a slash. Reject it
   // before routing so the public /game/ proxy cannot reach private /v1/.
   if(request.url.includes('\\'))return json(response,400,{error:'Invalid runtime request'});
   const path=new URL(request.url,'http://runtime').pathname;
   if(request.method==='GET' && path==='/health')return json(response,200,{status:'ok',api_version:'game-runtime.v1',environment_id:TRACK.id,environment_version:TRACK.version,engine_sha256:engineHash,node_version:process.version,sessions:sessions.size});
   if(request.method==='GET'&&path==='/game/build.json')return json(response,200,{engine_sha256:engineHash,environment_id:TRACK.id,environment_version:TRACK.version});
   if(path.startsWith('/v1/')) {
    let payload={};
    if(request.method==='POST') {
     let bytes=0,raw='';for await(const chunk of request){bytes+=chunk.length;if(bytes>8192){json(response,413,{error:'Request body exceeds 8192 bytes'});request.resume();return;}raw+=chunk;}
     payload=JSON.parse(raw);if(!payload||Array.isArray(payload)||typeof payload!=='object')throw new Error('Object required');
    }
    if(path==='/v1/sessions'&&request.method==='POST') {
     if(Object.keys(payload).some(k=>k!=='seed')||!Number.isSafeInteger(payload.seed)||payload.seed<0||payload.seed>4294967295)throw new Error('Invalid seed');
     if(sessions.size>=maxSessions)return json(response,429,{error:'Runtime session capacity reached'});
     const id=randomUUID(),state=createRace(payload.seed);sessions.set(id,{state,touched:now()});
     return json(response,201,{session_id:id,...snapshot(state),engine_sha256:engineHash,node_version:process.version});
    }
    const match=path.match(/^\/v1\/sessions\/([a-f0-9-]{36})(?:\/(step|baseline))?$/);
    const session=match&&sessions.get(match[1]);
    if(!session)return json(response,404,{error:'Unknown or expired game session'});
    session.touched=now();
    if(request.method==='DELETE'&&!match[2]){sessions.delete(match[1]);return json(response,200,{closed:true});}
    if(request.method==='GET'&&!match[2])return json(response,200,snapshot(session.state));
    if(request.method==='POST'&&match[2]==='baseline')return json(response,200,{...snapshot(session.state),baseline:baselineControls(session.state)});
    if(request.method==='POST'&&match[2]==='step') {
     if(Object.keys(payload).some(k=>!['controls','ticks'].includes(k))||!Number.isSafeInteger(payload.ticks)||payload.ticks<1||payload.ticks>240)throw new Error('Invalid interval');
     if(!payload.controls||Array.isArray(payload.controls)||typeof payload.controls!=='object')throw new Error('Controls required');
     const events=stepRace(session.state,payload.controls,payload.ticks);
     return json(response,200,snapshot(session.state,events));
    }
    return json(response,405,{error:'Unsupported operation'});
   }
   if(request.method!=='GET'&&request.method!=='HEAD')return json(response,405,{error:'Read-only static assets'});
   // Only this baked static subtree is public; never proxy the internal API.
   if(!path.startsWith('/game/'))return json(response,404,{error:'Not found'});
   const relative=decodeURIComponent(path.slice('/game/'.length))||'gameplay.html';
   const root=resolve(staticRoot),target=resolve(root,relative);
   if(!target.startsWith(root+sep))return json(response,404,{error:'Not found'});
   let data;try{data=await readFile(target);}catch{return json(response,404,{error:'Not found'});}
   const type={'.html':'text/html','.js':'application/javascript','.css':'text/css','.svg':'image/svg+xml','.png':'image/png'}[extname(target)]||'application/octet-stream';
   response.writeHead(200,{'Content-Type':type,'Cache-Control':extname(target)==='.html'?'no-store':'public,max-age=31536000,immutable','X-Content-Type-Options':'nosniff'});response.end(request.method==='HEAD'?undefined:data);
  }catch{if(!response.headersSent)json(response,400,{error:'Invalid runtime request'});else response.end();}
 });
 server.requestTimeout=10_000;server.headersTimeout=10_000;server.on('close',()=>clearInterval(timer));
 return server;
}
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href)createGameServer().listen(Number(process.env.PORT||8080),'0.0.0.0');
