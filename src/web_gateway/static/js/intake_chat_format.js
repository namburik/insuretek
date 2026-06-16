/*
 * intake_chat_format.js
 * Pure formatting helpers — no DOM access.
 * Exposes a single global object `intakeChatFormat`.
 */

const intakeChatFormat = (() => {

  const STATUS_PHRASE = /\b(retrieved|successfully|fetched|loaded|found|completed|success|failed|error)\b/i;

  // ── Primitives ────────────────────────────────────────────────────────────

  function esc(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function decodeEntities(s) {
    return String(s)
      .replace(/&#(\d+);/g, (_, n) => String.fromCharCode(+n))
      .replace(/&amp;/g, '&')
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/&quot;/g, '"')
      .replace(/&apos;/g, "'");
  }

  function stripMd(s) {
    return String(s)
      .replace(/^\s*[-*]+\s+/gm, '')
      .replace(/\*\*(.+?)\*\*/g, '$1')
      .replace(/\*(.+?)\*/g, '$1')
      .replace(/`([^`]+)`/g, '$1')
      .trim();
  }

  function formatLabel(key) {
    return String(key)
      .replace(/^@\w+$/, (s) => s.slice(1))       // @type → type, @id → id
      .replace(/^[\w-]+:/, '')                      // strip namespace prefixes (isar-xxx:, insuretek:)
      .replace(/([a-z])([A-Z])/g, '$1 $2')          // camelCase → words
      .replace(/[_-]+/g, ' ')
      .replace(/\b([a-z])/g, (_, ch) => ch.toUpperCase())
      .replace(/\bId\b/gi, 'ID')
      .replace(/\bUuid\b/gi, 'UUID')
      .trim();
  }

  function isMonospaceText(key, value) {
    if (typeof value !== 'string') return false;
    const v = value.trim();
    const currency = /^(?:\$|€|£|¥|₹)\s?\d|[\d,]+(?:\.\d{2})?(?:\s?(?:USD|EUR|GBP|JPY|AUD|CAD|INR))?$/i;
    const id      = /^[A-Za-z0-9_-]{8,}$|^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
    const hint    = /(id|uuid|identifier|token|acct|account_number|routing_number)$/i.test(key);
    return currency.test(v) || id.test(v) || hint;
  }

  function isIdLike(key) {
    const k = String(key).trim().toLowerCase();
    return k === 'merchant_id' || k === 'applicant_id';
  }

  function truncateId(v) {
    return v.length > 14 ? v.slice(0, 8) + '...' + v.slice(-3) : v;
  }

  const COPY_ICON_SVG = '<svg class="copy-icon" xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';

  function flattenObject(obj) {
    return Object.values(obj).flatMap((v) =>
      v && typeof v === 'object' ? flattenObject(v) : (v ? [String(v)] : [])
    ).join(', ');
  }

  // ── Bubble markdown renderer ──────────────────────────────────────────────

  function looksLikeRawDataLine(line) {
    const t = line.trim();
    return (t.startsWith('{') || t.startsWith('[{') || t.startsWith('[')) &&
           (t.endsWith('}') || t.endsWith(']'));
  }

  function renderBubbleHtml(text) {
    const normalized = decodeEntities(String(text))
      .replace(/([^\n])\* /g, '$1\n* ')
      .replace(/([^\n])((?:Is|Are|Does|Do|Can|Could|Would|Should|Has|Have|Did|Was|Were)[^\n?]+\?)/g, '$1\n$2');

    const inline = (s) => s
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>');

    const lines = esc(normalized).split('\n');
    const parts = [];
    let inList = false;

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (looksLikeRawDataLine(line)) {
        if (inList) { parts.push('</ul>'); inList = false; }
        parts.push(`<pre class="raw-data-block">${line}</pre>`);
      } else if (/^\* /.test(line)) {
        if (!inList) { parts.push('<ul class="msg-list">'); inList = true; }
        parts.push(`<li>${inline(line.slice(2))}</li>`);
      } else {
        if (inList) { parts.push('</ul>'); inList = false; }
        if (line === '') {
          if (parts.length && parts[parts.length - 1] !== '<br>') parts.push('<br>');
        } else {
          parts.push(inline(line));
          if (i < lines.length - 1) parts.push('<br>');
        }
      }
    }
    if (inList) parts.push('</ul>');
    return parts.join('');
  }

  // ── Structured data parser ────────────────────────────────────────────────

  function parseStructuredResponse(text) {
    const lines = String(text).split(/\r?\n/);
    const root  = {};
    const stack = [{ indent: -1, obj: root }];

    for (const raw of lines) {
      const line = raw.replace(/\t/g, '  ').replace(/ /g, ' ').replace(/ /g, ' ').replace(/\s+$/u, '');
      if (!line.trim()) continue;

      const match = /^(\s*)([^:]+):\s*(.*)$/.exec(line);
      if (!match) continue;

      const indent = match[1].length;
      const key   = match[2].trim();
      const value = match[3];

      while (stack.length > 0 && stack[stack.length - 1].indent >= indent) stack.pop();
      const parent = stack[stack.length - 1].obj;

      if (value === '') {
        parent[key] = {};
        stack.push({ indent, obj: parent[key] });
      } else {
        parent[key] = value.trim();
      }
    }
    return unflattenDotKeys(root);
  }

  function unflattenDotKeys(obj) {
    const result = {};
    for (const [key, rawVal] of Object.entries(obj)) {
      const val = rawVal && typeof rawVal === 'object' ? unflattenDotKeys(rawVal) : rawVal;
      if (key.includes('.')) {
        const parts = key.split('.');
        let cur = result;
        for (let i = 0; i < parts.length - 1; i++) {
          if (!cur[parts[i]] || typeof cur[parts[i]] !== 'object') cur[parts[i]] = {};
          cur = cur[parts[i]];
        }
        cur[parts[parts.length - 1]] = val;
      } else {
        result[key] = val;
      }
    }
    return result;
  }

  function isStructuredText(text) {
    const lines   = String(text).split(/\r?\n/).filter((l) => l.trim());
    if (!lines.length) return false;
    const kvLines = lines.filter((l) => /^[^:]{1,60}:\s*.+/.test(l.trim()));
    return kvLines.length >= 3 && kvLines.length / lines.length >= 0.5;
  }

  // ── Structured card HTML builder ──────────────────────────────────────────

  const NAME_KEYS = ['account_name', 'merchant_name', 'business_name', 'name', 'label', 'account_label'];

  function deriveEntityName(data) {
    for (const key of NAME_KEYS) {
      const val = data[key];
      if (val && typeof val === 'string' && val.trim() && !STATUS_PHRASE.test(val)) return val.trim();
    }
    return null;
  }

  function deriveIntent(userQuery) {
    if (!userQuery) return null;
    const q = userQuery.toLowerCase();
    if (/workflow|request.?id/.test(q))      return 'Workflow';
    if (/payment/.test(q))                   return 'Payments';
    if (/platform/.test(q))                  return 'Platforms';
    if (/explain|summar/.test(q))            return 'Summary';
    if (/merchant/.test(q))                  return 'Merchant';
    return null;
  }

  function deriveTitle(data, userQuery) {
    const entityName = deriveEntityName(data);
    const intent     = deriveIntent(userQuery);

    if (intent === 'Workflow') {
      const wfId = data.workflow_id || data.applicant_id;
      if (wfId && typeof wfId === 'string') {
        const short = wfId.length > 14 ? wfId.slice(0, 8) + '…' : wfId;
        return entityName ? `Workflow — ${entityName}` : `Workflow ${short}`;
      }
      return entityName ? `Workflow — ${entityName}` : 'Workflow Details';
    }

    if (entityName && intent) return `${intent} — ${entityName}`;
    if (entityName)           return entityName;

    const keyStr = Object.keys(data).join(' ').toLowerCase();
    if (intent)                           return `${intent} Details`;
    if (/workflow/.test(keyStr))          return 'Workflow Details';
    if (/transaction/.test(keyStr))       return 'Transaction Details';
    if (/payment/.test(keyStr))           return 'Payment Details';
    if (/platform/.test(keyStr))          return 'Platform Details';
    if (/account/.test(keyStr))           return 'Account Details';
    if (/customer/.test(keyStr))          return 'Customer Details';
    if (/invoice/.test(keyStr))           return 'Invoice Details';
    if (/merchant/.test(keyStr))          return 'Merchant Details';

    if (userQuery) {
      const words   = userQuery.trim().split(/\s+/);
      const snippet = words.slice(0, 5).join(' ');
      return snippet.charAt(0).toUpperCase() + snippet.slice(1) + (words.length > 5 ? '…' : '');
    }
    return 'Details';
  }

  function buildEntryHtml(key, rawValue, depth) {
    depth = depth || 0;
    const cleanKey = stripMd(key);
    const label    = formatLabel(cleanKey);
    if (rawValue == null || typeof rawValue !== 'object') {
      const value = rawValue == null || rawValue === '' ? '—' : (stripMd(String(rawValue)) || '—');
      const mono   = isMonospaceText(cleanKey, value);
      const idLike = value !== '—' && isIdLike(cleanKey);
      let valueHtml;
      if (idLike) {
        const display = truncateId(value);
        valueHtml = `<span class="id-chip" data-full-id="${esc(value)}"><span class="id-text">${esc(display)}</span><button class="id-copy-btn" type="button" title="Copy">${COPY_ICON_SVG}</button></span>`;
      } else {
        valueHtml = esc(value);
      }
      return `<div class="structured-item${depth ? ' nested' : ''}"><dt>${esc(label)}</dt><dd class="${mono ? 'structured-value mono' : 'structured-value'}">${valueHtml}</dd></div>`;
    }
    const children = Object.entries(rawValue).map(([k, v]) => buildEntryHtml(k, v, depth + 1)).join('');
    return `<div class="structured-group"><dt>${esc(label)}</dt><dl class="structured-nested">${children}</dl></div>`;
  }

  // ── Approval-tasks stepper ────────────────────────────────────────────────

  const GATE_LABELS = {
    identity: 'Identity', bank_verification: 'Bank Verification',
    underwriting: 'Underwriting', fraud: 'Fraud Check',
    logistics: 'Logistics', compliance: 'Compliance',
  };

  function buildApprovalTasksHtml(value) {
    if (!value || typeof value !== 'object') return null;

    let tasks = [];
    const vals = Object.values(value);
    const allStatuses = vals.every(v => /^(COMPLETED|PENDING|FAILED)$/i.test(String(v)));

    if (Array.isArray(value)) {
      tasks = value
        .map(item => {
          if (!item || typeof item !== 'object') return null;
          const name = item.gate_check || item.provider || item.name || item.task || item.label;
          const status = item.status || item.state || item.result || 'PENDING';
          return name ? { name: String(name), status: String(status).toUpperCase(), essential: item.is_essential === true } : null;
        })
        .filter(Boolean);
    } else if (Array.isArray(value.gate_check)) {
      // columnar format: { gate_check: [...], status: [...], is_essential: [...] }
      const len = value.gate_check.length;
      tasks = Array.from({ length: len }, (_, i) => ({
        name: String(value.gate_check[i]),
        status: String(value.status?.[i] || 'PENDING').toUpperCase(),
        essential: value.is_essential?.[i] !== false,
      }));
    } else if (allStatuses) {
      tasks = Object.entries(value).map(([name, status]) => ({ name, status: String(status).toUpperCase() }));
    } else if (value.gate_check && value.status) {
      tasks = [{ name: value.gate_check, status: String(value.status).toUpperCase() }];
    }
    if (!tasks.length) return null;

    const stepsHtml = tasks.map(({ name, status }) => {
      const label   = GATE_LABELS[name] || formatLabel(name);
      const done    = status === 'COMPLETED';
      const failed  = status === 'FAILED';
      const mod     = done ? 'completed' : failed ? 'failed' : 'pending';
      const icon    = done ? '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>' : failed ? '✕' : '';
      const badge   = done ? 'Completed' : failed ? 'Failed' : 'Pending';
      return `<div class="task-step task-step--${mod}"><div class="task-step-icon">${icon}</div><div class="task-step-body"><span class="task-step-label">${esc(label)}</span><span class="task-step-badge">${badge}</span></div></div>`;
    }).join('');

    return `<section class="structured-section structured-section--custom"><h3>Approval Tasks</h3><div class="task-stepper">${stepsHtml}</div></section>`;
  }

  // ── Transaction-fees pricing table ────────────────────────────────────────

  function _parseFeesString(str) {
    return str.split(/\s*;\s*/).filter(Boolean).map((entry) => {
      const bpsM  = entry.match(/(\d+)\s*bps/i);
      const flatM = entry.match(/(\d+)\s*¢/);
      const type  = entry.replace(/\d+\s*bps/i, '').replace(/\d+\s*¢/, '').trim() || '—';
      return { type, bps: bpsM ? parseInt(bpsM[1]) : null, flat: flatM ? parseInt(flatM[1]) : null };
    });
  }

  function buildTransactionFeesHtml(value) {
    let rows = [];
    if (typeof value === 'string' && value.trim()) {
      rows = _parseFeesString(value);
    } else if (Array.isArray(value)) {
      rows = value.map(f => ({
        type: f.card_type || '—',
        bps:  f.interchange_markup_basis_points != null ? +f.interchange_markup_basis_points : f.rate_basis_points != null ? +f.rate_basis_points : null,
        flat: f.flat_fee_cents != null ? +f.flat_fee_cents : null,
      }));
    } else if (value && typeof value === 'object') {
      rows = [{
        type: value.card_type || '—',
        bps:  value.rate_basis_points != null ? +value.rate_basis_points : null,
        flat: value.flat_fee_cents != null ? +value.flat_fee_cents : null,
      }];
    }
    if (!rows.length) return null;

    const hasType = rows.some(r => r.type && r.type !== '—');
    const hasBps  = rows.some(r => r.bps  != null);
    const hasFlat = rows.some(r => r.flat != null);

    const fmtPct  = bps  => bps  != null ? `${(bps / 100).toFixed(2)}%` : '—';
    const fmtFlat = flat => flat != null ? `$${(flat / 100).toFixed(2)}` : '—';

    const header = [
      hasType && '<th>Type</th>',
      hasBps  && '<th>Rate</th>',
      hasFlat && '<th>Per Txn</th>',
    ].filter(Boolean).join('');

    const body = rows.map(r => {
      const cells = [
        hasType && `<td><span class="fee-type-badge">${esc(r.type)}</span></td>`,
        hasBps  && `<td class="fee-num">${fmtPct(r.bps)}</td>`,
        hasFlat && `<td class="fee-num">${fmtFlat(r.flat)}</td>`,
      ].filter(Boolean).join('');
      return `<tr>${cells}</tr>`;
    }).join('');

    return `<section class="structured-section structured-section--custom"><h3>Transaction Fees</h3><table class="fee-table"><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table></section>`;
  }

  function formatBadgeValue(val, type) {
    if (type === 'id') return val;
    const s = String(val);
    if (type === 'neutral') return formatLabel(s);
    // status badges: sentence-case (COMPLETED → Completed)
    return s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();
  }

  const KEY_APPROVAL_TASKS = /^(?:approval[_\s])?tasks?$/i;
  const KEY_TRANSACTION_FEES = /transaction[_\s]fees?/i;

  function tryParsePythonLiteral(val) {
    if (typeof val !== 'string') return null;
    const t = val.trim();
    if (!((t.startsWith('{') && t.endsWith('}')) || (t.startsWith('[') && t.endsWith(']')))) return null;
    try { return JSON.parse(t); } catch (_) {}
    try {
      const json = t
        .replace(/'/g, '"')
        .replace(/\bTrue\b/g, 'true')
        .replace(/\bFalse\b/g, 'false')
        .replace(/\bNone\b/g, 'null');
      return JSON.parse(json);
    } catch (_) {}
    return null;
  }

  function buildStructuredCardHtml(data, userQuery) {
    const entries = Object.entries(data || {});
    if (!entries.length) return null;

    const title  = deriveTitle(data, userQuery);
    const mid    = typeof data.merchant_id === 'string' && data.merchant_id;
    const midBadge = mid ? (mid.length > 12 ? mid.slice(0, 8) + '…' : mid) : null;
    const rawStatus = data.status || data.workflow_status || null;
    const statusType = rawStatus
      ? /complet|success|active/i.test(rawStatus) ? 'success'
        : /fail|error|reject/i.test(rawStatus)    ? 'error'
        : /pending|progress|queue/i.test(rawStatus)? 'warn'
        : 'neutral'
      : null;
    const badgeDefs = [
      data.merchant_type    && { val: data.merchant_type,    type: 'neutral' },
      data.merchant_industry && { val: data.merchant_industry, type: 'neutral' },
      rawStatus             && { val: rawStatus,             type: statusType },
      midBadge              && { val: midBadge,              type: 'id' },
    ].filter(Boolean);
    const seen = new Set();
    const uniqueBadges = badgeDefs.filter(b => seen.has(b.val) ? false : seen.add(b.val));

    const scalarRows    = [];
    const nestedGroups  = [];  // [ [key, value], … ]
    const customSections = [];

    for (const [key, rawValue] of entries) {
      if (STATUS_PHRASE.test(key)) continue;
      if (typeof rawValue === 'string' && STATUS_PHRASE.test(rawValue) && rawValue.split(/\s+/).length <= 6) continue;

      const value = tryParsePythonLiteral(rawValue) ?? rawValue;

      if (KEY_APPROVAL_TASKS.test(key)) {
        const html = buildApprovalTasksHtml(value);
        if (html) { customSections.push(html); continue; }
      }
      if (KEY_TRANSACTION_FEES.test(key)) {
        const html = buildTransactionFeesHtml(value);
        if (html) { customSections.push(html); continue; }
      }

      if (value != null && typeof value === 'object') {
        nestedGroups.push([key, value]);
      } else {
        scalarRows.push([key, value]);
      }
    }

    const sections = [];

    // Scalar fields → one overview section (label only when other sections exist alongside)
    if (scalarRows.length) {
      const scalarHtml = scalarRows.map(([k, v]) => `<div class="structured-row">${buildEntryHtml(k, v)}</div>`).join('');
      const hasOthers  = nestedGroups.length > 0 || customSections.length > 0;
      const heading    = hasOthers ? '<h3>Overview</h3>' : '';
      sections.push(`<section class="structured-section">${heading}<dl>${scalarHtml}</dl></section>`);
    }

    // Each nested object → its own labeled section card
    for (const [key, value] of nestedGroups) {
      const label   = formatLabel(stripMd(key));
      const entries = Object.entries(value);
      const nestedCustom = [];
      const remaining = [];
      for (const [k, v] of entries) {
        const parsed = tryParsePythonLiteral(v) ?? v;
        if (KEY_TRANSACTION_FEES.test(k)) {
          const html = buildTransactionFeesHtml(parsed);
          if (html) { nestedCustom.push(html); continue; }
        }
        if (KEY_APPROVAL_TASKS.test(k)) {
          const html = buildApprovalTasksHtml(parsed);
          if (html) { nestedCustom.push(html); continue; }
        }
        remaining.push([k, parsed]);
      }
      const childHtml = remaining
        .map(([k, v]) => `<div class="structured-row">${buildEntryHtml(k, v, 0)}</div>`)
        .join('');
      if (childHtml) {
        sections.push(`<section class="structured-section"><h3>${esc(label)}</h3><dl>${childHtml}</dl></section>`);
      }
      nestedCustom.forEach(html => sections.push(html));
    }

    // Custom sections (approval tasks, transaction fees) — always span full width
    sections.push(...customSections);

    if (!sections.length) return null;

    const isMulti    = sections.length > 1;
    const gridClass  = isMulti ? 'structured-grid structured-grid--dynamic' : 'structured-grid';
    const badgesHtml = uniqueBadges.map((b) => `<span data-badge="${b.type}">${esc(formatBadgeValue(b.val, b.type))}</span>`).join('');

    return `<div class="structured-header"><div><p class="structured-label">${esc(title)}</p><div class="structured-badges">${badgesHtml}</div></div></div><div class="${gridClass}">${sections.join('')}</div>`;
  }

  return {
    esc,
    stripMd,
    formatLabel,
    isMonospaceText,
    flattenObject,
    renderBubbleHtml,
    parseStructuredResponse,
    isStructuredText,
    buildStructuredCardHtml,
  };
})();
