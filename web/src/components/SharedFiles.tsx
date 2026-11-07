import { useEffect, useState } from 'react';
import { SharedFile } from '../types/gateway';
import { MarkdownRenderer, ReadAloudControl } from './MarkdownRenderer';
import { ChevronDown, ChevronRight, Download, Eye, FileText, Loader2, X } from 'lucide-react';

interface SharedFilesProps {
  files: SharedFile[];
  token: string;
  readAloud?: Omit<ReadAloudControl, 'playbackPrefix'>;
}

type PreviewKind = 'image' | 'pdf' | 'text' | 'html' | 'binary';

const TEXT_EXT = new Set([
  'md', 'txt', 'json', 'csv', 'py', 'ts', 'tsx', 'js', 'jsx', 'yaml', 'yml',
  'log', 'css', 'xml', 'toml', 'rst', 'sh',
]);

function isHtmlPreview(file: SharedFile): boolean {
  const ext = file.name.split('.').pop()?.toLowerCase() || '';
  const mime = (file.mime || '').toLowerCase();
  return ext === 'html' || ext === 'htm' || mime === 'text/html';
}

function fileUrl(token: string, id: string, download = false): string {
  const query = new URLSearchParams({ token });
  if (download) query.set('download', '1');
  return `/api/files/${encodeURIComponent(id)}?${query.toString()}`;
}

