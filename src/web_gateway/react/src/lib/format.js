const STATUS_PHRASE = /\b(retrieved|successfully|fetched|loaded|found|completed|success|failed|error)\b/i;
const KEY_APPROVAL_TASKS = /^(?:approval[_\s])?tasks?$/i;
const KEY_TRANSACTION_FEES = /transaction[_\s]fees?/i;
const TASK_STATUS_RE = /^(COMPLETED|PENDING|FAILED)$/i;

export function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

export function stripMd(s) {
  return String(s)
    .replace(/^\s*[-*]+\s+/gm, '')
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/\*(.+?)\*/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .trim();
}

export function formatLabel(key) {
  return String(key)
    .replace(/^@\w+$/, (s) => s.slice(1))
    .replace(/^[\w-]+:/, '')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/[_-]+/g, ' ')
    .replace(/\b([a-z])/g, (_, ch) => ch.toUpperCase())
    .replace(/\bId\b/gi, 'ID')
    .replace(/\bUuid\b/gi, 'UUID')
    .trim();
}

export function isMonospaceText(key, value) {
  if (typeof value !== 'string') return false;
  const v = value.trim();
  const currency = /^(?:\$|€|£|¥|₹)\s?\d|[\d,]+(?:\.\d{2})?(?:\s?(?:USD|EUR|GBP|JPY|AUD|CAD|INR))?$/i;
  const id = /^[A-Za-z0-9_-]{8,}$|^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const hint = /(id|uuid|identifier|token|acct|account_number|routing_number)$/i.test(key);
  return currency.test(v) || id.test(v) || hint;
}

export function isIdLike(key) {
  const k = String(key).trim().toLowerCase();
  return k === '_id' || k === 'applicant_id';
}

export function truncateId(v) {
  return v.length > 14 ? v.slice(0, 8) + '...' + v.slice(-3) : v;
}

export function flattenObject(obj) {
  return Object.values(obj).flatMap((v) =>
    v && typeof v === 'object' ? flattenObject(v) : (v ? [String(v)] : [])
  ).join(', ');
}

export function looksLikeRawDataLine(line) {
  const t = line.trim();
  return (t.startsWith('{') || t.startsWith('[{') || t.startsWith('[')) &&
         (t.endsWith('}') || t.endsWith(']'));
}

export function renderBubbleHtml(text) {
  const normalized = String(text)
    .replace(/([^\n\s])\* /g, '$1\n* ')
    .replace(/([^\n])((?:Is|Are|Does|Do|Can|Could|Would|Should|Has|Have|Did|Was|Were)[^\n?]+\?)/g, '$1\n$2');

  const inline = (s) => s
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>');

  const lines = esc(normalized).split('\n');
  const parts = [];
  let inList = false;
  let inSubList = false;

  const closeSubList = () => { if (inSubList) { parts.push('</ul>'); inSubList = false; } };
  const closeList = () => { closeSubList(); if (inList) { parts.push('</ul>'); inList = false; } };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    if (looksLikeRawDataLine(line)) {
      closeList();
      parts.push(`<pre class="raw-data-block">${line}</pre>`);
    } else if (/^ {2,}\* /.test(line)) {
      if (!inList) { parts.push('<ul class="msg-list">'); inList = true; }
      if (!inSubList) { parts.push('<ul class="msg-sublist">'); inSubList = true; }
      parts.push(`<li>${inline(line.replace(/^ +\* /, ''))}</li>`);
    } else if (/^\* /.test(line)) {
      closeSubList();
      if (!inList) { parts.push('<ul class="msg-list">'); inList = true; }
      parts.push(`<li>${inline(line.slice(2))}</li>`);
    } else {
      closeList();
      if (line === '') {
        if (parts.length && parts[parts.length - 1] !== '<br>') parts.push('<br>');
      } else {
        parts.push(inline(line));
        if (i < lines.length - 1) parts.push('<br>');
      }
    }
  }
  closeList();
  return parts.join('');
}

