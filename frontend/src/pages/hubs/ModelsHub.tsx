import { HubPage } from "../../components/HubPage";
import type { Capability, FlowStep } from "../../components/HubPage";
import { IconCpu } from "../../components/icons";
import { defineMessages, useMessages } from "../../i18n";

const MESSAGES = defineMessages<{
  title: string;
  lede: string;
  flow: FlowStep[];
  items: Capability[];
}>({
  en: {
    title: "Models",
    lede: "Connect a provider, tune a local model if you have one, and compare models on the same case.",
    flow: [
      { title: "Connect", detail: "a gateway or a Claude account" },
      { title: "Tune", detail: "only for a local model" },
      { title: "Compare", detail: "same case, same prompt" },
      { title: "Read", detail: "checks per phase, cost, time" }
    ],
    items: [
      {
        title: "Connect a provider",
        body: "A LiteLLM gateway or a Claude account — either is enough. Refresh from gateway pulls the model list.",
        status: ["ui"],
        to: "/system/settings"
      },
      {
        title: "Tune your local model",
        body: "Machine, server, configuration, load and result. Estimate whether it fits before spending 20–45 minutes on a case.",
        status: ["ui"],
        to: "/local/tune"
      },
      {
        title: "Compare models on one case",
        body: "Every model gets the same prompt, steps and repetitions. Only the model changes.",
        status: ["ui"],
        to: "/evaluate/compare-models"
      },
      {
        title: "Does a stronger model need less harness?",
        body: "A harness × model grid. Reports the harness effect per model, with its interval.",
        status: ["ui"],
        to: "/evaluate/harness-by-model"
      },
      {
        title: "Local model results",
        body: "Speed tests, and real cases run with a local model as the executor.",
        status: ["ui"],
        to: "/local/results"
      }
    ]
  },
  es: {
    title: "Modelos",
    lede: "Conectá un proveedor, afiná un modelo local si tenés uno, y comparalos sobre el mismo caso.",
    flow: [
      { title: "Conectá", detail: "un gateway o una cuenta Claude" },
      { title: "Afiná", detail: "solo si es local" },
      { title: "Compará", detail: "mismo caso, mismo prompt" },
      { title: "Leé", detail: "chequeos por fase, costo, tiempo" }
    ],
    items: [
      {
        title: "Conectá un proveedor",
        body: "Un gateway LiteLLM o una cuenta Claude: con uno alcanza. «Refrescar desde el gateway» trae la lista de modelos.",
        status: ["ui"],
        to: "/system/settings"
      },
      {
        title: "Afiná tu modelo local",
        body: "Máquina, servidor, configuración, carga y resultado. Estimá si entra antes de gastar 20 a 45 minutos en un caso.",
        status: ["ui"],
        to: "/local/tune"
      },
      {
        title: "Compará modelos en un caso",
        body: "Todos reciben el mismo prompt, pasos y repeticiones. Solo cambia el modelo.",
        status: ["ui"],
        to: "/evaluate/compare-models"
      },
      {
        title: "¿Un modelo más fuerte necesita menos arnés?",
        body: "Grilla arnés × modelo. Reporta el efecto del arnés por modelo, con su intervalo.",
        status: ["ui"],
        to: "/evaluate/harness-by-model"
      },
      {
        title: "Resultados de modelos locales",
        body: "Pruebas de velocidad y casos reales corridos con un modelo local como ejecutor.",
        status: ["ui"],
        to: "/local/results"
      }
    ]
  }
});

export function ModelsHub() {
  const m = useMessages(MESSAGES);
  return <HubPage flow={m.flow} icon={IconCpu} items={m.items} lede={m.lede} title={m.title} />;
}
