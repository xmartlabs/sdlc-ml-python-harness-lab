import type { ReactNode } from "react";
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
};

function isExternal(href: string): boolean {
  return /^(https?:)?\/\//.test(href) || href.startsWith("mailto:");
}

// Shared markdown renderer (GFM, no raw HTML pass-through — react-markdown
// escapes HTML by default, which is the sanitization strategy here).
export function Markdown({ children, onDocLink }: MarkdownProps) {
  return (
    <div className="markdown-body">
      <ReactMarkdown
        components={{
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
