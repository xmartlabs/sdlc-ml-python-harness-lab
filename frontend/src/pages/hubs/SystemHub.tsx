import { HubPage } from "../../components/HubPage";
import type { Capability } from "../../components/HubPage";
import { IconSettings } from "../../components/icons";
import { defineMessages, useMessages } from "../../i18n";

const MESSAGES = defineMessages<{ title: string; lede: string; items: Capability[] }>({
  en: {
    title: "System",
    lede: "What you set up once.",
    items: [
      {
        title: "Settings",
        body: "Gateway, keys, default models and local runtimes. Writes .env without a restart.",
        status: ["ui"],
        to: "/system/settings"
      },
      {
        title: "Agent CLIs",
        body: "Claude, Codex, OpenCode: enable each one with clicks and test it with one real request.",
        status: ["ui"],
        to: "/system/agent-clis"
      },
      {
        title: "Health",
        body: "API, worker, gateway and local models: what answers and what does not.",
        status: ["ui"],
        to: "/system/health"
      },
      {
        title: "Guide",
        body: "One page per journey, and the long reference. Diagrams render in place.",
        status: ["ui"],
        to: "/system/guide"
      }
    ]
  },
  es: {
    title: "Sistema",
    lede: "Lo que se configura una vez.",
    items: [
      {
        title: "Configuración",
        body: "Gateway, claves, modelos por defecto y runtimes locales. Escribe en .env sin reiniciar.",
        status: ["ui"],
        to: "/system/settings"
      },
      {
        title: "CLIs de agentes",
        body: "Claude, Codex, OpenCode: habilitá cada uno con clicks y probalo con un pedido real.",
        status: ["ui"],
        to: "/system/agent-clis"
      },
      {
        title: "Estado",
        body: "API, worker, gateway y modelos locales: qué responde y qué no.",
        status: ["ui"],
        to: "/system/health"
      },
      {
        title: "Guía",
        body: "Una carilla por recorrido y la referencia larga. Los diagramas se ven en el lugar.",
        status: ["ui"],
        to: "/system/guide"
      }
    ]
  }
});

export function SystemHub() {
  const m = useMessages(MESSAGES);
  return <HubPage icon={IconSettings} items={m.items} lede={m.lede} title={m.title} />;
}
