import { useMemo, useState } from "react";

import { defineMessages, useMessages } from "../i18n";

/**
 * A small unified diff viewer for an ad hoc run's patch (ADR-0095).
 *
 * The patch is one `git diff` for the whole change; this splits it back into
 * per-file hunks, colours +/- lines, and collapses each file so a patch
 * touching many files does not open as one wall of text. Rendering is capped
 * so a huge patch does not freeze the tab.
 */

const MAX_LINES = 3000;

const MESSAGES = defineMessages({
  en: {
    showMore: (n: number) => `Show ${n} more lines`,
    truncatedNote: "This patch is larger than the lab keeps for the review screen."
  },
  es: {
    showMore: (n: number) => `Mostrar ${n} líneas más`,
    truncatedNote: "Este patch es más grande de lo que el lab guarda para la pantalla de revisión."
  }
});

type FileDiff = { path: string; lines: string[] };

/** Split one unified diff into its `diff --git a/x b/x` sections. */
function splitByFile(patch: string): FileDiff[] {
  const lines = patch.split("\n");
  const files: FileDiff[] = [];
  let current: FileDiff | null = null;
  for (const line of lines) {
    if (line.startsWith("diff --git ")) {
      const match = /diff --git a\/(.+) b\/(.+)$/.exec(line);
      current = { path: match ? match[2] : line, lines: [line] };
      files.push(current);
    } else if (current) {
      current.lines.push(line);
    }
  }
  return files;
}

function lineClass(line: string): string {
  if (line.startsWith("+++") || line.startsWith("---")) return "diff-line diff-line--meta";
  if (line.startsWith("+")) return "diff-line diff-line--add";
  if (line.startsWith("-")) return "diff-line diff-line--del";
  if (line.startsWith("@@")) return "diff-line diff-line--hunk";
  return "diff-line";
}

function FileSection({ file }: { file: FileDiff }) {
  const [open, setOpen] = useState(true);
  const [showAll, setShowAll] = useState(false);
  const m = useMessages(MESSAGES);
  const capped = showAll ? file.lines : file.lines.slice(0, MAX_LINES);
  const remaining = file.lines.length - capped.length;

  return (
    <details className="diff-file" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary className="diff-file-head mono small">{file.path}</summary>
      <pre className="diff-body mono small">
        {capped.map((line, index) => (
          <span className={lineClass(line)} key={index}>
            {line}
            {"\n"}
          </span>
        ))}
      </pre>
      {remaining > 0 ? (
        <button className="ghost" onClick={() => setShowAll(true)} type="button">
          {m.showMore(remaining)}
        </button>
      ) : null}
    </details>
  );
}

export function DiffView({ patch, truncated }: { patch: string; truncated?: boolean }) {
  const m = useMessages(MESSAGES);
  const files = useMemo(() => splitByFile(patch), [patch]);

  return (
    <div className="diff-view">
      {truncated ? <p className="muted small">{m.truncatedNote}</p> : null}
      {files.map((file) => (
        <FileSection file={file} key={file.path} />
      ))}
    </div>
  );
}
