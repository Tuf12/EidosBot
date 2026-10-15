import React, { useState } from 'react';
import { Copy, Check, Terminal, Volume2, Square, Loader2 } from 'lucide-react';

export interface ReadAloudControl {
  playbackPrefix?: string;
  playingMessageId?: string | null;
  playbackActive?: boolean;
  isBuffering?: boolean;
  onPlayAudio?: (text: string, messageId: string) => void;
  onStopAudio?: () => void;
}

interface MarkdownRendererProps extends ReadAloudControl {
  content: string;
}

interface CodeBlockProps extends ReadAloudControl {
  code: string;
  language?: string;
  blockIndex: number;
}

const MAX_CODE_SPEECH = 12000;

const CODE_SYMBOLS: [RegExp, string][] = [
  [/\/\//g, ' comment '],
  [/\/\*/g, ' comment '],
  [/\*\//g, ' comment '],
  [/\.\.\./g, ' ellipsis '],
  [/===/g, ' triple equal '],
  [/!==/g, ' not equal '],
  [/==/g, ' double equal '],
  [/!=/g, ' not equal '],
  [/<=/g, ' less or equal '],
  [/>=/g, ' greater or equal '],
  [/=>/g, ' arrow '],
  [/->/g, ' arrow '],
  [/\+\+/g, ' plus plus '],
  [/--/g, ' minus minus '],
  [/&&/g, ' and '],
  [/\|\|/g, ' or '],
  [/</g, ' less than '],
  [/>/g, ' greater than '],
  [/#/g, ' '],
  [/\*/g, ' star '],
  [/_/g, ' '],
  [new RegExp('~', 'g'), ' tilde '],
];

const FENCE_RE = /(?:^[ \t]*)?```([a-zA-Z0-9_\-+]*)[ \t]*\n?([\s\S]*?)(?:^[ \t]*)?```/g;
const UNCLOSED_FENCE_RE = /(?:^[ \t]*)?```([a-zA-Z0-9_\-+]*)[ \t]*\n?([\s\S]*)$/m;

/**
 * Wording as shown in the bubble: heading marks, emphasis, and link syntax removed.
 * Code fences are dropped and the code itself is kept. Links become "label (url)".
 */
export function markdownToPlainText(text: string): string {
  if (!text) return '';
  let source = text.replace(/\r\n/g, '\n');
  const blocks: string[] = [];
  FENCE_RE.lastIndex = 0;

  source = source.replace(FENCE_RE, (_full, _lang, code: string) => {
    blocks.push(dedentCode(String(code).replace(/\n$/, '')));
    return `\uE000CODE${blocks.length - 1}\uE001`;
  });

  const unclosed = UNCLOSED_FENCE_RE.exec(source);
  if (unclosed && unclosed.index !== undefined) {
    blocks.push(dedentCode(String(unclosed[2] || '').replace(/\n$/, '')));
    source = source.slice(0, unclosed.index) + `\uE000CODE${blocks.length - 1}\uE001`;
  }

  const plain = source.split('\n').map(plainMarkdownLine).join('\n');
  return blocks.reduce(
    (acc, code, index) => acc.split(`\uE000CODE${index}\uE001`).join(code),
    plain,
  );
}

function plainMarkdownLine(line: string): string {
  const heading = line.match(/^(#{1,6})[ \t]+(.*)$/);
  if (heading) return plainInline(heading[2].replace(/[ \t]+#+\s*$/, ''));

  const quote = line.match(/^> (.*)$/);
  if (quote) return plainInline(quote[1]);

  const bullet = line.match(/^(\s*)[-*]\s+(.*)$/);
  if (bullet) return `${bullet[1]}• ${plainInline(bullet[2])}`;

  const ordered = line.match(/^(\s*)(\d+)\.\s+(.*)$/);
  if (ordered) return `${ordered[1]}${ordered[2]}. ${plainInline(ordered[3])}`;

  return plainInline(line);
}

function plainInline(text: string): string {
  const codes: string[] = [];
  let body = text.replace(/`([^`\n]+)`/g, (_match, code: string) => {
    codes.push(code);
    return `\uE000IN${codes.length - 1}\uE001`;
  });
  body = body.replace(/\*\*([^*]+)\*\*/g, '$1');
  body = body.replace(/\*([^*\n]+)\*/g, '$1');
  body = body.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_match, label: string, url: string) => {
    const cleanLabel = label.trim();
    const cleanUrl = url.trim().replace(/^['"]|['"]$/g, '');
    if (!cleanLabel || cleanLabel === cleanUrl) return cleanUrl;
    return `${cleanLabel} (${cleanUrl})`;
  });
  return codes.reduce(
    (acc, code, index) => acc.split(`\uE000IN${index}\uE001`).join(code),
    body,
  );
}

/**
 * Robust clipboard copy supporting modern Clipboard API with fallback for non-secure contexts.
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  if (navigator?.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Fallback to execCommand below
    }
  }
  try {
    const textArea = document.createElement('textarea');
    textArea.value = text;
    textArea.style.position = 'fixed';
    textArea.style.top = '0';
    textArea.style.left = '0';
    textArea.style.width = '2em';
    textArea.style.height = '2em';
    textArea.style.padding = '0';
    textArea.style.border = 'none';
    textArea.style.outline = 'none';
    textArea.style.boxShadow = 'none';
    textArea.style.background = 'transparent';
    document.body.appendChild(textArea);
    textArea.focus();
    textArea.select();
    const successful = document.execCommand('copy');
    document.body.removeChild(textArea);
    return successful;
  } catch {
    return false;
  }
}

/**
 * Turn source into words the voice model can say. Message playback strips
 * fenced code, and it also deletes characters like #, *, and <...> as markdown.
 */
export function codeToSpeech(code: string, language?: string): string {
  const lines = code.split('\n').map(speakCodeLine).filter(Boolean);
  let body = lines.join('. ');
  if (body.length > MAX_CODE_SPEECH) {
    body = `${body.slice(0, MAX_CODE_SPEECH).replace(/\s+\S*$/, '').trim()}. Remaining lines omitted.`;
  }
  const lang = (language || '').trim().toLowerCase();
  const intro = lang ? `${lang} code.` : 'Code.';
  return `${intro} ${body}`.replace(/\s+/g, ' ').trim();
}

function speakCodeLine(raw: string): string {
  const line = raw.replace(/\t/g, '    ');
  const leadingComment = line.match(/^(\s*)(\/\/|#)(.*)$/);
  if (leadingComment) {
    const body = expandCodeSymbols(leadingComment[3]);
    return body ? `comment. ${body}` : 'comment';
  }
  return expandCodeSymbols(line);
}

function expandCodeSymbols(line: string): string {
  let text = line;
  for (const [pattern, spoken] of CODE_SYMBOLS) {
    pattern.lastIndex = 0;
    text = text.replace(pattern, spoken);
  }
  text = text
    .replace(/`/g, ' ')
    .replace(/=/g, ' equals ')
    .replace(/\+/g, ' plus ')
    .replace(/-/g, ' minus ')
    .replace(/\\/g, ' backslash ')
    .replace(/\./g, ' dot ')
    .replace(/@/g, ' at ')
    .replace(/\$/g, ' dollar ')
    .replace(/%/g, ' percent ')
    .replace(/\^/g, ' caret ')
    .replace(/&/g, ' ampersand ')
    .replace(/\|/g, ' bar ')
    .replace(/!/g, ' not ')
    .replace(/\?/g, ' question ')
    .replace(/[#*_~><]/g, ' ');
  return text.replace(/\s+/g, ' ').trim();
}

/**
 * Remove uniform leading indentation from multiline code blocks.
 */
function dedentCode(code: string): string {
  const lines = code.split('\n');
  let minIndent = Infinity;
  for (const line of lines) {
    if (!line.trim()) continue;
    const match = line.match(/^[ \t]*/);
    const indent = match ? match[0].length : 0;
    if (indent < minIndent) minIndent = indent;
  }
  if (minIndent === Infinity || minIndent === 0) return code;
  return lines.map((line) => (line.length >= minIndent ? line.slice(minIndent) : line)).join('\n');
}

/**
 * Syntax highlighter rendering formatted tokens for programming languages.
 */
function highlightSyntax(code: string): React.ReactNode {
  const lines = code.split('\n');
  const tokenRegex = /(\/\/[^\n]*|\/\*[\s\S]*?\*\/|#[^\n]*)|("[^"\\]*(?:\\.[^"\\]*)*"|'[^'\\]*(?:\\.[^'\\]*)*'|`[^`\\]*(?:\\.[^`\\]*)*`)|(\b\d+(?:\.\d+)?\b)|(\b(?:def|class|return|if|elif|else|for|while|try|except|finally|with|as|import|from|lambda|yield|pass|break|continue|raise|assert|True|False|None|async|await|module|function|difference|union|intersection|translate|rotate|scale|linear_extrude|rotate_extrude|const|let|var|new|export|echo|exit|then|fi|do|done|case|esac|sudo)\b)|(\b(?:cylinder|cube|sphere|circle|square|polygon|text|print|len|range|str|int|float|dict|list|set|open|console|document|window|Math|JSON)\b)/g;

  return lines.map((line, lineIdx) => {
    const tokens: React.ReactNode[] = [];
    let lastIndex = 0;
    let match: RegExpExecArray | null;
    tokenRegex.lastIndex = 0;

    while ((match = tokenRegex.exec(line)) !== null) {
      if (match.index > lastIndex) {
        tokens.push(line.substring(lastIndex, match.index));
      }
      const [full, comment, str, num, kw, builtin] = match;
      const key = `${lineIdx}_${match.index}`;

      if (comment) {
        tokens.push(<span key={key} className="text-[#6272a4] italic">{comment}</span>);
      } else if (str) {
        tokens.push(<span key={key} className="text-[#f1fa8c]">{str}</span>);
      } else if (num) {
        tokens.push(<span key={key} className="text-[#bd93f9]">{num}</span>);
      } else if (kw) {
        tokens.push(<span key={key} className="text-[#ff79c6] font-semibold">{kw}</span>);
      } else if (builtin) {
        tokens.push(<span key={key} className="text-[#8be9fd]">{builtin}</span>);
      } else {
        tokens.push(full);
      }
      lastIndex = match.index + full.length;
    }

    if (lastIndex < line.length) {
      tokens.push(line.substring(lastIndex));
    }

    return (
      <div key={lineIdx} className="table-row font-mono text-[11px] leading-relaxed">
        <span className="table-cell select-none pr-3 text-right text-[#6272a4]/50 font-mono text-[10px] tabular-nums select-none border-r border-[#44475a]/40">
          {lineIdx + 1}
        </span>
        <span className="table-cell select-text pl-3 whitespace-pre font-mono text-[11px] leading-relaxed text-[#f8f8f2]">
          {tokens.length > 0 ? tokens : ' '}
        </span>
      </div>
    );
  });
}

const CodeBlock: React.FC<CodeBlockProps> = ({
  code,
  language,
  blockIndex,
  playbackPrefix,
  playingMessageId = null,
  playbackActive = false,
  isBuffering = false,
  onPlayAudio,
  onStopAudio,
}) => {
  const [copied, setCopied] = useState(false);
  const cleanCode = dedentCode(code.replace(/\r\n/g, '\n').replace(/\n$/, ''));
  const lineCount = cleanCode.split('\n').length;
  const playbackId = playbackPrefix ? `${playbackPrefix}:code:${blockIndex}` : '';
  const isThisBlock = Boolean(playbackId) && playingMessageId === playbackId;
  const isThisBuffering = isThisBlock && isBuffering;
  const isThisPlaying = isThisBlock && playbackActive;
  const canRead = Boolean(onPlayAudio && playbackId && cleanCode.trim());

  const handleCopy = async () => {
    const success = await copyToClipboard(cleanCode);
    if (success) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const handleRead = () => {
    if (!onPlayAudio || !playbackId) return;
    if (isThisPlaying || isThisBuffering) {
      onStopAudio?.();
      return;
    }
    const spoken = codeToSpeech(cleanCode, (language || '').trim().toLowerCase());
    if (!spoken) return;
    onPlayAudio(spoken, playbackId);
  };

  const cleanLang = (language || '').trim().toLowerCase();
  const displayLang = cleanLang ? cleanLang.toUpperCase() : 'CODE';

  return (
    <div className="my-3 rounded-xl border border-[#44475a] bg-[#14151b] overflow-hidden shadow-lg">
      <div className="flex flex-wrap items-center justify-between gap-2 px-3.5 py-2 bg-[#181a24] border-b border-[#44475a]/70 text-[11px] text-[#6272a4] font-mono">
        <div className="flex items-center gap-2 font-bold tracking-wider min-w-0">
          <Terminal className="w-3.5 h-3.5 text-[#50fa7b] shrink-0" />
          <span className="text-[#bd93f9] font-mono truncate">{displayLang}</span>
          <span className="text-[#6272a4]/70 text-[10px] font-normal shrink-0">• {lineCount} line{lineCount > 1 ? 's' : ''}</span>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {canRead && (
            <button
              type="button"
              onClick={handleRead}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-semibold transition active:scale-95 ${
                isThisBuffering
                  ? 'bg-[#bd93f9]/20 text-[#bd93f9] border border-[#bd93f9]/50'
                  : isThisPlaying
                  ? 'bg-[#ff5555]/20 text-[#ff5555] border border-[#ff5555]/50'
                  : 'text-[#f8f8f2] hover:text-[#bd93f9] hover:bg-[#44475a]/40 bg-[#282a36] border border-[#44475a]/80'
              }`}
              title={
                isThisBuffering
                  ? 'Preparing to read this code aloud'
                  : isThisPlaying
                  ? 'Stop reading this code'
                  : 'Read this code aloud'
              }
            >
              {isThisBuffering ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : isThisPlaying ? (
                <Square className="w-3.5 h-3.5 fill-current" />
              ) : (
                <Volume2 className="w-3.5 h-3.5" />
              )}
              <span>{isThisPlaying || isThisBuffering ? 'Stop' : 'Read'}</span>
            </button>
          )}
          <button
            type="button"
            onClick={handleCopy}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-semibold transition active:scale-95 ${
              copied
                ? 'bg-[#50fa7b]/20 text-[#50fa7b] border border-[#50fa7b]/50'
                : 'text-[#f8f8f2] hover:text-[#50fa7b] hover:bg-[#44475a]/40 bg-[#282a36] border border-[#44475a]/80'
            }`}
            title="Copy code to clipboard"
          >
            {copied ? <Check className="w-3.5 h-3.5 text-[#50fa7b]" /> : <Copy className="w-3.5 h-3.5" />}
            <span>{copied ? 'Copied!' : 'Copy'}</span>
          </button>
        </div>
      </div>
      <div className="p-3 text-[11px] font-mono leading-relaxed overflow-x-auto select-text bg-[#14151b]">
        <div className="table w-full border-collapse">
          {highlightSyntax(cleanCode)}
        </div>
      </div>
    </div>
  );
};

export const MarkdownRenderer: React.FC<MarkdownRendererProps> = ({
  content,
  playbackPrefix,
  playingMessageId,
  playbackActive,
  isBuffering,
  onPlayAudio,
  onStopAudio,
}) => {
  if (!content) return null;

  const readAloud = {
    playbackPrefix,
    playingMessageId,
    playbackActive,
    isBuffering,
    onPlayAudio,
    onStopAudio,
  };

  // Split content by triple-backtick code blocks tolerating optional leading indentation
  const codeBlockRegex = /(?:^[ \t]*)?```([a-zA-Z0-9_\-\+]*)[ \t]*\n?([\s\S]*?)(?:^[ \t]*)?```/gm;
  const elements: React.ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  let blockIdx = 0;
  while ((match = codeBlockRegex.exec(content)) !== null) {
    // Text before the code block
    if (match.index > lastIndex) {
      const textBefore = content.substring(lastIndex, match.index);
      if (textBefore.trim()) {
        elements.push(
          <RenderRichText key={`text_${blockIdx}_${lastIndex}`} text={textBefore} />
        );
      }
    }

    // Code block itself
    const lang = match[1] || '';
    const code = match[2] || '';
    elements.push(
      <CodeBlock key={`code_${blockIdx}`} language={lang} code={code} blockIndex={blockIdx} {...readAloud} />
    );

    lastIndex = match.index + match[0].length;
    blockIdx++;
  }

  // Trailing text - support unclosed code blocks during active streaming
  if (lastIndex < content.length) {
    const trailingText = content.substring(lastIndex);
    const unclosedMatch = trailingText.match(/(?:^[ \t]*)?```([a-zA-Z0-9_\-\+]*)[ \t]*\n?([\s\S]*)$/m);
    if (unclosedMatch && unclosedMatch.index !== undefined) {
      const beforeUnclosed = trailingText.substring(0, unclosedMatch.index);
      if (beforeUnclosed.trim()) {
        elements.push(
          <RenderRichText key={`text_end_${lastIndex}`} text={beforeUnclosed} />
        );
      }
      elements.push(
        <CodeBlock
          key={`code_stream_${blockIdx}`}
          language={unclosedMatch[1] || ''}
          code={unclosedMatch[2] || ''}
          blockIndex={blockIdx}
          {...readAloud}
        />
      );
    } else {
      elements.push(
        <RenderRichText key={`text_end_${lastIndex}`} text={trailingText} />
      );
    }
  }

  return <div className="space-y-1.5 select-text">{elements}</div>;
};

