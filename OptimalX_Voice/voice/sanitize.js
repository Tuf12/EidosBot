'use strict';

const ONES = [
  'zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine',
  'ten', 'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen',
  'seventeen', 'eighteen', 'nineteen',
];
const TENS = ['', '', 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety'];

function integerToWords(n) {
  if (!Number.isFinite(n) || n < 0) return '';
  n = Math.floor(n);
  if (n < 20) return ONES[n];
  if (n < 100) {
    const tens = Math.floor(n / 10);
    const rem = n % 10;
    return rem ? `${TENS[tens]}-${ONES[rem]}` : TENS[tens];
  }
  if (n < 1000) {
    const hundreds = Math.floor(n / 100);
    const rem = n % 100;
    const head = `${ONES[hundreds]} hundred`;
    return rem ? `${head} ${integerToWords(rem)}` : head;
  }
  if (n < 1_000_000) {
    const thousands = Math.floor(n / 1000);
    const rem = n % 1000;
    const head = `${integerToWords(thousands)} thousand`;
    return rem ? `${head} ${integerToWords(rem)}` : head;
  }
  if (n < 1_000_000_000) {
    const millions = Math.floor(n / 1_000_000);
    const rem = n % 1_000_000;
    const head = `${integerToWords(millions)} million`;
    return rem ? `${head} ${integerToWords(rem)}` : head;
  }
  return String(n);
}

function currencyToWords(raw) {
  const normalized = String(raw || '').replace(/,/g, '');
  const match = normalized.match(/^(\d+)(?:\.(\d{1,2}))?$/);
  if (!match) return null;

  const dollars = parseInt(match[1], 10);
  const centsRaw = match[2] || '';
  const cents = centsRaw ? parseInt(centsRaw.padEnd(2, '0').slice(0, 2), 10) : 0;

  const parts = [];
  if (dollars > 0) {
    parts.push(`${integerToWords(dollars)} dollar${dollars === 1 ? '' : 's'}`);
  }
  if (cents > 0) {
    parts.push(`${integerToWords(cents)} cent${cents === 1 ? '' : 's'}`);
  }
  if (parts.length === 0) return 'zero dollars';
  if (parts.length === 2) return `${parts[0]} and ${parts[1]}`;
  return parts[0];
}

/**
 * Strip markdown / tooling noise so TTS reads clean prose.
 */
function sanitizeForTts(text) {
  let s = String(text || '');
  if (!s.trim()) return '';

  // Fenced code blocks → short placeholder
  s = s.replace(/```[\s\S]*?```/g, ' ');
  // Inline code
  s = s.replace(/`([^`]+)`/g, '$1');
  // Images / links → label text
  s = s.replace(/!\[([^\]]*)\]\([^)]+\)/g, '$1');
  s = s.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1');
  // Headings / emphasis / strikethrough
  s = s.replace(/^#{1,6}\s+/gm, '');
  s = s.replace(/(\*\*|__)(.*?)\1/g, '$2');
  s = s.replace(/(\*|_)(.*?)\1/g, '$2');
  s = s.replace(/~~(.*?)~~/g, '$1');
  // HTML tags
  s = s.replace(/<[^>]+>/g, ' ');
  // optimalx:// URIs → bare label already handled via links; strip leftovers
  s = s.replace(/optimalx:\/\/[^\s)]+/gi, ' ');
  // Bullet markers
  s = s.replace(/^\s*[-*+]\s+/gm, '');
  s = s.replace(/^\s*\d+\.\s+/gm, '');
  // Leftover URLs
  s = s.replace(/https?:\/\/\S+/gi, ' ');
  // File / path fragments (POSIX and Windows)
  s = s.replace(/(?:\b[A-Za-z]:)?(?:\/|\\)[\w.\-~%$@#:]+(?:\/|\\[\w.\-~%$@#:]+)*/g, ' ');
  // snake_case identifiers → word breaks
  s = s.replace(/_/g, ' ');
  // Currency → spoken dollars/cents before stripping $
  s = s.replace(/\$(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)/g, (_match, amount) => {
    const spoken = currencyToWords(amount);
    return spoken ? ` ${spoken} ` : _match;
  });
  // Symbols TTS should not vocalize (keep letters, digits, basic prose punctuation)
  s = s.replace(/[$#@%^&*\\/`~+=<>|{}\[\]();]/g, ' ');
  // Decorative quotes (keep apostrophes in contractions)
  s = s.replace(/[""„«»"]/g, ' ');
  // Collapse whitespace
  s = s.replace(/\s+/g, ' ').trim();
  return s;
}

module.exports = { sanitizeForTts };
