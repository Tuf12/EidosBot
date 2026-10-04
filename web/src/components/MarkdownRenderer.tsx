import React, { useState } from 'react';
import { Copy, Check, Terminal } from 'lucide-react';

interface MarkdownRendererProps {
  content: string;
}

interface CodeBlockProps {
  code: string;
  language?: string;
}

const CodeBlock: React.FC<CodeBlockProps> = ({ code, language }) => {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const cleanLang = (language || '').trim().toLowerCase();

  return (
    <div className="my-2.5 rounded-xl border border-[#44475a] bg-[#14151b] overflow-hidden shadow-inner">
      <div className="flex items-center justify-between px-3 py-1.5 bg-[#181a24] border-b border-[#44475a]/60 text-[10px] text-[#6272a4] font-mono">
        <div className="flex items-center gap-1.5 font-bold uppercase tracking-wider text-[#bd93f9]">
          <Terminal className="w-3 h-3 text-[#50fa7b]" />
          <span>{cleanLang || 'CODE'}</span>
        </div>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1 text-[#f8f8f2] hover:text-[#50fa7b] transition active:scale-95"
          title="Copy code to clipboard"
        >
          {copied ? <Check className="w-3 h-3 text-[#50fa7b]" /> : <Copy className="w-3 h-3" />}
          <span>{copied ? 'Copied' : 'Copy'}</span>
        </button>
      </div>
      <pre className="p-3 text-[11px] font-mono leading-relaxed text-[#50fa7b] overflow-x-auto whitespace-pre select-text">
        <code>{code}</code>
      </pre>
    </div>
  );
};

export const MarkdownRenderer: React.FC<MarkdownRendererProps> = ({ content }) => {
  if (!content) return null;

  // Split content by triple-backtick code blocks
  // Regex matches ```[lang]\n[code]```
  const codeBlockRegex = /```([a-zA-Z0-9_\-\+]*)\n?([\s\S]*?)```/g;
  const elements: React.ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  let blockIdx = 0;
  while ((match = codeBlockRegex.exec(content)) !== null) {
    // Text before the code block
    if (match.index > lastIndex) {
      const textBefore = content.substring(lastIndex, match.index);
      elements.push(
        <RenderRichText key={`text_${blockIdx}_${lastIndex}`} text={textBefore} />
      );
    }

    // Code block itself
    const lang = match[1] || '';
    const code = match[2] || '';
    elements.push(
      <CodeBlock key={`code_${blockIdx}`} language={lang} code={code.replace(/\n$/, '')} />
    );

    lastIndex = match.index + match[0].length;
    blockIdx++;
  }

  // Trailing text
  if (lastIndex < content.length) {
    const trailingText = content.substring(lastIndex);
    elements.push(
      <RenderRichText key={`text_end_${lastIndex}`} text={trailingText} />
    );
  }

  return <div className="space-y-1.5 select-text">{elements}</div>;
};

// Sub-component to parse line-by-line markdown
const RenderRichText: React.FC<{ text: string }> = ({ text }) => {
  const lines = text.split('\n');
  const renderedLines: React.ReactNode[] = [];

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // Headings
    if (line.startsWith('### ')) {
      renderedLines.push(
        <h3 key={i} className="text-xs font-bold text-[#bd93f9] mt-2 mb-1">
          {renderInlineSpans(line.slice(4))}
        </h3>
      );
    } else if (line.startsWith('## ')) {
      renderedLines.push(
        <h2 key={i} className="text-sm font-bold text-[#8be9fd] mt-2.5 mb-1">
          {renderInlineSpans(line.slice(3))}
        </h2>
      );
    } else if (line.startsWith('# ')) {
      renderedLines.push(
        <h1 key={i} className="text-sm font-extrabold text-[#bd93f9] mt-3 mb-1.5">
          {renderInlineSpans(line.slice(2))}
        </h1>
      );
    }
    // Blockquotes
    else if (line.startsWith('> ')) {
      renderedLines.push(
        <blockquote
          key={i}
          className="border-l-2 border-[#bd93f9] pl-2.5 py-0.5 my-1 text-[#6272a4] italic text-[11px]"
        >
          {renderInlineSpans(line.slice(2))}
        </blockquote>
      );
    }
    // Unordered lists
    else if (line.trim().startsWith('- ') || line.trim().startsWith('* ')) {
      const indent = line.search(/\S/);
      const bulletContent = line.trim().slice(2);
      renderedLines.push(
        <div
          key={i}
          style={{ paddingLeft: `${Math.max(4, indent * 6)}px` }}
          className="flex items-start gap-1.5 my-0.5"
        >
          <span className="text-[#bd93f9] font-bold text-xs leading-none mt-1">•</span>
          <span className="flex-1">{renderInlineSpans(bulletContent)}</span>
        </div>
      );
    }
    // Ordered lists
    else if (/^\s*\d+\.\s+/.test(line)) {
      const match = line.match(/^(\s*)(\d+)\.\s+(.*)$/);
      if (match) {
        const num = match[2];
        const rest = match[3];
        renderedLines.push(
          <div key={i} className="flex items-start gap-1.5 my-0.5 pl-1">
            <span className="text-[#bd93f9] font-mono text-[11px] font-bold shrink-0">{num}.</span>
            <span className="flex-1">{renderInlineSpans(rest)}</span>
          </div>
        );
      } else {
        renderedLines.push(<p key={i}>{renderInlineSpans(line)}</p>);
      }
    }
    // Blank lines
    else if (!line.trim()) {
      renderedLines.push(<div key={i} className="h-1.5" />);
    }
    // Normal paragraph
    else {
      renderedLines.push(
        <p key={i} className="leading-relaxed">
          {renderInlineSpans(line)}
        </p>
      );
    }
  }

  return <>{renderedLines}</>;
};

