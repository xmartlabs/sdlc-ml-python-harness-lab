/** Fixed builds are served by the dedicated game runtime, not the dev checkout. */
import { useEffect, useRef } from 'react';
import { SESSION_EXPIRED_EVENT } from '../api';

export function RacingRuntime() {
  const frame = useRef<HTMLIFrameElement>(null);
  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.origin === window.location.origin && event.source === frame.current?.contentWindow && event.data === SESSION_EXPIRED_EVENT) {
        window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
      }
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, []);
  return <section aria-label="Fixed racing game"><iframe ref={frame} title="Tidal Ring racing runtime" src="/game/" style={{width:'100%',height:'calc(100vh - 120px)',minHeight:700,border:0,borderRadius:16}} /></section>;
}
