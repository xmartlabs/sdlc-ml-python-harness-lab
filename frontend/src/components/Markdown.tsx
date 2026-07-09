import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type MarkdownProps = {
  children: string;
};

// Shared markdown renderer (GFM, no raw HTML pass-through — react-markdown
// escapes HTML by default, which is the sanitization strategy here).
export function Markdown({ children }: MarkdownProps) {
  return (
    <div className="markdown-body">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  );
}