export function parseStructuredResponse(text) {
  const lines = String(text).split(/\r?\n/);
  const root = {};
  const stack = [{ indent: -1, obj: root }];
  let lastKey = null;
  let lastParent = root;

  for (const raw of lines) {
    const line = raw.replace(/\t/g, '  ').replace(/ | /gu, ' ').replace(/\s+$/u, '');
    if (!line.trim()) continue;
    const match = /^(\s*)([^:]+):\s*(.*)$/.exec(line);
    if (!match) {
      // Bullet item without a colon — collect under the last open key as an array
      const bulletMatch = /^(\s*)[-*]\s+(.+)$/.exec(line);
      if (bulletMatch && lastKey !== null) {
        const item = bulletMatch[2].replace(/\s*\{[^}]*\}\s*$/, '').trim();
        if (item) {
          const existing = lastParent[lastKey];
          if (Array.isArray(existing)) existing.push(item);
          else lastParent[lastKey] = existing ? [existing, item] : [item];
        }
      }
      continue;
    }
    const indent = match[1].length;
    const key = match[2].trim();
    // Strip trailing Python dict/None config blobs e.g. "tokenization {'acquirer_company_id': None}"
    const value = match[3].replace(/\s*\{[^}]*\}\s*$/, '').trim();

    while (stack.length > 0 && stack[stack.length - 1].indent >= indent) stack.pop();
    const parent = stack[stack.length - 1].obj;

    if (value === '') {
      parent[key] = {};
      stack.push({ indent, obj: parent[key] });
      lastKey = key;
      lastParent = parent;
    } else {
      const existing = parent[key];
      if (existing !== undefined) {
        // Repeated key (e.g. multiple capability: lines) → collect as array
        if (Array.isArray(existing)) existing.push(value);
        else parent[key] = [existing, value];
      } else {
        parent[key] = value;
      }
      lastKey = key;
      lastParent = parent;
    }
  }

  return root;
}

// Wrapper keys emitted by the model around tool results — stripped before ratio check
const WRAPPER_HEADING_RE = /^(output|result|answer|id|text|type|message|data)\s*:/i;

export function isStructuredText(text) {
  const lines = String(text).split(/\r?\n/).filter((l) => l.trim());
  if (!lines.length) return false;
  // Strip known model wrapper headings before computing density
  const meaningful = lines.filter((l) => !WRAPPER_HEADING_RE.test(l.trim()));
  const base = meaningful.length || lines.length;
  const kvLines = meaningful.filter((l) => /^[^:]{1,60}:\s*.+/.test(l.trim()));
  // Flat key: value format (dense enough)
  if (kvLines.length >= 3 && kvLines.length / base >= 0.5) return true;
  // Model-wrapped output: wrapper heading present + at least 2 real kv lines
  const hasWrapperHeadings = lines.some((l) => WRAPPER_HEADING_RE.test(l.trim()));
  if (hasWrapperHeadings && kvLines.length >= 2) return true;
  // Indented block format: section headers (key:) + indented key: value lines
  const rawLines = String(text).split(/\r?\n/);
  const hasIndentedKv = rawLines.some((l) => /^ {2,}[^:]+:\s*.+/.test(l));
  const hasSectionHeader = lines.some((l) => /^[^:]{1,60}:\s*$/.test(l));
  return hasIndentedKv && hasSectionHeader && kvLines.length >= 2;
}

