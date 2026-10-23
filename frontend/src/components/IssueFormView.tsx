import { useState } from "react";
import { defineMessages, useMessages } from "../i18n";
import type { ProposalIssueForm } from "../types";

type IssueFormViewProps = {
  repository: string;
  markdown: string;
  form: ProposalIssueForm;
};

const MESSAGES = defineMessages({
  en: {
    openIssue: "Open prefilled issue on GitHub ↗",
    linkExplanation: (template: string, repository: string) => (
      <>
        The link opens the <code>{template}</code> form on {repository} with every
        field already filled in. Confirm the two fields marked{" "}
        <strong>recommended</strong> (channel and SemVer) and tick the sanitisation
        boxes before creating it. Each field can also be copied on its own if you
        would rather paste by hand.
      </>
    ),
    recommended: "recommended",
    copy: "Copy",
    copied: "Copied",
    copyAll: "Copy all",
    viewAsMarkdown: "View as a single markdown block (manual copy and paste)",
    issueFor: (repository: string) => `Issue for ${repository}`
  },
  es: {
    openIssue: "Abrir el issue precargado en GitHub ↗",
    linkExplanation: (template: string, repository: string) => (
      <>
        El enlace abre el formulario <code>{template}</code> en {repository} con
        todos los campos ya completos. Confirmá los dos campos marcados como{" "}
        <strong>recomendado</strong> (canal y SemVer) y tildá las casillas de
        depuración antes de crearlo. Cada campo también se puede copiar por
        separado si preferís pegarlo a mano.
      </>
    ),
    recommended: "recomendado",
    copy: "Copiar",
    copied: "Copiado",
    copyAll: "Copiar todo",
    viewAsMarkdown: "Ver como un bloque markdown único (copiar y pegar a mano)",
    issueFor: (repository: string) => `Issue para ${repository}`
  }
});

// Renders a sanitized issue result: prefilled GitHub URL button, per-field copy
// cards, and the full markdown behind a <details>. Same layout as the per-run
// card in RunDetail, extracted so the Improvements page can reuse it.
export function IssueFormView({ repository, markdown, form }: IssueFormViewProps) {
  const m = useMessages(MESSAGES);
  const [copiedField, setCopiedField] = useState<string | null>(null);
  const [markdownCopied, setMarkdownCopied] = useState(false);

  const handleCopyField = (key: string, text: string) => {
    navigator.clipboard
      .writeText(text)
      .then(() => {
        setCopiedField(key);
        window.setTimeout(
          () => setCopiedField((current) => (current === key ? null : current)),
          2000
        );
      })
      .catch(() => {
        // Clipboard access denied — the value stays selectable in the field.
      });
  };

  const handleCopyMarkdown = () => {
    navigator.clipboard
      .writeText(markdown)
      .then(() => {
        setMarkdownCopied(true);
        window.setTimeout(() => setMarkdownCopied(false), 2000);
      })
      .catch(() => {
        // Clipboard access denied — the markdown stays selectable in the <pre>.
      });
  };

  return (
    <>
      <div className="run-actions" style={{ marginBottom: "0.5rem" }}>
        <a
          className="primary"
          href={form.url}
          target="_blank"
          rel="noreferrer"
          style={{
            display: "inline-block",
            padding: "8px 14px",
            borderRadius: "6px",
            textDecoration: "none"
          }}
        >
          {m.openIssue}
        </a>
      </div>
      <p className="muted" style={{ fontSize: "0.85rem" }}>
        {m.linkExplanation(form.template, repository)}
      </p>
      <div className="proposal-fields" style={{ display: "grid", gap: "0.75rem" }}>
        {form.fields.map((field) => (
          <div key={field.id}>
            <div className="panel-head">
              {/* field.label is backend-composed and stays in English. */}
              <h4 style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
                {field.label}
                {field.recommended ? <span className="badge ok">{m.recommended}</span> : null}
              </h4>
              <button onClick={() => handleCopyField(field.id, field.value)} type="button">
                {copiedField === field.id ? m.copied : m.copy}
              </button>
            </div>
            {field.reason ? (
              <p className="muted" style={{ fontSize: "0.85rem", marginTop: 0 }}>
                {field.reason}
              </p>
            ) : null}
            <pre
              style={{
                fontFamily: "monospace",
                whiteSpace: "pre-wrap",
                margin: 0
              }}
            >
              {field.value}
            </pre>
          </div>
        ))}
      </div>
      <details style={{ marginTop: "1rem" }}>
        <summary>{m.viewAsMarkdown}</summary>
        <div className="panel-head" style={{ marginTop: "0.5rem" }}>
          <span className="muted">{m.issueFor(repository)}</span>
          <button onClick={handleCopyMarkdown} type="button">
            {markdownCopied ? m.copied : m.copyAll}
          </button>
        </div>
        <pre
          style={{
            fontFamily: "monospace",
            whiteSpace: "pre-wrap",
            maxHeight: "24rem",
            overflow: "auto"
          }}
        >
          {markdown}
        </pre>
      </details>
    </>
  );
}
