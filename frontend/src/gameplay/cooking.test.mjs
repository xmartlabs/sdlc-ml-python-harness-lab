import test from 'node:test';
import assert from 'node:assert/strict';
import {COOKING,COOK_MAX_TICKS,createKitchen,stepKitchen,observeKitchen,kitchenMetrics,kitchenTerminal,kitchenBaselineControls} from './cooking.mjs';
function station(s,id,interact=1,ticks=1) {const p=COOKING.stations[id];s.actor.x=p.x;s.actor.z=p.z;stepKitchen(s,{target:id,interact},ticks);}
function start(){const s=createKitchen(7);stepKitchen(s,{target:-1,interact:0},30);return s;}
test('countdown, terminal interval and deterministic controls',()=>{
 const a=createKitchen(7),b=createKitchen(7);stepKitchen(a,{target:0,interact:1},10);assert.equal(a.held,'empty');
 stepKitchen(b,{target:0,interact:1},10);assert.deepEqual(a,b);
 const before=structuredClone(b);assert.throws(()=>stepKitchen(b,{target:1.1,interact:0},1),/controls/);assert.deepEqual(b,before);
 assert.throws(()=>stepKitchen(b,{target:0,interact:true},1),/controls/);
 stepKitchen(a,{target:-1,interact:0},COOK_MAX_TICKS);assert.equal(a.phase,'finished');const tick=a.tick;stepKitchen(a,{target:-1,interact:0},10);assert.equal(a.tick,tick);
});
test('calibration delivers valid soup with deterministic state and independent kitchens',()=>{
 const a=createKitchen(7), untouched=createKitchen(7);
 while(!kitchenTerminal(a))stepKitchen(a,kitchenBaselineControls(a),10);
 assert.ok(kitchenMetrics(a).orders_fulfilled>=1);assert.equal(untouched.tick,0);
 const b=createKitchen(7);while(!kitchenTerminal(b))stepKitchen(b,kitchenBaselineControls(b),10);assert.deepEqual(a,b);
});
test('incorrect deliveries and discarding food count distinct mistakes and waste',()=>{
 const s=start();station(s,0);assert.equal(s.held,'raw');station(s,4);assert.equal(s.mistakes,1);assert.equal(s.waste,1);assert.equal(s.held,'empty');
 station(s,0);station(s,5);assert.equal(s.waste,2);assert.equal(s.ordersFulfilled,0);
});
test('chopping requires work, pot cooks and burns after grace; burnt food cannot score',()=>{
 const s=start();station(s,0);station(s,1,1,19);assert.equal(s.held,'raw');station(s,1);assert.equal(s.held,'chopped');station(s,2);assert.equal(s.pot.stage,'cooking');assert.equal(s.held,'empty');
 stepKitchen(s,{target:-1,interact:0},30);assert.equal(s.pot.stage,'ready');stepKitchen(s,{target:-1,interact:0},100);assert.equal(s.pot.stage,'burnt');assert.equal(s.burns,1);
 station(s,2);assert.equal(s.pot.stage,'empty');assert.equal(s.waste,1);assert.equal(s.ordersFulfilled,0);
});
test('orders expire against simulation time; no interaction from a distance',()=>{
 const s=start();stepKitchen(s,{target:-1,interact:1},1);assert.equal(s.held,'empty');
 const deadline=s.orders[0].deadline_tick;stepKitchen(s,{target:-1,interact:0},deadline-s.tick);assert.equal(s.ordersExpired,1);assert.ok(s.orders[0].deadline_tick>deadline);
 assert.ok(observeKitchen(s).remaining_seconds>0);
});
test('terminal at call entry is a no-op while final action consumes its full interval',()=>{
 const state=createKitchen(7);
 stepKitchen(state,{target:-1,interact:0},929);
 stepKitchen(state,{target:-1,interact:0},10);
 assert.equal(state.tick,939);
 assert.equal(state.phase,'finished');
 const finished=structuredClone(state);
 assert.deepEqual(stepKitchen(state,{target:0,interact:1},10),[]);
 assert.deepEqual(state,finished);
});
test('last shift tick permits delivery and finishes both interacting and idle shifts',()=>{
 const active=start();active.tick=929;active.held='plated';active.orders[0].deadline_tick=1000;
 const delivery=COOKING.stations[4];active.actor.x=delivery.x;active.actor.z=delivery.z;
 stepKitchen(active,{target:4,interact:1},1);
 assert.equal(active.ordersFulfilled,1);assert.equal(active.phase,'finished');assert.equal(active.tick,930);
 const idle=start();idle.tick=929;stepKitchen(idle,{target:-1,interact:0},1);
 assert.equal(idle.phase,'finished');assert.equal(idle.tick,930);
});