function buildEntryHtml(key, rawValue, depth = 0) {
  const cleanKey = stripMd(key);
  const label = formatLabel(cleanKey);

  if (Array.isArray(rawValue)) {
    if (rawValue.some((v) => v && typeof v === 'object')) {
      const children = rawValue
        .filter((v) => v && typeof v === 'object')
        .map((v) => {
          const inner = Object.entries(v)
            .filter(([, val]) => hasNonBlankValue(val))
            .map(([k, val]) => buildEntryHtml(k, val, depth + 1))
            .filter(Boolean)
            .join('');
          return inner ? `<div class="structured-subitem">${inner}</div>` : '';
        })
        .filter(Boolean)
        .join('');
      if (!children) return '';
      return `<div class="structured-group"><dt>${esc(label)}</dt><dl class="structured-nested">${children}</dl></div>`;
    }
    const items = rawValue.map((v) => stripMd(String(v)).trim()).filter(Boolean);
    if (!items.length) return '';
    const valueHtml = items.map((v) => `<span class="tag-pill">${esc(v)}</span>`).join('');
    return `<div class="structured-item${depth ? ' nested' : ''}"><dt>${esc(label)}</dt><dd class="structured-value tag-list">${valueHtml}</dd></div>`;
  }

  if (rawValue == null || typeof rawValue !== 'object') {
    if (rawValue == null || rawValue === '') return '';
    const value = stripMd(String(rawValue)).trim();
    if (!value || EMPTY_LITERAL.test(value)) return '';
    const mono = isMonospaceText(cleanKey, value);
    const idLike = isIdLike(cleanKey);
    let valueHtml;
    if (idLike) {
      const display = truncateId(value);
      valueHtml = `<span class="id-chip" data-full-id="${esc(value)}"><span class="id-text">${esc(display)}</span><button class="id-copy-btn" type="button" title="Copy">${COPY_ICON_SVG}</button></span>`;
    } else {
      valueHtml = esc(value);
    }
    return `<div class="structured-item${depth ? ' nested' : ''}"><dt>${esc(label)}</dt><dd class="${mono ? 'structured-value mono' : 'structured-value'}">${valueHtml}</dd></div>`;
  }

  const children = Object.entries(rawValue)
    .filter(([, v]) => hasNonBlankValue(v))
    .map(([k, v]) => buildEntryHtml(k, v, depth + 1))
    .filter(Boolean)
    .join('');
  if (!children) return '';
  return `<div class="structured-group"><dt>${esc(label)}</dt><dl class="structured-nested">${children}</dl></div>`;
}

const COPY_ICON_SVG = '<svg class="copy-icon" xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
const CHECK_ICON_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';

const EMPTY_LITERAL = /^\s*(\{\s*\}|\[\s*\])\s*$/;

function hasNonBlankValue(value) {
  if (value == null || value === '') return false;
  if (typeof value === 'object') return Object.values(value).some(hasNonBlankValue);
  const s = String(value).trim();
  return s !== '' && !EMPTY_LITERAL.test(s);
}

function countNonBlankLeaves(value) {
  if (value == null || value === '') return 0;
  if (typeof value === 'object') return Object.values(value).reduce((s, v) => s + countNonBlankLeaves(v), 0);
  return String(value).trim() !== '' ? 1 : 0;
}

function buildSubcell(k, v) {
  const subLabel = formatLabel(stripMd(k));
  const cellHtml = Object.entries(v)
    .map(([ck, cv]) => buildEntryHtml(ck, cv, 0))
    .filter(Boolean)
    .join('');
  if (!cellHtml) return '';
  return `<div class="structured-subcell"><h4>${esc(subLabel)}</h4><dl>${cellHtml}</dl></div>`;
}

function buildApprovalTasksHtml(value) {
  if (!value || typeof value !== 'object') return null;
  let tasks = [];

  if (Array.isArray(value)) {
    // Array of task objects: [{gate_check, provider, status, is_essential, task_id}, ...]
    tasks = value
      .filter((t) => t && typeof t === 'object')
      .map((t) => {
        const name = t.gate_check || t.provider || t.label || t.task || t.name;
        const status = t.status || t.state || t.result || 'PENDING';
        return name ? {
          name: String(name),
          status: String(status).toUpperCase(),
          essential: t.is_essential !== false,
        } : null;
      })
      .filter(Boolean);
  } else {
    // columnar format: keys may be snake_case OR formatted ("Gate Check", "Is Essential", ...)
    const findKey = (re) => Object.keys(value).find((k) => re.test(k));
    const gateKey = findKey(/^gate[_\s]check$/i);
    if (gateKey && Array.isArray(value[gateKey])) {
      const statusKey = findKey(/^status$/i);
      const essentialKey = findKey(/^is[_\s]essential$/i);
      tasks = value[gateKey].map((name, i) => ({
        name: String(name),
        status: String(statusKey && Array.isArray(value[statusKey]) ? value[statusKey][i] : 'PENDING').toUpperCase(),
        essential: !(essentialKey && Array.isArray(value[essentialKey]) && (value[essentialKey][i] === false || value[essentialKey][i] === 'false')),
      }));
    }
  }
  if (!tasks.length) {
    const vals = Object.values(value);
    const allStatuses = vals.every((v) => TASK_STATUS_RE.test(String(v)));
    if (allStatuses) {
      tasks = Object.entries(value).map(([name, status]) => ({ name, status: String(status).toUpperCase(), essential: true }));
    } else if (value.gate_check && value.status) {
      tasks = [{ name: value.gate_check, status: String(value.status).toUpperCase(), essential: value.is_essential !== false }];
    }
  }

  if (!tasks.length) return null;

  const stepsHtml = tasks.map(({ name, status, essential }) => {
    const label = formatLabel(name);
    const done = status === 'COMPLETED';
    const failed = status === 'FAILED';
    const mod = done ? 'completed' : failed ? 'failed' : 'pending';
    const icon = done ? CHECK_ICON_SVG : failed ? '✕' : '';
    const badge = done ? 'Completed' : failed ? 'Failed' : 'Pending';
    const essentialTag = essential ? '' : '<span class="task-step-optional">optional</span>';
    return `<div class="task-step task-step--${mod}"><div class="task-step-icon">${icon}</div><div class="task-step-body"><span class="task-step-label">${esc(label)}</span>${essentialTag}<span class="task-step-badge">${badge}</span></div></div>`;
  }).join('');

  return `<section class="structured-section structured-section--custom"><h3>Approval Tasks</h3><div class="task-stepper">${stepsHtml}</div></section>`;
}