const HEADING_TAG: Record<number, 'h1' | 'h2' | 'h3' | 'h4' | 'h5' | 'h6'> = {
  1: 'h1',
  2: 'h2',
  3: 'h3',
  4: 'h4',
  5: 'h5',
  6: 'h6',
};
const HEADING_CLASS: Record<number, string> = {
  1: 'text-sm font-extrabold text-[#bd93f9] mt-3 mb-1.5',
  2: 'text-sm font-bold text-[#8be9fd] mt-2.5 mb-1',
  3: 'text-xs font-bold text-[#bd93f9] mt-2 mb-1',
  4: 'text-xs font-semibold text-[#8be9fd] mt-2 mb-1',
  5: 'text-[11px] font-semibold text-[#8be9fd] mt-1.5 mb-0.5',
  6: 'text-[11px] font-medium text-[#8be9fd] mt-1.5 mb-0.5',
};

// Sub-component to parse line-by-line markdown
const RenderRichText: React.FC<{ text: string }> = ({ text }) => {
  const lines = text.split('\n');
  const renderedLines: React.ReactNode[] = [];

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    const heading = line.match(/^(#{1,6})[ \t]+(.*)$/);
    if (heading) {
      const level = heading[1].length;
      const title = heading[2].replace(/[ \t]+#+\s*$/, '');
      const HeadingTag = HEADING_TAG[level];
      renderedLines.push(
        <HeadingTag key={i} className={HEADING_CLASS[level]}>
          {renderInlineSpans(title)}
        </HeadingTag>
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
      await copyToClipboard(url);
    }
  } catch {
    await copyToClipboard(url);
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
