import { NativeClisPanel } from "../components/NativeClisPanel";
import { defineMessages, useMessages } from "../i18n";

const MESSAGES = defineMessages({
  en: {
    title: "Agent CLIs",
    lede: "Enable each harness the lab can run, and test it with one real request."
  },
  es: {
    title: "CLIs de agentes",
    lede: "Habilitá cada harness que el lab puede correr, y probalo con un pedido real."
  }
});

/** System → Agent CLIs (ADR-0094): connect, test and disconnect each CLI. */
export function AgentClis() {
  const m = useMessages(MESSAGES);
  return (
    <section className="panel">
      <div className="panel-head">
        <h2>{m.title}</h2>
      </div>
      <p className="muted">{m.lede}</p>
      <NativeClisPanel />
    </section>
  );
}