// Inline parser for `code`, **bold**, *italic*, and [links](url)
function renderInlineSpans(text: string): React.ReactNode {
  // Tokenize by inline code first: `code`
  const codeParts = text.split(/(`[^`\n]+`)/g);

  return codeParts.map((part, idx) => {
    if (part.startsWith('`') && part.endsWith('`') && part.length >= 2) {
      const codeSnippet = part.slice(1, -1);
      return (
        <code
          key={idx}
          className="bg-[#14151b] text-[#ff79c6] border border-[#44475a]/70 rounded px-1.5 py-0.5 font-mono text-[11px]"
        >
          {codeSnippet}
        </code>
      );
    }

    // Process bold, italics, and links inside normal text
    return <span key={idx}>{parseFormattingAndLinks(part)}</span>;
  });
}

async function handleLocalLinkClick(url: string) {
  try {
    const token = localStorage.getItem('eidos_token') || '';
    const res = await fetch('/api/open_link', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ target: url }),
    });
    if (!res.ok) {
      await navigator.clipboard.writeText(url);
    }
  } catch {
    try {
      await navigator.clipboard.writeText(url);
    } catch {
      // ignore
    }
  }
}

function parseFormattingAndLinks(raw: string): React.ReactNode {
  // Matches markdown links: [text](url)
  const linkRegex = /\[([^\]]+)\]\(([^)\s]+)\)/g;
  const parts: React.ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = linkRegex.exec(raw)) !== null) {
    if (match.index > lastIndex) {
      parts.push(parseRawUrlsAndFormatting(raw.substring(lastIndex, match.index), `txt_${lastIndex}`));
    }
    const label = match[1];
    const url = match[2].trim().replace(/^['"]|['"]$/g, '');
    const isWebUrl = url.startsWith('http://') || url.startsWith('https://') || url.startsWith('www.');
    const fullWebUrl = url.startsWith('www.') ? `https://${url}` : url;

    parts.push(
      <a
        key={`link_${match.index}`}
        href={isWebUrl ? fullWebUrl : '#'}
        target={isWebUrl ? '_blank' : undefined}
        rel={isWebUrl ? 'noopener noreferrer' : undefined}
        onClick={(e) => {
          if (!isWebUrl) {
            e.preventDefault();
            handleLocalLinkClick(url);
          }
        }}
        title={isWebUrl ? url : `Open file: ${url}`}
        className="text-[#8be9fd] underline underline-offset-2 hover:text-[#50fa7b] font-semibold transition cursor-pointer"
      >
        {label}
      </a>
    );
    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < raw.length) {
    parts.push(parseRawUrlsAndFormatting(raw.substring(lastIndex), `txt_end_${lastIndex}`));
  }

  return parts.length > 0 ? parts : parseRawUrlsAndFormatting(raw, 'txt_raw');
}

function parseRawUrlsAndFormatting(text: string, keyPrefix: string): React.ReactNode {
  // Autolink raw web URLs
  const rawUrlRegex = /(https?:\/\/[^\s<"'\)]+?)([\.,;]?(?=\s|$|<|'|"))/g;
  const parts: React.ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = rawUrlRegex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(parseBoldAndItalic(text.substring(lastIndex, match.index), `${keyPrefix}_b_${lastIndex}`));
    }
    const url = match[1];
    const trailing = match[2] || '';
    parts.push(
      <React.Fragment key={`${keyPrefix}_url_${match.index}`}>
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-[#8be9fd] underline underline-offset-2 hover:text-[#50fa7b] font-semibold transition cursor-pointer"
        >
          {url}
        </a>
        {trailing}
      </React.Fragment>
    );
    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < text.length) {
    parts.push(parseBoldAndItalic(text.substring(lastIndex), `${keyPrefix}_end_${lastIndex}`));
  }

  return parts.length > 0 ? parts : parseBoldAndItalic(text, keyPrefix);
}

function parseBoldAndItalic(text: string, keyPrefix: string): React.ReactNode {
  // Simple bold **text** parser
  const boldParts = text.split(/(\*\*[^*]+\*\*)/g);
  return boldParts.map((bPart, bIdx) => {
    if (bPart.startsWith('**') && bPart.endsWith('**') && bPart.length >= 4) {
      const boldText = bPart.slice(2, -2);
      return (
        <strong key={`${keyPrefix}_b_${bIdx}`} className="font-bold text-[#f8f8f2]">
          {boldText}
        </strong>
      );
    }
    // Italic *text*
    const italicParts = bPart.split(/(\*[^*]+\*)/g);
    return italicParts.map((iPart, iIdx) => {
      if (iPart.startsWith('*') && iPart.endsWith('*') && iPart.length >= 2) {
        return (
          <em key={`${keyPrefix}_i_${iIdx}`} className="italic text-[#f8f8f2]/90">
            {iPart.slice(1, -1)}
          </em>
        );
      }
      return iPart;
    });
  });
}
