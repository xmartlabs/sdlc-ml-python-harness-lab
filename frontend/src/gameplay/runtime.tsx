import { createRoot } from 'react-dom/client';
import { Racing } from '../pages/Racing';
import './runtime.css';
import { SESSION_EXPIRED_EVENT } from '../api';

window.addEventListener(SESSION_EXPIRED_EVENT, () => {
  if (window.parent !== window) window.parent.postMessage(SESSION_EXPIRED_EVENT, window.location.origin);
});

createRoot(document.getElementById('game-root')!).render(<Racing />);
