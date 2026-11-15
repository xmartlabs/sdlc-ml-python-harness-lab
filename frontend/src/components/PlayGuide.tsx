import { defineMessages, useMessages } from "../i18n";
import type { RunContext } from "../types";

// How to play the game a run built, shown above the Play desktop (ADR-0078).
// Two sources, kept apart on purpose: the case's guide says what the contract
// asked for; the keys come from the game itself (the grader reads its live
// InputMap), so a game that bound different keys is shown as it really is.

const MESSAGES = defineMessages({
  en: {
    title: "How to play",
    controls: "Controls",
    bound: "Keys this build bound",
    boundHint: "read from the game itself; may differ from what the case asked for",
    notBound: "not bound",
    start: "Getting started",
    startSteps: [
      "Press Launch below: the game starts on its own in a desktop in your browser.",
      "Click once inside the game window before using the keyboard — the browser has to give it focus.",
      "If the game closes, a window shows why; the terminal underneath can relaunch it."
    ],
    cpu: "It renders on the CPU (no GPU in the container): playable, but heavy scenes are slower than on a real machine."
  },
  es: {
    title: "Cómo jugar",
    controls: "Controles",
    bound: "Teclas que asignó este juego",
    boundHint: "leídas del juego mismo; pueden diferir de lo que pidió el caso",
    notBound: "sin tecla",
    start: "Para empezar",
    startSteps: [
      "Tocá Lanzar abajo: el juego arranca solo en un escritorio en tu navegador.",
      "Hacé un clic dentro de la ventana del juego antes de usar el teclado: el navegador tiene que darle el foco.",
      "Si el juego se cierra, una ventana muestra por qué; la terminal de abajo lo puede relanzar."
    ],
    cpu: "Se renderiza en la CPU (el contenedor no tiene GPU): se puede jugar, pero las escenas pesadas van más lentas que en una máquina real."
  }
});

const ACTION_LABELS: Record<string, string> = {
  accelerate: "accelerate",
  brake: "brake",
  steer_left: "steer left",
  steer_right: "steer right",
  reset_car: "reset car",
  move_forward: "forward",
  move_back: "back",
  move_left: "left",
  move_right: "right",
  sprint: "sprint",
  interact: "interact"
};

export function PlayGuide({ context }: { context: RunContext }) {
  const m = useMessages(MESSAGES);
  const guide = context.play_guide && "goal" in context.play_guide ? context.play_guide : null;
  const bound = context.bound_keys ?? {};
  const actions = Object.keys(bound).sort();
  return (
    <section className="play-guide" aria-label={m.title}>
      <div className="play-guide-main">
        <h3>{m.title}</h3>
        {guide?.goal ? <p className="play-guide-goal">{guide.goal}</p> : null}
        {guide && guide.controls.length > 0 ? (
          <dl className="play-guide-controls">
            {guide.controls.map(([what, keys]) => (
              <div key={what}>
                <dt>{what}</dt>
                <dd>{keys}</dd>
              </div>
            ))}
          </dl>
        ) : null}
        {guide && guide.tips.length > 0 ? (
          <ul className="play-guide-tips">
            {guide.tips.map((tip) => (
              <li key={tip}>{tip}</li>
            ))}
          </ul>
        ) : null}
      </div>
      <div className="play-guide-side">
        <h4>{m.start}</h4>
        <ol>
          {m.startSteps.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
        {actions.length > 0 ? (
          <>
            <h4>
              {m.bound} <span className="muted small">— {m.boundHint}</span>
            </h4>
            <dl className="play-guide-bound">
              {actions.map((action) => (
                <div key={action}>
                  <dt>{ACTION_LABELS[action] ?? action}</dt>
                  <dd>
                    {bound[action].length > 0 ? (
                      bound[action].map((key) => <kbd key={key}>{key}</kbd>)
                    ) : (
                      <span className="warn-text">{m.notBound}</span>
                    )}
                  </dd>
                </div>
              ))}
            </dl>
          </>
        ) : null}
        <p className="muted small">{m.cpu}</p>
      </div>
    </section>
  );
}
