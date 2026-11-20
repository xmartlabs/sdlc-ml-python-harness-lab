import { HubPage } from "../../components/HubPage";
import type { Capability } from "../../components/HubPage";
import { IconTrendUp } from "../../components/icons";
import { defineMessages, useMessages } from "../../i18n";

const MESSAGES = defineMessages<{ title: string; lede: string; items: Capability[] }>({
  en: {
    title: "Results",
    lede: "Everything the lab has measured. Read the validity banner first, then the verdict and its interval.",
    items: [
      {
        title: "Experiments",
        body: "Each question with its arms, repetitions and verdict.",
        status: ["ui"],
        to: "/evaluate/experiments"
      },
      {
        title: "Runs",
        body: "Queue state, logs, scores, per-step reward and the Play tab.",
        status: ["ui"],
        to: "/advanced/runs"
      },
      {
        title: "Ranking",
        body: "Harness quality by condition, every figure with its denominator.",
        status: ["ui"],
        to: "/advanced/benchmarks"
      },
      {
        title: "Findings",
        body: "What the runs showed, and whether the fix was tested.",
        status: ["ui"],
        to: "/evidence/findings"
      },
      {
        title: "Compare runs",
        body: "The same case under different conditions, side by side.",
        status: ["ui"],
        to: "/advanced/compare"
      },
      {
        title: "Improvement proposals",
        body: "Saved proposals and pruning verdicts.",
        status: ["ui"],
        to: "/advanced/improvements"
      }
    ]
  },
  es: {
    title: "Resultados",
    lede: "Todo lo que el lab midió. Primero el aviso de validez, después el veredicto y su intervalo.",
    items: [
      {
        title: "Experimentos",
        body: "Cada pregunta con sus brazos, repeticiones y veredicto.",
        status: ["ui"],
        to: "/evaluate/experiments"
      },
      {
        title: "Corridas",
        body: "Estado de la cola, logs, puntajes, recompensa por paso y la pestaña Jugar.",
        status: ["ui"],
        to: "/advanced/runs"
      },
      {
        title: "Ranking",
        body: "Calidad del arnés por condición, cada cifra con su denominador.",
        status: ["ui"],
        to: "/advanced/benchmarks"
      },
      {
        title: "Hallazgos",
        body: "Qué mostraron las corridas y si la corrección se probó.",
        status: ["ui"],
        to: "/evidence/findings"
      },
      {
        title: "Comparar corridas",
        body: "Mismo caso, distintas condiciones, lado a lado.",
        status: ["ui"],
        to: "/advanced/compare"
      },
      {
        title: "Propuestas de mejora",
        body: "Propuestas guardadas y veredictos de poda.",
        status: ["ui"],
        to: "/advanced/improvements"
      }
    ]
  }
});

export function ResultsHub() {
  const m = useMessages(MESSAGES);
  return <HubPage icon={IconTrendUp} items={m.items} lede={m.lede} title={m.title} />;
}
