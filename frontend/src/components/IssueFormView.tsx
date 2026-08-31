import { useState } from "react";
import type { ProposalIssueForm } from "../types";

type IssueFormViewProps = {
  repository: string;
  markdown: string;
  form: ProposalIssueForm;
};

// Renders a sanitized issue result: prefilled GitHub URL button, per-field copy
// cards, and the full markdown behind a <details>. Same layout as the per-run
// card in RunDetail, extracted so the Improvements page can reuse it.
export function IssueFormView({ repository, markdown, form }: IssueFormViewProps) {
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
          Open prefilled issue on GitHub ↗
        </a>
      </div>
      <p className="muted" style={{ fontSize: "0.85rem" }}>
        The link opens the <code>{form.template}</code> form on {repository} with every
        field already filled in. Confirm the two fields marked{" "}
        <strong>recommended</strong> (channel and SemVer) and tick the sanitisation
        boxes before creating it. Each field can also be copied on its own if you
        would rather paste by hand.
      </p>
      <div className="proposal-fields" style={{ display: "grid", gap: "0.75rem" }}>
        {form.fields.map((field) => (
          <div key={field.id}>
            <div className="panel-head">
              <h4 style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
                {field.label}
                {field.recommended ? <span className="badge ok">recommended</span> : null}
              </h4>
              <button onClick={() => handleCopyField(field.id, field.value)} type="button">
                {copiedField === field.id ? "Copied" : "Copy"}
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
        <summary>View as a single markdown block (manual copy and paste)</summary>
        <div className="panel-head" style={{ marginTop: "0.5rem" }}>
          <span className="muted">Issue for {repository}</span>
          <button onClick={handleCopyMarkdown} type="button">
            {markdownCopied ? "Copied" : "Copy all"}
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