function formatSize(bytes: number): string {
  if (!bytes) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function previewKind(file: SharedFile): PreviewKind {
  const ext = file.name.split('.').pop()?.toLowerCase() || '';
  const mime = (file.mime || '').toLowerCase();
  if (isHtmlPreview(file)) return 'html';
  if (ext === 'svg' || mime.includes('svg')) return 'html';
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'].includes(ext) || mime.startsWith('image/')) return 'image';
  if (ext === 'pdf' || mime === 'application/pdf') return 'pdf';
  if (mime.startsWith('text/') || mime.includes('json') || TEXT_EXT.has(ext)) return 'text';
  return 'binary';
}

export function SharedFiles({ files, token, readAloud }: SharedFilesProps) {
  const [openFile, setOpenFile] = useState<SharedFile | null>(null);
  const [collapsed, setCollapsed] = useState(false);

  if (!files.length && !openFile) return null;

  return (
    <>
      {files.length > 0 && (
        <div className="mx-3 mt-2 rounded-xl border border-[#44475a] bg-[#282a36] overflow-hidden">
          <button
            type="button"
            onClick={() => setCollapsed((value) => !value)}
            aria-expanded={!collapsed}
            aria-controls="shared-files-list"
            title={collapsed ? 'Expand shared files' : 'Collapse shared files'}
            className={`w-full flex items-center justify-between gap-2 px-3 py-1.5 text-left hover:bg-[#44475a]/30 transition ${
              collapsed ? '' : 'border-b border-[#44475a]/60'
            }`}
          >
            <span className="text-[11px] font-bold text-[#bd93f9]">
              Shared files ({files.length})
            </span>
            {collapsed ? (
              <ChevronRight className="w-3.5 h-3.5 text-[#bd93f9] shrink-0" />
            ) : (
              <ChevronDown className="w-3.5 h-3.5 text-[#bd93f9] shrink-0" />
            )}
          </button>
          {!collapsed && (
            <div id="shared-files-list" className="max-h-36 overflow-y-auto">
              {files.map((file) => (
                <div
                  key={file.id}
                  className="flex items-center gap-2 px-3 py-2 border-b border-[#44475a]/30 last:border-b-0"
                >
                  <FileText className="w-3.5 h-3.5 text-[#8be9fd] shrink-0" />
                  <div className="min-w-0 flex-1">
                    <div className="text-xs text-[#f8f8f2] truncate">{file.name}</div>
                    <div className="text-[10px] text-[#6272a4] truncate">
                      {file.direction === 'user' ? 'You sent' : 'From Eidos'}
                      {' · '}
                      {formatSize(file.size)}
                      {file.missing ? ' · missing on disk' : ''}
                      {file.note ? ` · ${file.note}` : ''}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => setOpenFile(file)}
                    disabled={file.missing}
                    title="View"
                    className="p-1.5 rounded-lg text-[#50fa7b] hover:bg-[#50fa7b]/10 disabled:opacity-30 disabled:cursor-not-allowed"
                  >
                    <Eye className="w-3.5 h-3.5" />
                  </button>
                  <a
                    href={file.missing ? undefined : fileUrl(token, file.id, true)}
                    download={file.name}
                    title="Download"
                    className={`p-1.5 rounded-lg text-[#8be9fd] hover:bg-[#8be9fd]/10 ${file.missing ? 'pointer-events-none opacity-30' : ''}`}
                  >
                    <Download className="w-3.5 h-3.5" />
                  </a>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      {openFile && (
        <FileViewer
          file={openFile}
          token={token}
          readAloud={readAloud}
          onClose={() => setOpenFile(null)}
        />
      )}
    </>
  );
}

function FileViewer({
  file,
  token,
  readAloud,
  onClose,
}: {
  file: SharedFile;
  token: string;
  readAloud?: Omit<ReadAloudControl, 'playbackPrefix'>;
  onClose: () => void;
}) {
  const kind = previewKind(file);
  const [text, setText] = useState('');
  const [showHtmlSource, setShowHtmlSource] = useState(false);
  const [loading, setLoading] = useState(kind === 'text' || (kind === 'html' && showHtmlSource));
  const [error, setError] = useState<string | null>(file.missing ? 'This file is no longer on disk.' : null);
  const url = fileUrl(token, file.id, false);
  const isMarkdown = file.name.toLowerCase().endsWith('.md') || (file.mime || '').includes('markdown');
  const htmlPreview = kind === 'html';

  useEffect(() => {
    const needsText = kind === 'text' || (kind === 'html' && showHtmlSource);
    if (!needsText || file.missing) return;
    if (file.size > 1_500_000) {
      setLoading(false);
      setError('This file is too large to preview here. Download it instead.');
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetch(url)
      .then(async (res) => {
        if (!res.ok) throw new Error(`Could not open file (${res.status})`);
        return res.text();
      })
      .then((body) => {
        if (!cancelled) setText(body);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message || 'Could not open file');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [file.id, file.missing, file.size, kind, showHtmlSource, url]);

  useEffect(() => {
    setShowHtmlSource(false);
    setText('');
  }, [file.id]);

  return (
    <div className="fixed inset-0 z-50 bg-black/70 flex items-end sm:items-center justify-center p-3">
      <div
        className={`w-full max-h-[85dvh] bg-[#1e1f29] border border-[#44475a] rounded-2xl shadow-2xl flex flex-col overflow-hidden ${
          htmlPreview ? 'max-w-5xl' : 'max-w-2xl'
        }`}
      >
        <div className="flex items-center gap-2 px-3 py-2 border-b border-[#44475a]">
          <div className="min-w-0 flex-1">
            <div className="text-sm font-semibold text-[#f8f8f2] truncate">{file.name}</div>
            <div className="text-[10px] text-[#6272a4] truncate">
              {formatSize(file.size)}
              {file.path ? ` · ${file.path}` : ''}
            </div>
          </div>
          {htmlPreview && !file.missing && (
            <div className="flex rounded-lg border border-[#44475a] overflow-hidden shrink-0">
              <button
                type="button"
                onClick={() => setShowHtmlSource(false)}
                className={`px-2 py-1 text-[10px] font-semibold ${
                  !showHtmlSource ? 'bg-[#44475a] text-[#f8f8f2]' : 'text-[#6272a4] hover:text-[#f8f8f2]'
                }`}
              >
                Live
              </button>
              <button
                type="button"
                onClick={() => setShowHtmlSource(true)}
                className={`px-2 py-1 text-[10px] font-semibold ${
                  showHtmlSource ? 'bg-[#44475a] text-[#f8f8f2]' : 'text-[#6272a4] hover:text-[#f8f8f2]'
                }`}
              >
                Source
              </button>
            </div>
          )}
          {!file.missing && htmlPreview && (
            <a
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              className="px-2.5 py-1.5 rounded-lg text-[11px] font-semibold text-[#bd93f9] border border-[#44475a] hover:border-[#bd93f9] hidden sm:inline"
            >
              New tab
            </a>
          )}
          <a
            href={file.missing ? undefined : fileUrl(token, file.id, true)}
            download={file.name}
            className={`px-2.5 py-1.5 rounded-lg text-[11px] font-semibold text-[#8be9fd] border border-[#44475a] hover:border-[#8be9fd] ${file.missing ? 'pointer-events-none opacity-30' : ''}`}
          >
            Download
          </a>
          <button
            type="button"
            onClick={onClose}
            title="Close"
            className="p-1.5 rounded-lg text-[#6272a4] hover:text-[#f8f8f2] hover:bg-[#44475a]/40"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="flex-1 overflow-auto p-3 select-text">
          {loading && (
            <div className="flex items-center gap-2 text-xs text-[#6272a4] py-8 justify-center">
              <Loader2 className="w-4 h-4 animate-spin" />
              Opening…
            </div>
          )}
          {error && (
            <div className="text-xs text-[#ff5555] bg-[#ff5555]/10 border border-[#ff5555]/30 rounded-lg p-3">
              {error}
            </div>
          )}
          {!loading && !error && kind === 'image' && (
            <img src={url} alt={file.name} className="max-w-full mx-auto rounded-lg" />
          )}
          {!loading && !error && kind === 'pdf' && (
            <iframe title={file.name} src={url} className="w-full h-[70dvh] rounded-lg bg-white" />
          )}
          {!loading && !error && kind === 'html' && !showHtmlSource && (
            <iframe
              title={file.name}
              src={url}
              sandbox="allow-scripts allow-forms allow-popups allow-modals"
              className="w-full h-[70dvh] rounded-lg bg-white border-0"
            />
          )}
          {!loading && !error && kind === 'html' && showHtmlSource && (
            <pre className="text-[11px] leading-relaxed whitespace-pre-wrap break-words text-[#f8f8f2] font-mono">
              {text}
            </pre>
          )}
          {!loading && !error && kind === 'text' && isMarkdown && (
            <MarkdownRenderer
              content={text}
              playbackPrefix={`file:${file.id}`}
              {...readAloud}
            />
          )}
          {!loading && !error && kind === 'text' && !isMarkdown && (
            <pre className="text-[11px] leading-relaxed whitespace-pre-wrap break-words text-[#f8f8f2] font-mono">
              {text}
            </pre>
          )}
          {!loading && !error && kind === 'binary' && (
            <div className="text-xs text-[#f8f8f2] leading-relaxed">
              This file does not preview in the browser. Download it to open it on this device.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
