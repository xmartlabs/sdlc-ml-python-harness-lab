export interface CameraTarget {x:number;z:number;heading:number}
export interface CameraOptions {mode:'overview'|'follow';target?:number}
export interface OverviewFit {centerX:number;centerZ:number;halfWidth:number;halfHeight:number}
export interface ScreenPoint {x:number;y:number}
export function fitTrackOverview(points:readonly {x:number;z:number}[],shoulder:number,width:number,height:number,margin?:number):OverviewFit;
export function followCameraPose(state:CameraTarget):{position:{x:number;y:number;z:number};lookAt:{x:number;y:number;z:number}};
export function selectFollowTarget<T>(observations:readonly (T|undefined)[],target?:number):T|undefined;
export function layoutParticipantLabels(points:(ScreenPoint|undefined)[],width:number,height:number,size?:number,gap?:number):(ScreenPoint|undefined)[];
