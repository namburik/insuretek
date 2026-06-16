import { useState, useCallback, useRef } from 'react';
import './Table.css';

// ── Status normalisation ─────────────────────────────────────────────────────

const STATUS_VARIANTS = {
  // Green
  success: 'green', approved: 'green', active: 'green',
  completed: 'green', verified: 'green', enabled: 'green', passed: 'green',
  // Amber
  pending: 'amber', awaiting: 'amber', 'in progress': 'amber',
  in_progress: 'amber', processing: 'amber', review: 'amber',
  submitted: 'amber', queued: 'amber', running: 'amber',
  // Red
  failed: 'red', error: 'red', inactive: 'red',
  rejected: 'red', cancelled: 'red', canceled: 'red', disabled: 'red',
};

function statusVariant(value) {
  if (typeof value !== 'string') return null;
  // normalise to lowercase, collapse whitespace/underscores/hyphens
  const key = value.trim().toLowerCase().replace(/[-_\s]+/g, ' ');
  return STATUS_VARIANTS[key] ?? STATUS_VARIANTS[key.replace(/ /g, '_')] ?? null;
}

// ── Status badge ─────────────────────────────────────────────────────────────

function StatusBadge({ value }) {
  const variant = statusVariant(value) ?? 'neutral';
  return (
    <span className={`dt-badge dt-badge--${variant}`}>
      {value}
    </span>
  );
}

// ── ID / hash cell ────────────────────────────────────────────────────────────

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const HASH_RE = /^[0-9a-f]{16,}$/i;

function looksLikeId(str) {
  return UUID_RE.test(str.trim()) || HASH_RE.test(str.trim()) || str.length > 24;
}

function truncateId(str) {
  if (str.length <= 16) return str;
  return `${str.slice(0, 8)}…${str.slice(-4)}`;
}

function copyToClipboard(text, onDone) {
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(text).then(onDone).catch(() => fallbackCopy(text, onDone));
  } else {
    fallbackCopy(text, onDone);
  }
}

function fallbackCopy(text, onDone) {
  const ta = Object.assign(document.createElement('textarea'), {
    value: text,
    style: 'position:fixed;opacity:0',
  });
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand('copy'); onDone(); } catch (_) {}
  document.body.removeChild(ta);
}

function IdCell({ value }) {
  const str = String(value);
  const [copied, setCopied] = useState(false);
  const timerRef = useRef(null);

  const handleCopy = useCallback(() => {
    copyToClipboard(str, () => {
      setCopied(true);
      clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => setCopied(false), 1600);
    });
  }, [str]);

  return (
    <button
      type="button"
      className={`dt-id-cell${copied ? ' dt-copied' : ''}`}
      title={copied ? 'Copied!' : str}
      aria-label={`Copy ${str}`}
      onClick={handleCopy}
    >
      <span className="dt-id-text">{truncateId(str)}</span>
      <span className="dt-copy-icon" aria-hidden="true">
        {copied ? '✓' : '⎘'}
      </span>
    </button>
  );
}

// ── Skeleton row ──────────────────────────────────────────────────────────────

function SkeletonRows({ columns, count = 4 }) {
  return Array.from({ length: count }, (_, i) => (
    <tr key={i} className="dt-row dt-skeleton-row">
      {columns.map((col) => (
        <td key={col.key} className="dt-td">
          <div
            className="dt-skeleton-cell"
            style={{ width: col.type === 'status' ? 64 : col.type === 'number' ? 48 : '80%' }}
          />
        </td>
      ))}
    </tr>
  ));
}

// ── Cell content dispatch ─────────────────────────────────────────────────────

function CellContent({ column, value }) {
  if (value === null || value === undefined || value === '') {
    return <span className="dt-empty">—</span>;
  }

  switch (column.type) {
    case 'status':
      return <StatusBadge value={String(value)} />;

    case 'id':
    case 'hash':
      return <IdCell value={value} />;

    case 'number':
    case 'currency': {
      const num = typeof value === 'number' ? value : parseFloat(value);
      if (Number.isNaN(num)) return <span>{String(value)}</span>;
      const formatted =
        column.type === 'currency'
          ? num.toLocaleString('en-US', { style: 'currency', currency: column.currency ?? 'USD' })
          : num.toLocaleString();
      return <span>{formatted}</span>;
    }

    default: {
      const str = String(value);
      // Auto-detect UUIDs/hashes in plain string columns
      if (column.autoId !== false && looksLikeId(str)) {
        return <IdCell value={str} />;
      }
      return <span>{str}</span>;
    }
  }
}

// ── Alignment helper ──────────────────────────────────────────────────────────

function colAlign(column) {
  if (column.align) return column.align;
  if (column.type === 'number' || column.type === 'currency') return 'right';
  if (column.type === 'status') return 'center';
  return 'left';
}

// ── DataTable ─────────────────────────────────────────────────────────────────

/**
 * DataTable — reusable AI-data table with light/dark theming.
 *
 * Column shape:
 *   { key, label, type, align, autoId, currency }
 *
 * Column types:
 *   'string'   — left-aligned plain text (default)
 *   'number'   — right-aligned, locale-formatted
 *   'currency' — right-aligned, $-formatted (currency prop for locale)
 *   'status'   — centered pill badge with auto colour-coding
 *   'id'|'hash'— monospace chip with truncation and click-to-copy
 *
 * Props:
 *   columns       Array<Column>   required
 *   rows          Array<object>   required
 *   theme         'light'|'dark'|'auto'  default 'auto'
 *   caption       string          optional table caption
 *   loading       boolean         shows skeleton rows
 *   skeletonRows  number          skeleton row count (default 4)
 *   emptyMessage  string          empty-state message
 *   onRowClick    (row) => void   makes rows clickable
 *   className     string          extra class on wrapper
 */
export function DataTable({
  columns = [],
  rows = [],
  theme = 'auto',
  caption,
  loading = false,
  skeletonRows = 4,
  emptyMessage = 'No data to display.',
  onRowClick,
  className = '',
}) {
  return (
    <div className={`dt-wrap ${className}`.trim()} data-theme={theme}>
      <div className="dt-scroll">
        <table className="dt-table" role="table">
          {caption && (
            <caption className="dt-caption">{caption}</caption>
          )}

          <thead>
            <tr className="dt-header-row">
              {columns.map((col) => (
                <th
                  key={col.key}
                  className="dt-th"
                  scope="col"
                  style={{ textAlign: colAlign(col) }}
                >
                  {col.label ?? col.key}
                </th>
              ))}
            </tr>
          </thead>

          <tbody>
            {loading ? (
              <SkeletonRows columns={columns} count={skeletonRows} />
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={columns.length} className="dt-empty-row">
                  {emptyMessage}
                </td>
              </tr>
            ) : (
              rows.map((row, rowIdx) => (
                <tr
                  key={row.id ?? row._id ?? row.key ?? rowIdx}
                  className={`dt-row${onRowClick ? ' dt-row--clickable' : ''}`}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                  aria-rowindex={rowIdx + 2}
                >
                  {columns.map((col) => (
                    <td
                      key={col.key}
                      className="dt-td"
                      style={{ textAlign: colAlign(col) }}
                    >
                      <CellContent column={col} value={row[col.key]} />
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
