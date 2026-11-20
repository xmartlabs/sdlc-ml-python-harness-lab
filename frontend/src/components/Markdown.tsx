import { useEffect, useRef, useState, type ReactElement, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type MarkdownProps = {
  children: string;
  /**
   * Opens a link that points at another catalogued doc in place, instead of a
   * relative URL the app cannot serve. Returns false to let the link behave
   * normally. Used by the Guide, where the journeys link each other by file name.
   */
  onDocLink?: (href: string) => boolean;
  /**
   * Render ```mermaid blocks as diagrams. Off by default, and on only for the
   * Guide, whose markdown is the repository's own docs/. Run artifacts, audits
   * and reports are written by agents: rendering their mermaid would inject an
   * SVG built from untrusted text, so they keep showing it as code (ADR-0090).
   */
  diagrams?: boolean;
};

function isExternal(href: string): boolean {
  return /^(https?:)?\/\//.test(href) || href.startsWith("mailto:");
}

// Pinned past the published XSS advisories (GHSA-7rqq-prvp-x9jh,
// GHSA-8gwm-58g9-j8pw, GHSA-ghcm-xqfw-q4vr). Only trusted docs reach it.
const MERMAID_CDN_URL = "https://cdn.jsdelivr.net/npm/mermaid@11.17.2/dist/mermaid.esm.min.mjs";

// Minimal local typing for the mermaid ESM module loaded from the CDN at
// runtime — we never install mermaid as an npm dependency, so there is no
// package type declaration to rely on.
type MermaidRenderResult = { svg: string };
type MermaidModule = {
  default: {
    initialize: (config: { startOnLoad: boolean; theme: "dark" | "default"; securityLevel: "strict" }) => void;
    render: (id: string, text: string) => Promise<MermaidRenderResult>;
  };
};

let mermaidModulePromise: Promise<MermaidModule> | null = null;

function loadMermaid(): Promise<MermaidModule> {
  if (!mermaidModulePromise) {
    mermaidModulePromise = import(/* @vite-ignore */ MERMAID_CDN_URL) as Promise<MermaidModule>;
  }
  return mermaidModulePromise;
}

function isDarkTheme(): boolean {
  const dataTheme = document.documentElement.dataset.theme;
  if (dataTheme) {
    return dataTheme === "dark";
  }
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
}

let mermaidDiagramCounter = 0;

// Renders a mermaid fenced code block as an inline SVG diagram, loading the
// mermaid runtime lazily from a CDN (no npm dependency). Falls back to the
// original source code if the module fails to load or the diagram is
// invalid — this must never throw and break the rest of the page.
function MermaidDiagram({ source }: { source: string }) {
  const [svg, setSvg] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const idRef = useRef<string>(`mermaid-diagram-${++mermaidDiagramCounter}`);

  useEffect(() => {
    let cancelled = false;
    loadMermaid()
      .then(async (mermaid) => {
        mermaid.default.initialize({
          startOnLoad: false,
          theme: isDarkTheme() ? "dark" : "default",
          securityLevel: "strict"
        });
        const result = await mermaid.default.render(idRef.current, source);
        if (!cancelled) {
          setSvg(result.svg);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setFailed(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [source]);

  if (svg) {
    return <div className="markdown-mermaid" dangerouslySetInnerHTML={{ __html: svg }} />;
  }
  if (failed) {
    return (
      <pre>
        <code className="language-mermaid">{source}</code>
      </pre>
    );
  }
  return (
    <pre>
      <code className="language-mermaid">{source}</code>
    </pre>
  );
}

function codeText(node: ReactNode): string {
  if (typeof node === "string") {
    return node;
  }
  if (Array.isArray(node)) {
    return node.map(codeText).join("");
  }
  return "";
}

// Shared markdown renderer (GFM, no raw HTML pass-through — react-markdown
// escapes HTML by default, which is the sanitization strategy here). The one
// exception is a mermaid diagram's SVG, and only when `diagrams` is set, which
// only the Guide does: its markdown is the repository's own docs.
export function Markdown({ children, onDocLink, diagrams = false }: MarkdownProps) {
  return (
    <div className="markdown-body">
      <ReactMarkdown
        components={{
          pre: ({ children: preChildren }: { children?: ReactNode }): ReactElement => {
            const codeElement = Array.isArray(preChildren) ? preChildren[0] : preChildren;
            const codeProps =
              codeElement && typeof codeElement === "object" && "props" in codeElement
                ? (codeElement.props as { className?: string; children?: ReactNode })
                : undefined;
            const isMermaid = /\blanguage-mermaid\b/.test(codeProps?.className ?? "");
            if (diagrams && isMermaid) {
              return <MermaidDiagram source={codeText(codeProps?.children).replace(/\n$/, "")} />;
            }
            return <pre>{preChildren}</pre>;
          },
          a: ({ href, children: label }: { href?: string; children?: ReactNode }) => {
            const target = href ?? "";
            if (isExternal(target)) {
              return (
                <a href={target} rel="noreferrer" target="_blank">
                  {label}
                </a>
              );
            }
            return (
              <a
                href={target}
                onClick={(event) => {
                  if (onDocLink?.(target)) {
                    event.preventDefault();
                  }
                }}
              >
                {label}
              </a>
            );
          }
        }}
        remarkPlugins={[remarkGfm]}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
