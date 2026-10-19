import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export default function Markdown({ children, className }: { children: string; className?: string }) {
  return (
    <div className={`md ${className ?? ""}`} dir="auto">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{ a: ({ ...props }) => <a {...props} target="_blank" rel="noreferrer" /> }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