function _parseFeesString(str) {
  return str.split(/\s*;\s*/).filter(Boolean).map((entry) => {
    const bpsM = entry.match(/(\d+)\s*bps/i);
    const flatM = entry.match(/(\d+)\s*¢/);
    const type = entry.replace(/\d+\s*bps/i, '').replace(/\d+\s*¢/, '').trim() || '—';
    return { type, bps: bpsM ? parseInt(bpsM[1], 10) : null, flat: flatM ? parseInt(flatM[1], 10) : null };
  });
}

function buildTransactionFeesHtml(value) {
  let rows = [];
  if (typeof value === 'string' && value.trim()) {
    rows = _parseFeesString(value);
  } else if (Array.isArray(value)) {
    rows = value.map((f) => ({
      type: f.card_type || '—',
      bps: f.interchange_markup_basis_points ?? f.rate_basis_points ?? null,
      flat: f.flat_fee_cents ?? null,
    }));
  }
  if (!rows.length) return null;

  const hasType = rows.some((r) => r.type && r.type !== '—');
  const hasBps = rows.some((r) => r.bps != null);
  const hasFlat = rows.some((r) => r.flat != null);

  const fmtPct = (bps) => (bps != null ? `${(bps / 100).toFixed(2)}%` : '—');
  const fmtFlat = (flat) => (flat != null ? `$${(flat / 100).toFixed(2)}` : '—');

  const header = [
    hasType && '<th>Type</th>',
    hasBps && '<th>Rate</th>',
    hasFlat && '<th>Per Txn</th>',
  ].filter(Boolean).join('');

  const body = rows.map((r) => {
    const cells = [
      hasType && `<td><span class="fee-type-badge">${esc(r.type)}</span></td>`,
      hasBps && `<td class="fee-num">${fmtPct(r.bps)}</td>`,
      hasFlat && `<td class="fee-num">${fmtFlat(r.flat)}</td>`,
    ].filter(Boolean).join('');
    return `<tr>${cells}</tr>`;
  }).join('');

  return `<section class="structured-section structured-section--custom"><h3>Transaction Fees</h3><table class="fee-table"><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table></section>`;
}

const HOIST_KEY_RE = /^(output|result|answer|response)$/i;
const SKIP_KEY_RE = /^(id|text|type|message|count|total|total_count|num_results|page|limit|offset)$/i;
const RESULT_KEY_RE = /^(result|item|entry|match|applicant|record)\s*\d*$/i;
const ENTITY_NAME_KEYS = ['applicant_name', 'account_name', 'task_id'];

function normalizeData(data) {
  const raw = Object.entries(data || {});
  // If there is a single wrapper key (output/result) whose value is an object, hoist it
  const hoistEntry = raw.find(([k, v]) => HOIST_KEY_RE.test(k) && v && typeof v === 'object' && !Array.isArray(v));
  if (hoistEntry) {
    const rest = raw.filter(([k]) => !HOIST_KEY_RE.test(k) && !SKIP_KEY_RE.test(k));
    return { ...Object.fromEntries(rest), ...hoistEntry[1] };
  }
  // Otherwise just drop skip-only metadata scalars that have no domain value
  const meaningful = raw.filter(([k, v]) => {
    if (!SKIP_KEY_RE.test(k)) return true;
    // Keep if it looks like a business value (long string, object, array)
    if (Array.isArray(v) || (v && typeof v === 'object')) return true;
    if (typeof v === 'string' && v.length > 40) return true;
    return false;
  });
  return Object.fromEntries(meaningful);
}

