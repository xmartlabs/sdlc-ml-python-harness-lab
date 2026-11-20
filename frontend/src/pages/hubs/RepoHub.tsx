import { HubPage } from "../../components/HubPage";
import type { Capability, FlowStep } from "../../components/HubPage";
import { IconRepo } from "../../components/icons";
import { defineMessages, useMessages } from "../../i18n";

const MESSAGES = defineMessages<{
  title: string;
  lede: string;
  flow: FlowStep[];
  items: Capability[];
}>({
  en: {
    title: "Your repo",
    lede: "Start with the 30-second check. If you want more, audit it and take home what it is missing.",
    flow: [
      { title: "Check", detail: "eight checks, no stack, no cost" },
      { title: "Audit", detail: "your clone, one pinned commit" },
      { title: "Measure", detail: "one case that passes" },
      { title: "Take it home", detail: "the harness or one skill" }
    ],
    items: [
      {
        title: "Check a harness in 30 seconds",
        body: "Eight checks decided by code, not a model. One fix per finding.",
        status: ["cli"],
        command: "make harness-check REPO=../your-project"
      },
      {
        title: "Audit a repository",
        body: "Registers your clone, pins a commit, reads what it already ships, and ends when one case passes.",
        status: ["ui"],
        to: "/evaluate/audit"
      },
      {
        title: "Validate your tool",
        body: "An agent, an MCP server or a service wrapper, and the skill that drives it. Does it work reliably?",
        status: ["ui"],
        to: "/evaluate/subject"
      },
      {
        title: "Take the harness or one skill home",
        body: "Copies the governance, or a single skill, into your project — previewed first.",
        status: ["cli"],
        command: "make harness-export REPO=../your-project SKILLS=<name> GOVERNANCE=0"
      },
      {
        title: "Registered repositories",
        body: "Each repository's workbench: cases, findings and reports.",
        status: ["ui"],
        to: "/evidence/repositories"
      }
    ]
  },
  es: {
    title: "Tu repo",
    lede: "Empezá por el chequeo de 30 segundos. Si querés más, auditalo y llevate lo que le falta.",
    flow: [
      { title: "Chequeá", detail: "ocho chequeos, sin stack ni costo" },
      { title: "Auditá", detail: "tu clon, un commit fijado" },
      { title: "Medí", detail: "un caso que pase" },
      { title: "Llevátelo", detail: "el arnés o una skill" }
    ],
    items: [
      {
        title: "Revisá el arnés en 30 segundos",
        body: "Ocho chequeos que decide el código, no un modelo. Una corrección por hallazgo.",
        status: ["cli"],
        command: "make harness-check REPO=../tu-proyecto"
      },
      {
        title: "Auditá un repositorio",
        body: "Registra tu clon, fija un commit, lee lo que ya trae y termina cuando un caso pasa.",
        status: ["ui"],
        to: "/evaluate/audit"
      },
      {
        title: "Validá tu herramienta",
        body: "Un agente, un servidor MCP o un wrapper, y la skill que lo maneja. ¿Funciona de forma confiable?",
        status: ["ui"],
        to: "/evaluate/subject"
      },
      {
        title: "Llevate el arnés o una skill",
        body: "Copia la gobernanza, o una sola skill, a tu proyecto, con vista previa.",
        status: ["cli"],
        command: "make harness-export REPO=../tu-proyecto SKILLS=<nombre> GOVERNANCE=0"
      },
      {
        title: "Repositorios registrados",
        body: "El banco de trabajo de cada repo: casos, hallazgos y reportes.",
        status: ["ui"],
        to: "/evidence/repositories"
      }
    ]
  }
});

export function RepoHub() {
  const m = useMessages(MESSAGES);
  return <HubPage flow={m.flow} icon={IconRepo} items={m.items} lede={m.lede} title={m.title} />;
}
