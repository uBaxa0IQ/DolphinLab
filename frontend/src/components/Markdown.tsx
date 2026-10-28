import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { readableText } from "../lib/presentation";

export default function Markdown({
  text,
  technical = false,
  className = "",
}: {
  text: string;
  technical?: boolean;
  className?: string;
}) {
  const shown = technical ? readableText(text) : text;
  const content = (value: string) => (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      skipHtml
      components={{
        a: ({ children, href }) => (
          <a
            href={href}
            target={href?.startsWith("http") ? "_blank" : undefined}
            rel="noreferrer noopener"
          >
            {children}
          </a>
        ),
        img: ({ alt }) => <span className="muted">{alt || "Изображение"}</span>,
        table: ({ children }) => (
          <div className="markdown-table">
            <table>{children}</table>
          </div>
        ),
      }}
    >
      {value}
    </ReactMarkdown>
  );
  return (
    <div className={`markdown ${className}`}>
      {content(shown || "Подробности ответа сохранены ниже.")}
      {technical && shown !== text.trim() && (
        <details className="technical-details">
          <summary>Технические подробности</summary>
          {content(text)}
        </details>
      )}
    </div>
  );
}
