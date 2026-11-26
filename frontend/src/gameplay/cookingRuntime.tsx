import { createRoot } from 'react-dom/client';
import { Cooking } from '../pages/Cooking';
import { SESSION_EXPIRED_EVENT } from '../api';
import './runtime.css';
window.addEventListener(SESSION_EXPIRED_EVENT, () => {
  if (window.parent !== window) window.parent.postMessage(SESSION_EXPIRED_EVENT, window.location.origin);
});
createRoot(document.getElementById('game-root')!).render(<Cooking />);