export function buildStructuredCardData(data, userQuery) {
  const entries = Object.entries(normalizeData(data));
  if (!entries.length) return null;

  const title = deriveTitle(data, userQuery);

  const scalarRows = [];
  const nestedGroups = [];
  const customSections = [];

  for (const [key, value] of entries) {
    if (STATUS_PHRASE.test(key)) continue;
    if (typeof value === 'string' && STATUS_PHRASE.test(value) && value.split(/\s+/).length <= 6) continue;

    if (KEY_APPROVAL_TASKS.test(key)) {
      const html = buildApprovalTasksHtml(value);
      if (html) { customSections.push({ html, count: countNonBlankLeaves(value), hasNested: false }); continue; }
    }
    if (KEY_TRANSACTION_FEES.test(key)) {
      const html = buildTransactionFeesHtml(value);
      if (html) { customSections.push({ html, count: countNonBlankLeaves(value), hasNested: false }); continue; }
    }

    if (value != null && typeof value === 'object') {
      const html = buildApprovalTasksHtml(value);
      if (html) { customSections.push({ html, count: countNonBlankLeaves(value), hasNested: false }); continue; }
      if (Array.isArray(value)) {
        scalarRows.push([key, value]);  // rendered as tag-pill list by buildEntryHtml
      } else {
        nestedGroups.push([key, value]);
      }
    } else {
      scalarRows.push([key, value]);
    }
  }

  let rootSection = null;
  if (scalarRows.length) {
    const scalarHtml = scalarRows.map(([k, v]) => buildEntryHtml(k, v)).filter(Boolean).join('');
    if (scalarHtml) {
      const heading = (nestedGroups.length + customSections.length) >= 2 ? '<h3>Overview</h3>' : '';
      rootSection = `<section class="structured-section">${heading}<dl>${scalarHtml}</dl></section>`;
    }
  }

  const subSections = [];
  for (const [key, value] of nestedGroups) {
    let label = formatLabel(stripMd(key));
    // For generic "Result N" / "Item N" keys, replace with the entity name from the value if available
    if (RESULT_KEY_RE.test(key.trim()) && value && typeof value === 'object') {
      const entityName = ENTITY_NAME_KEYS.map((k) => value[k]).find((v) => v && typeof v === 'string' && v.trim());
      if (entityName) label = entityName.trim();
    }
    const nestedCustom = [];
    const innerScalars = [];
    const innerNested = [];

    for (const [k, v] of Object.entries(value)) {
      if (KEY_TRANSACTION_FEES.test(k)) {
        const html = buildTransactionFeesHtml(v);
        if (html) { nestedCustom.push({ html, count: countNonBlankLeaves(v), hasNested: false }); continue; }
      }
      if (KEY_APPROVAL_TASKS.test(k)) {
        const html = buildApprovalTasksHtml(v);
        if (html) { nestedCustom.push({ html, count: countNonBlankLeaves(v), hasNested: false }); continue; }
      }
      if (v != null && typeof v === 'object') {
        const html = buildApprovalTasksHtml(v);
        if (html) { nestedCustom.push({ html, count: countNonBlankLeaves(v), hasNested: false }); continue; }
        if (hasNonBlankValue(v)) innerNested.push([k, v]);
      } else {
        innerScalars.push([k, v]);
      }
    }

    const parts = [];
    if (innerScalars.length) {
      const scalarsHtml = innerScalars.map(([k, v]) => buildEntryHtml(k, v, 0)).filter(Boolean).join('');
      if (scalarsHtml) parts.push(`<dl>${scalarsHtml}</dl>`);
    }
    if (innerNested.length) {
      const counts = innerNested.map(([, v]) => countNonBlankLeaves(v));
      const total = counts.reduce((s, n) => s + n, 0);
      const maxIdx = counts.indexOf(Math.max(...counts));
      const dominant = innerNested.length > 1 && total > 0 && counts[maxIdx] / total > 0.5;

      if (dominant) {
        const dominantCell = buildSubcell(...innerNested[maxIdx]);
        const others = innerNested.filter((_, i) => i !== maxIdx);
        const otherCells = others.map((entry) => buildSubcell(...entry)).filter(Boolean);
        if (!dominantCell && !otherCells.length) {
          // nothing to render
        } else if (!dominantCell || otherCells.length === 0) {
          const cells = (dominantCell ? [dominantCell] : otherCells).join('');
          parts.push(`<div class="structured-inner-subgrid">${cells}</div>`);
        } else {
          const restHtml = otherCells.length > 1
            ? `<div class="structured-subgrid-rest">${otherCells.join('')}</div>`
            : otherCells[0];
          parts.push(`<div class="structured-inner-subgrid--dominant">${dominantCell}${restHtml}</div>`);
        }
      } else {
        const cells = innerNested.map((entry) => buildSubcell(...entry)).filter(Boolean).join('');
        if (cells) parts.push(`<div class="structured-inner-subgrid">${cells}</div>`);
      }
    }

    if (parts.length) {
      const count = innerScalars.reduce((s, [, v]) => s + countNonBlankLeaves(v), 0)
                  + innerNested.reduce((s, [, v]) => s + countNonBlankLeaves(v), 0);
      subSections.push({
        html: `<section class="structured-section"><h3>${esc(label)}</h3>${parts.join('')}</section>`,
        count,
        hasNested: innerNested.length > 0,
      });
    }
    nestedCustom.forEach((item) => subSections.push(item));
  }
  customSections.forEach((item) => subSections.push(item));

  if (!rootSection && !subSections.length) return null;

  subSections.sort((a, b) => b.count - a.count);

  return { title, rootSection, subSections };
}

