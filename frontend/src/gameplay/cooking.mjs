/** Original fixed kitchen. All work, walking and deadlines use simulation ticks. */
export const COOK_TICK_RATE = 10;
export const COOK_ACTION_TICKS = 10;
export const COOK_MAX_TICKS = 930;
const COUNTDOWN = 30;
const CHOP_TICKS = 20;
const COOK_TICKS = 30;
const BURN_TICKS = 130;
export const COOKING = Object.freeze({id:'harbor-kitchen',version:'1',name:'Harbor Kitchen',
  stations:Object.freeze([
    {id:0,name:'Tomato pantry',x:-4,z:-3},{id:1,name:'Chopping board',x:0,z:-3},
    {id:2,name:'Soup hob',x:4,z:-3},{id:3,name:'Plate counter',x:4,z:3},
    {id:4,name:'Delivery window',x:0,z:3},{id:5,name:'Waste bin',x:-4,z:3},
  ].map(Object.freeze)),
});
function nextOrder(state) {
  // Stable seeded deadlines, without global RNG or changing recipe difficulty.
  const duration = 350 + ((state.seed + state.orderSerial * 17) % 6) * 10;
  state.orders = [{id:state.orderSerial++,recipe:'tomato-soup',deadline_tick:state.tick+duration}];
}
export function createKitchen(seed=7) {
  if(!Number.isSafeInteger(seed)||seed<0)throw new Error('seed must be a non-negative safe integer');
  return {version:'1',seed,tick:0,phase:'countdown',actor:{x:0,z:0,target:-1},held:'empty',chop_ticks:0,
    pot:{stage:'empty',ticks:0},orders:[],orderSerial:0,ordersFulfilled:0,ordersExpired:0,
    mistakes:0,waste:0,burns:0,workTicks:0};
}
export function kitchenTerminal(state){return state.phase==='finished';}
export function stepKitchen(state,controls,ticks=1){
  if(!controls || Object.keys(controls).some(k=>!['target','interact'].includes(k)) ||
    !Number.isInteger(controls.target)||controls.target < -1||controls.target > 5||
    !Number.isFinite(controls.interact)||controls.interact<0||controls.interact>1)throw new Error('Invalid kitchen controls');
  if(!Number.isSafeInteger(ticks)||ticks<0||ticks>COOK_MAX_TICKS)throw new Error('Invalid kitchen ticks');
  if(kitchenTerminal(state))return [];
  const events=[];
  const finishTick=()=>{
    if(state.tick>=COOK_MAX_TICKS){state.phase='finished';events.push('shift_finished');}
  };
  for(let i=0;i<ticks;i++){
    state.tick++;
    if(kitchenTerminal(state))continue; // Consume requested recorder interval, freeze finished world.
    if(state.tick<=COUNTDOWN){
      if(state.tick===COUNTDOWN){state.phase='running';nextOrder(state);events.push('shift_started');}
      continue;
    }
    if(state.orders.length && state.tick>=state.orders[0].deadline_tick){
      state.ordersExpired++;events.push('order_expired');nextOrder(state);
    }
    if(state.pot.stage==='cooking'||state.pot.stage==='ready'){
      state.pot.ticks++;
      if(state.pot.stage==='cooking'&&state.pot.ticks>=COOK_TICKS){state.pot.stage='ready';events.push('soup_ready');}
      if(state.pot.ticks>=BURN_TICKS){state.pot.stage='burnt';state.burns++;events.push('soup_burnt');}
    }
    state.actor.target=controls.target;
    const station=COOKING.stations[controls.target];
    if(!station){finishTick();continue;}
    const dx=station.x-state.actor.x,dz=station.z-state.actor.z,distance=Math.hypot(dx,dz);
    if(distance>0.001){const step=Math.min(distance,0.35);state.actor.x+=dx/distance*step;state.actor.z+=dz/distance*step;}
    if(Math.hypot(station.x-state.actor.x,station.z-state.actor.z)>0.05 || controls.interact<0.5){finishTick();continue;}
    switch(station.id){
      case 0:
        if(state.held==='empty'){state.held='raw';state.chop_ticks=0;events.push('tomato_taken');}break;
      case 1:
        if(state.held==='raw'){
          state.chop_ticks++;state.workTicks++;
          if(state.chop_ticks>=CHOP_TICKS){state.held='chopped';events.push('tomato_chopped');}
        }break;
      case 2:
        if(state.held==='chopped'&&state.pot.stage==='empty'){
          state.held='empty';state.pot={stage:'cooking',ticks:0};events.push('cooking_started');
        }else if(state.held==='empty'&&state.pot.stage==='ready'){
          state.held='cooked';state.pot={stage:'empty',ticks:0};events.push('soup_taken');
        }else if(state.held==='empty'&&state.pot.stage==='burnt'){
          state.pot={stage:'empty',ticks:0};state.waste++;events.push('burnt_soup_discarded');
        }break;
      case 3:
        if(state.held==='cooked'){state.held='plated';events.push('soup_plated');}break;
      case 4:
        if(state.held!=='empty'){
          if(state.held==='plated'&&state.orders.length){state.ordersFulfilled++;events.push('order_fulfilled');nextOrder(state);}
          else {state.mistakes++;state.waste++;events.push('incorrect_delivery');}
          state.held='empty';state.chop_ticks=0;
        }break;
      case 5:
        if(state.held!=='empty'){state.held='empty';state.chop_ticks=0;state.waste++;events.push('food_discarded');}break;
    }
    finishTick();
  }
  return events;
}
export function kitchenMetrics(state){return {
  orders_fulfilled:state.ordersFulfilled,orders_expired:state.ordersExpired,
  incorrect_deliveries:state.mistakes,food_wasted:state.waste,burns:state.burns,
  game_time_seconds:Math.max(0,Math.min(state.tick-COUNTDOWN,900))/COOK_TICK_RATE,
  finished:kitchenTerminal(state)?1:0,
};}
export function observeKitchen(state){return {
  environment_id:COOKING.id,environment_version:COOKING.version,phase:state.phase,seed:state.seed,tick:state.tick,
  countdown_seconds:Math.max(0,COUNTDOWN-state.tick)/COOK_TICK_RATE,
  game_time_seconds:kitchenMetrics(state).game_time_seconds,
  remaining_seconds:Math.max(0,COOK_MAX_TICKS-Math.max(COUNTDOWN,state.tick))/COOK_TICK_RATE,
  actor:{...state.actor},held:state.held,chop_ticks:state.chop_ticks,
  chop_required_ticks:CHOP_TICKS,pot:{...state.pot},cook_required_ticks:COOK_TICKS,burn_after_ticks:BURN_TICKS,
  orders:state.orders.map(order=>({...order,remaining_seconds:Math.max(0,order.deadline_tick-state.tick)/COOK_TICK_RATE})),
  stations:COOKING.stations.map(station=>({...station})),metrics:kitchenMetrics(state),
};}
/** Deterministic calibration, never an evaluated model. */
export function kitchenBaselineControls(state){
  let target=0;
  if(state.held==='raw')target=1;
  else if(state.held==='chopped')target=2;
  else if(state.held==='cooked')target=3;
  else if(state.held==='plated')target=4;
  else if(state.pot.stage!=='empty')target=2;
  return {target,interact:1};
}