function deriveEntityName(data) {
  const NAME_KEYS = ['applicant_name', 'account_name', 'name'];
  for (const key of NAME_KEYS) {
    const val = data[key];
    if (val && typeof val === 'string' && val.trim() && !STATUS_PHRASE.test(val)) return val.trim();
  }
  return null;
}

function deriveIntent(userQuery) {
  if (!userQuery) return null;
  const q = userQuery.toLowerCase();
  if (/workflow|request.?id/.test(q)) return 'Workflow';
  if (/quote|premium|underwrite|risk.?tier/.test(q)) return 'Underwriting';
  if (/explain|summar|analys/.test(q)) return 'Analysis';
  if (/applicant|policy|claim|bind|cancel/.test(q)) return 'Applicant';
  return null;
}

function deriveTitle(data, userQuery) {
  const entityName = deriveEntityName(data);
  const intent = deriveIntent(userQuery);

  if (intent === 'Workflow') {
    const wfId = data.workflow_id || data.applicant_id;
    if (wfId && typeof wfId === 'string') {
      const short = wfId.length > 14 ? wfId.slice(0, 8) + '…' : wfId;
      return entityName ? `Workflow — ${entityName}` : `Workflow ${short}`;
    }
    return entityName ? `Workflow — ${entityName}` : 'Workflow Details';
  }

  if (intent === 'Analysis') {
    if (entityName) return `Analysis — ${entityName}`;
    const aid = (typeof data._id === 'string' && data._id) || (typeof data.applicant_id === 'string' && data.applicant_id);
    if (aid) return `Analysis — ${aid}`;
  }

  if (entityName && intent) return `${intent} — ${entityName}`;
  if (entityName) return entityName;

  const keyStr = Object.keys(data).join(' ').toLowerCase();
  if (intent) return `${intent} Details`;
  if (/workflow/.test(keyStr)) return 'Workflow Details';
  if (/underwriting|risk_tier|premium/.test(keyStr)) return 'Underwriting Details';
  if (/applicant/.test(keyStr)) return 'Applicant Details';

  if (userQuery) {
    const words = userQuery.trim().split(/\s+/);
    const snippet = words.slice(0, 5).join(' ');
    return snippet.charAt(0).toUpperCase() + snippet.slice(1) + (words.length > 5 ? '…' : '');
  }
  return 'Details';
}
