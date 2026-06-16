import { useEffect, useRef, useState } from 'react';
import './App.css';
import { API_BASE_URL, MODE_CONFIG, SAMPLE_PROMPTS, SESSION_KEY, REQUIRED_FIELDS, OPTIONAL_FIELDS, FIELD_CATEGORIES } from './lib/constants';

const SECTION_ICONS = {
  'Workflow Status': (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#4f46e5" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/>
    </svg>
  ),
  'Audit Trail': (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#0891b2" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
    </svg>
  ),
  'Applicant Information': (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#059669" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2"/>
    </svg>
  ),
  'Analytics & Insights': (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#d97706" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>
    </svg>
  ),
};

const FIELD_MATCHERS = {
  applicant_name:   /applicant\s*(name)?\s*[:=]\s*\S/i,
  policy_type:      /policy\s*type\s*[:=]\s*\S/i,
  contact_info:     /email\s*[:=]\s*\S/i,
  location_address: /address\s*[:=]\s*\S/i,
  coverage_request: /coverage\s*(amount)?\s*[:=]\s*\S/i,
  declared_value:   /declared\s*(value)?\s*[:=]\s*\S/i,
  risk_profile:     /(property\s*type|year\s*built|prior\s*claims)\s*[:=]\s*\S/i,
};
import { buildStructuredCardData, flattenObject, isStructuredText, parseStructuredResponse, renderBubbleHtml } from './lib/format';

const getModeStorageKey = (mode) => mode === 'onboarding' ? `${SESSION_KEY}_onboarding` : `${SESSION_KEY}_shared`;

const generateId = () => {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID().slice(0, 8);
  }
  return Math.random().toString(16).slice(2, 10);
};

const createMessage = ({ role, text = '', html = null, cardData = null, structured = false }) => ({
  id: generateId(),
  role,
  text,
  html,
  cardData,
  structured,
});

function SubGrid({ sections }) {
  if (sections.length === 0) return null;
  if (sections.length === 1) {
    return (
      <div className="structured-grid">
        {/* eslint-disable-next-line react/no-danger */}
        <div dangerouslySetInnerHTML={{ __html: sections[0].html }} />
      </div>
    );
  }

  const total = sections.reduce((s, sec) => s + sec.count, 0);
  const maxIdx = sections.reduce((best, sec, i) => sec.count > sections[best].count ? i : best, 0);
  const dominant = total > 0 && sections[maxIdx].count / total > 0.5;

  if (!dominant) {
    return (
      <div className="structured-grid structured-grid--dynamic">
        {sections.map((sec, i) => (
          // eslint-disable-next-line react/no-danger
          <div key={i} dangerouslySetInnerHTML={{ __html: sec.html }} />
        ))}
      </div>
    );
  }

  const dom = sections[maxIdx];
  const others = sections.filter((_, i) => i !== maxIdx);

  // Dominant always owns the full row; others flow below in a dynamic grid
  return (
    <>
      <div className="structured-grid">
        {/* eslint-disable-next-line react/no-danger */}
        <div dangerouslySetInnerHTML={{ __html: dom.html }} />
      </div>
      {others.length > 0 && (
        <div className="structured-grid structured-grid--dynamic">
          {others.map((sec, i) => (
            // eslint-disable-next-line react/no-danger
            <div key={i} dangerouslySetInnerHTML={{ __html: sec.html }} />
          ))}
        </div>
      )}
    </>
  );
}

function StructuredCard({ cardData }) {
  const { title, rootSection, subSections } = cardData;

  return (
    <>
      <div className="structured-header">
        <p className="structured-label">{title}</p>
      </div>
      {rootSection && (
        <div className="structured-grid">
          {/* eslint-disable-next-line react/no-danger */}
          <div dangerouslySetInnerHTML={{ __html: rootSection }} />
        </div>
      )}
      <SubGrid sections={subSections} />
    </>
  );
}

const initialMessages = {
  onboarding: [],
  track: [],
  servicing: [],
  labs: [],
};

function App() {
  const [mode, setMode] = useState(() => {
    const m = new URLSearchParams(window.location.search).get('mode');
    return ['onboarding', 'track', 'servicing', 'labs'].includes(m) ? m : 'onboarding';
  });
  const [env, setEnv] = useState('prod');
  const [sessionId, setSessionId] = useState('');
  const [messagesByMode, setMessagesByMode] = useState(initialMessages);
  const [inputText, setInputText] = useState(() => {
    const p = new URLSearchParams(window.location.search);
    if (p.get('from_demo') === '1' && p.get('mode') === 'labs')
      return 'Summarize applicant 06623088-079c-446b-9cdb-e3fc840fcbd3, provide gaps, coverage opportunities, or incomplete underwriting information';
    return '';
  });
  const [busy, setBusy] = useState(false);
  const [applicantId, setApplicantId] = useState(() => {
    const p = new URLSearchParams(window.location.search);
    const aid = p.get('applicant_id');
    return aid || localStorage.getItem('applicantId') || '';
  });
  const fromDemo = new URLSearchParams(window.location.search).get('from_demo') === '1';
  const [demoLabsQueryUsed, setDemoLabsQueryUsed] = useState(false);
  const [requestId, setRequestId] = useState('');
  const [boardingSessionId, setBoardingSessionId] = useState(
    () => localStorage.getItem(getModeStorageKey('onboarding')) || ''
  );
  const [draftData, setDraftData] = useState({});
  const [draftComplete, setDraftComplete] = useState(false);
  const [phase, setPhase] = useState('collecting');
  const [lastMessage, setLastMessage] = useState('');
  const [typing, setTyping] = useState(false);
  const [toast, setToast] = useState('');
  const [error, setError] = useState('');
  const messagesRef = useRef(null);
  const inputRef = useRef(null);
  const abortControllerRef = useRef(null);
  const selectionRangeRef = useRef(null);

  const [auth, setAuth] = useState(null);
  const [authConfig, setAuthConfig] = useState({ github_enabled: false, linkedin_enabled: false });
  const [profileOpen, setProfileOpen] = useState(false);
  const [qsOpen, setQsOpen] = useState(false);
  const [collapsedSections, setCollapsedSections] = useState({});
  const profileDropdownRef = useRef(null);
  const [authGateVisible, setAuthGateVisible] = useState(() => {
    if (typeof window === 'undefined') return false;
    const params = new URLSearchParams(window.location.search);
    if (params.get('from_demo') === '1') return false;
    const host = window.location.hostname;
    return !(host === 'localhost' || host === '127.0.0.1');
  });

  const activeMessages = messagesByMode[mode] || [];

  useEffect(() => {
    const storedSession = localStorage.getItem(getModeStorageKey(mode));
    const session = storedSession || generateId();
    localStorage.setItem(getModeStorageKey(mode), session);
    setSessionId(session);
    if (mode === 'onboarding') setBoardingSessionId(session);

    setMessagesByMode((prev) => {
      if (prev[mode] && prev[mode].length > 0) return prev;
      return {
        ...prev,
        [mode]: [
          createMessage({
            role: 'ai',
            text: MODE_CONFIG[mode].welcome,
            html: renderBubbleHtml(MODE_CONFIG[mode].welcome),
            structured: false,
          }),
        ],
      };
    });
  }, [mode]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const storedApplicantId = localStorage.getItem('applicantId');
    const storedRequestId = localStorage.getItem('requestId');
    if (storedApplicantId) setApplicantId(storedApplicantId);
    if (storedRequestId) setRequestId(storedRequestId);
  }, []);

  useEffect(() => {
    if (!messagesRef.current) return;
    messagesRef.current.scrollTop = messagesRef.current.scrollHeight;
  }, [activeMessages, typing]);

  useEffect(() => {
    if (!requestId || applicantId) return;
    const interval = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE_URL}/workflow/applicant/request/${requestId}`);
        if (!res.ok) return;
        const body = await res.json();
        const maybeApplicantId = body.applicant_id ||
          (typeof body.reply === 'string' && body.reply.match(/applicant[_\s]?id\s*[:=]?\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i)?.[1]);
        if (maybeApplicantId) {
          setApplicantId(maybeApplicantId);
          localStorage.setItem('applicantId', maybeApplicantId);
          showToast('Applicant ID captured');
        }
      } catch (_) { /* network hiccup — retry next tick */ }
    }, 5000);
    return () => clearInterval(interval);
  }, [requestId, applicantId]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get('from_demo') === '1') {
      setAuthGateVisible(false);
      return;
    }

    const host = window.location.hostname;
    if (host === 'localhost' || host === '127.0.0.1') {
      setAuthGateVisible(false);
      return;
    }

    const existing = getAuth();
    if (existing) {
      setAuth(existing);
      setAuthGateVisible(false);
      return;
    }

    const refHex = params.get('ref');
    if (refHex) {
      fetch(`/api/auth/check-ref?ref=${encodeURIComponent(refHex)}`)
        .then((res) => (res.ok ? res.json() : null))
        .then((data) => {
          if (data?.allowed) {
            const portalUser = { email: data.email };
            localStorage.setItem('portal-auth', JSON.stringify(portalUser));
            setAuth({ provider: 'portal', ...portalUser });
            setAuthGateVisible(false);
          } else {
            setAuthGateVisible(true);
            fetch('/api/auth/config')
              .then((r) => (r.ok ? r.json() : null))
              .then((cfg) => { if (cfg) setAuthConfig(cfg); })
              .catch(() => {});
          }
        })
        .catch(() => { setAuthGateVisible(true); });
      return;
    }

    setAuthGateVisible(true);
    fetch('/api/auth/config')
      .then((res) => (res.ok ? res.json() : null))
      .then((cfg) => {
        if (cfg) setAuthConfig(cfg);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!profileOpen) return;
    const handler = (e) => {
      if (profileDropdownRef.current && !profileDropdownRef.current.contains(e.target)) {
        setProfileOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [profileOpen]);

  const setModeSafe = (nextMode) => {
    if (mode === nextMode) return;
    abortActiveRequest();
    setTyping(false);
    setBusy(false);
    setMode(nextMode);
    setInputText('');
    setError('');
  };

  const getDraftValue = (draft, key) => {
    const parts = key.split('.');
    let value = draft;
    for (const part of parts) {
      if (!value || typeof value !== 'object') return undefined;
      value = value[part];
    }
    return value;
  };

  const hasDraftValue = (value) => {
    if (value == null) return false;
    if (typeof value === 'boolean') return true;
    if (typeof value === 'object') {
      return Object.values(value).some((v) => hasDraftValue(v));
    }
    return String(value).trim() !== '';
  };

  const updateDraftFromResponse = (draft, complete, nextPhase) => {
    if (draft && typeof draft === 'object') {
      setDraftData((prev) => ({ ...prev, ...draft }));
    }
    if (typeof complete === 'boolean') {
      setDraftComplete(complete);
    }
    if (typeof nextPhase === 'string') {
      setPhase(nextPhase);
    }
  };

  const updateMessage = (id, update) => {
    setMessagesByMode((prev) => ({
      ...prev,
      [mode]: prev[mode].map((msg) => (msg.id === id ? { ...msg, ...update } : msg)),
    }));
  };

  const appendMessage = (message) => {
    const msg = message.id ? message : createMessage(message);
    setMessagesByMode((prev) => ({
      ...prev,
      [mode]: [...(prev[mode] || []), msg],
    }));
  };

  const showToast = (message) => {
    setToast(message);
    setTimeout(() => setToast(''), 2500);
  };

  const getAuth = () => {
    try {
      const gh = localStorage.getItem('github-auth');
      if (gh) return { provider: 'github', ...JSON.parse(gh) };
      const li = localStorage.getItem('linkedin-auth');
      if (li) return { provider: 'linkedin', ...JSON.parse(li) };
      const po = localStorage.getItem('portal-auth');
      if (po) return { provider: 'portal', ...JSON.parse(po) };
    } catch (err) {
      // ignore parse errors
    }
    return null;
  };

  const githubLoginUrl = (clientId) => `https://github.com/login/oauth/authorize?client_id=${clientId}&state=github&scope=user:email`;
  const linkedinLoginUrl = (clientId) => {
    const redirectUri = `${window.location.origin}/auth/callback`;
    const scope = encodeURIComponent('openid profile email');
    return `https://www.linkedin.com/oauth/v2/authorization?response_type=code&client_id=${clientId}&redirect_uri=${encodeURIComponent(redirectUri)}&state=linkedin&scope=${scope}`;
  };

  const signOut = () => {
    localStorage.removeItem('github-auth');
    localStorage.removeItem('linkedin-auth');
    localStorage.removeItem('portal-auth');
    const url = new URL(window.location.href);
    url.searchParams.delete('ref');
    window.location.href = url.pathname + (url.search || '');
  };

  const abortActiveRequest = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
  };

  const resizeTextarea = () => {
    const ta = inputRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = `${Math.min(ta.scrollHeight, 120)}px`;
  };

  useEffect(() => {
    resizeTextarea();
    if (selectionRangeRef.current && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.setSelectionRange(selectionRangeRef.current.start, selectionRangeRef.current.end);
      selectionRangeRef.current = null;
    }
  }, [inputText]);

  const fallbackCopy = (text) => {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.left = '0';
    textarea.style.top = '-9999px';
    textarea.style.background = 'transparent';
    textarea.style.width = '1px';
    textarea.style.height = '1px';
    textarea.style.fontSize = '12px';
    document.body.appendChild(textarea);
    textarea.select();
    textarea.setSelectionRange(0, textarea.value.length);
    const success = document.execCommand('copy');
    document.body.removeChild(textarea);
    return success;
  };

  const copyText = (text, label) => {
    if (!text) return;
    const onCopied = () => showToast(label);
    const onFallback = () => {
      if (fallbackCopy(text)) {
        showToast(label);
      } else {
        showToast('Copy failed');
      }
    };

    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text).then(onCopied).catch(onFallback);
    } else {
      onFallback();
    }
  };

  const handleMessagesClick = (event) => {
    const btn = event.target.closest('.id-copy-btn');
    if (!btn) return;
    const fullId = btn.closest('.id-chip')?.dataset.fullId;
    if (fullId) copyText(fullId, 'ID copied');
  };

  const copyApplicantIdToServicing = () => {
    if (!applicantId) return;
    copyText(applicantId, 'Applicant ID copied — switching to Servicing');
    setModeSafe('servicing');
    const prompt = `Get applicant details for ${applicantId}`;
    selectionRangeRef.current = { start: prompt.length, end: prompt.length };
    setInputText(prompt);
  };

  const copyRequestIdToTrack = () => {
    if (!requestId) return;
    copyText(requestId, 'Application ID copied — switching to Track');
    setModeSafe('track');
    const prompt = `Get workflow by applicant_id ${requestId}`;
    selectionRangeRef.current = { start: prompt.length, end: prompt.length };
    setInputText(prompt);
  };

  const copyRequestId = () => {
    if (!requestId) return;
    copyText(requestId, 'Application ID copied');
  };

  const resetConversation = () => {
    abortActiveRequest();
    setMessagesByMode((prev) => ({
      ...prev,
      [mode]: [
        createMessage({
          role: 'ai',
          text: MODE_CONFIG[mode].welcome,
          html: renderBubbleHtml(MODE_CONFIG[mode].welcome),
          structured: false,
        }),
      ],
    }));
    setDraftData({});
    setDraftComplete(false);
    setPhase('collecting');
    setInputText('');
    setLastMessage('');
    setError('');
    setTyping(false);
    setBusy(false);
  };

  const clearSession = () => {
    abortActiveRequest();
    localStorage.removeItem(getModeStorageKey('onboarding'));
    localStorage.removeItem(getModeStorageKey('track'));
    localStorage.removeItem(getModeStorageKey('servicing'));
    localStorage.removeItem(getModeStorageKey('labs'));
    localStorage.removeItem('applicantId');
    localStorage.removeItem('requestId');
    setMessagesByMode({ onboarding: [], track: [], servicing: [], labs: [] });
    setApplicantId('');
    setRequestId('');
    setMode('onboarding');
    setPhase('collecting');
    setInputText('');
  };

  const buildCardData = (reply, query) => {
    const trimmed = reply.trim();
    // Try JSON first (agent returned raw dict/object)
    //This should move back to the backend once we have a more stable schema and can reliably detect structured responses there
    if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
      try {
        const raw = JSON.parse(trimmed);
        const data = Array.isArray(raw) ? raw[0] : raw;
        if (data && typeof data === 'object' && Object.keys(data).length > 0) {
          const cardData = buildStructuredCardData(data, query);
          if (cardData) return cardData;
        }
      } catch (e) { /* not valid JSON, fall through */ }
    }
    if (!isStructuredText(reply)) return null;
    const parsed = parseStructuredResponse(reply);
    if (!Object.keys(parsed).length) return null;
    return buildStructuredCardData(parsed, query);
  };

  const sendServicing = async (message) => {
    setBusy(true);
    setTyping(true);
    setError('');
    const abortCtrl = new AbortController();
    abortControllerRef.current = abortCtrl;
    const capturedMode = mode;
    try {
      const res = await fetch(`${API_BASE_URL}/intake/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Correlation-ID': generateId(), ...(auth?.provider === 'portal' ? { 'X-Auth-Provider': 'portal' } : {}) },
        body: JSON.stringify({ session_id: sessionId, message, mode: ['track', 'labs'].includes(mode) ? 'servicing' : mode, env }),
        signal: abortCtrl.signal,
      });
      if (mode !== capturedMode) return;
      setTyping(false);
      if (!res.ok) {
        const err = 'Something went wrong on our end. Please wait a moment and try again.';
        appendMessage({ role: 'ai', text: err, structured: false });
        setError(err);
        return;
      }
      const body = await res.json();
      if (mode !== capturedMode) return;
      const displayText = typeof body.reply === 'string' ? body.reply : 'No response';
      const cardData = buildCardData(displayText, message);
      appendMessage({
        role: 'ai',
        text: displayText,
        html: cardData ? null : renderBubbleHtml(displayText),
        cardData,
        structured: !!cardData,
      });
      if (body._id) {
        setApplicantId(body._id);
        localStorage.setItem('applicantId', body._id);
        showToast('Applicant ID captured');
      }
      if (body.request_id) {
        const newRequestId = body.request_id || requestId;
        setRequestId(newRequestId);
        localStorage.setItem('requestId', newRequestId);
      }
      updateDraftFromResponse(body.draft, body.draft_complete, body.phase);
    } catch (err) {
      if (err.name === 'AbortError') return;
      const msg = 'Unable to reach the server. Please try again after sometime.';
      appendMessage({ role: 'ai', text: msg, structured: false });
      setError(msg);
    } finally {
      if (abortControllerRef.current === abortCtrl) abortControllerRef.current = null;
      setBusy(false);
      setTyping(false);
      if (fromDemo && mode === 'labs') {
        setDemoLabsQueryUsed(true);
        setTimeout(() => setAuthGateVisible(true), 2500);
      }
    }
  };

  const parseSseFrameBuffer = (chunk, state) => {
    state.buffer += chunk;
    const frames = [];

    while (true) {
      const marker = state.buffer.indexOf('data:');
      if (marker === -1) break;
      const rest = state.buffer.slice(marker + 5);
      const jsonStart = rest.indexOf('{');
      if (jsonStart === -1) {
        state.buffer = state.buffer.slice(marker);
        break;
      }

      let depth = 0;
      let endIndex = -1;
      for (let i = jsonStart; i < rest.length; i += 1) {
        const ch = rest[i];
        if (ch === '{') depth += 1;
        else if (ch === '}') {
          depth -= 1;
          if (depth === 0) {
            endIndex = i;
            break;
          }
        }
      }

      if (endIndex === -1) {
        state.buffer = state.buffer.slice(marker);
        break;
      }

      const jsonText = rest.slice(jsonStart, endIndex + 1);
      frames.push(jsonText);
      state.buffer = rest.slice(endIndex + 1);
    }

    return frames;
  };

  const sendOnboarding = async (message) => {
    setBusy(true);
    setTyping(true);
    setError('');
    const abortCtrl = new AbortController();
    abortControllerRef.current = abortCtrl;
    try {
      const res = await fetch(`${API_BASE_URL}/intake/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Correlation-ID': generateId(), ...(auth?.provider === 'portal' ? { 'X-Auth-Provider': 'portal' } : {}) },
        body: JSON.stringify({ session_id: sessionId, message, env }),
        signal: abortCtrl.signal,
      });
      if (!res.ok) {
        const err = 'Something went wrong on our end. Please wait a moment and try again.';
        appendMessage({ role: 'ai', text: err, structured: false });
        setError(err);
        setTyping(false);
        return;
      }
      const reader = res.body?.getReader();
      if (!reader) {
        const err = 'No streaming response available.';
        appendMessage({ role: 'ai', text: err, structured: false });
        setError(err);
        setTyping(false);
        return;
      }
      const aiMessage = createMessage({ role: 'ai', text: '', structured: false });
      appendMessage(aiMessage);
      const decoder = new TextDecoder();
      const sseState = { buffer: '' };
      let text = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });
        const frames = parseSseFrameBuffer(chunk, sseState);

        for (const jsonText of frames) {
          try {
            const event = JSON.parse(jsonText);
            if (event.type === 'token' && typeof event.token === 'string') {
              text += event.token;
              updateMessage(aiMessage.id, { text, html: renderBubbleHtml(text), structured: false });
            } else if (event.type === 'done') {
              if (typeof event.reply === 'string') {
                text = event.reply;
                updateMessage(aiMessage.id, { text, html: renderBubbleHtml(text), structured: false });
              }
              if (event._id) {
                setApplicantId(event._id);
                localStorage.setItem('applicantId', event._id);
              }
              if (event.request_id) {
                setRequestId(event.request_id);
                localStorage.setItem('requestId', event.request_id);
              }
              updateDraftFromResponse(event.draft, event.draft_complete, event.phase);
            }
          } catch (err) {
            // If parsing fails, ignore partial or malformed frame and continue.
          }
        }
      }

      if (sseState.buffer.trim()) {
        try {
          const event = JSON.parse(sseState.buffer.trim());
          if (event.type === 'token' && typeof event.token === 'string') {
            text += event.token;
            updateMessage(aiMessage.id, { text, html: renderBubbleHtml(text), structured: false });
          } else if (event.type === 'done') {
            if (typeof event.reply === 'string') {
              text = event.reply;
              updateMessage(aiMessage.id, { text, html: renderBubbleHtml(text), structured: false });
            }
            if (event._id) {
              setApplicantId(event._id);
              localStorage.setItem('applicantId', event._id);
            }
            if (event.request_id) {
              setRequestId(event.request_id);
              localStorage.setItem('requestId', event.request_id);
            }
            updateDraftFromResponse(event.draft, event.draft_complete, event.phase);
          }
        } catch (err) {
          // final leftover is not JSON; ignore
        }
      }
    } catch (err) {
      if (err.name === 'AbortError') return;
      const msg = 'Unable to reach the server. Please try again later.';
      appendMessage({ role: 'ai', text: msg, structured: false });
      setError(msg);
    } finally {
      if (abortControllerRef.current === abortCtrl) abortControllerRef.current = null;
      setBusy(false);
      setTyping(false);
    }
  };

  const handleRetry = () => {
    if (busy || !lastMessage) return;
    setError('');
    appendMessage({ role: 'user', text: lastMessage, structured: false });
    if (mode === 'track' || mode === 'servicing' || mode === 'labs') {
      void sendServicing(lastMessage);
    } else {
      void sendOnboarding(lastMessage);
    }
  };

  const resolvePrompt = (raw) => {
    let prompt = raw;
    if (applicantId) prompt = prompt.replace(/<applicant_id>/g, applicantId);
    if (requestId) prompt = prompt.replace(/<request_id>/g, requestId);
    if (sessionId) prompt = prompt.replace(/<session_id>/g, sessionId);
    if (boardingSessionId) prompt = prompt.replace(/<boarding_session_id>/g, boardingSessionId);
    return prompt;
  };

  const fillPrompt = (raw) => {
    const prompt = resolvePrompt(raw);
    const match = /<[^>]+>/.exec(prompt);
    selectionRangeRef.current = match
      ? { start: match.index, end: match.index + match[0].length }
      : null;
    setInputText(prompt);
  };

  const handleSend = () => {
    const trimmed = inputText.trim();
    if (!trimmed || busy) return;
    setLastMessage(trimmed);
    appendMessage({ role: 'user', text: trimmed, structured: false });
    setInputText('');
    if (mode === 'track' || mode === 'servicing' || mode === 'labs') {
      void sendServicing(trimmed);
    } else {
      void sendOnboarding(trimmed);
    }
  };

  const promptItems = SAMPLE_PROMPTS[mode] || [];
  const currentTitle = MODE_CONFIG[mode].title;
  const placeholder = MODE_CONFIG[mode].placeholder;
  const onboardingLocked = mode === 'onboarding' && Boolean(applicantId);
  const demoLabsLocked = fromDemo && mode === 'labs' && demoLabsQueryUsed;
  const inputPlaceholder = onboardingLocked
    ? 'Underwriting intake started. Start a new session to underwrite another applicant.'
    : demoLabsLocked
    ? 'Sign up at insuretek.ai to continue exploring.'
    : placeholder;
  const TOKEN_LIMIT = 1000;
  const estimatedTokens = Math.ceil(inputText.length / 4);
  const overTokenLimit = estimatedTokens > TOKEN_LIMIT;
  const sendButtonDisabled = busy || onboardingLocked || overTokenLimit || demoLabsLocked;
  const textareaDisabled = busy || onboardingLocked || demoLabsLocked;

  const renderedDraft = [
    ...REQUIRED_FIELDS,
    ...OPTIONAL_FIELDS.filter((field) => hasDraftValue(getDraftValue(draftData, field.key))),
  ].map((field) => {
    const value = getDraftValue(draftData, field.key);
    const filled = hasDraftValue(value);
    return {
      key: field.key,
      label: field.label,
      filled,
      value,
    };
  });
  const requiredFilledCount = REQUIRED_FIELDS.filter((field) => hasDraftValue(getDraftValue(draftData, field.key))).length;
  const filledCount = renderedDraft.filter((row) => row.filled).length;
  const isDraftReady = draftComplete || requiredFilledCount === REQUIRED_FIELDS.length;
  const draftStatus = isDraftReady ? 'Validated' : 'Incomplete';
  const draftPercent = Math.round((requiredFilledCount / REQUIRED_FIELDS.length) * 100);
  const isSubmitted = phase === 'done' || Boolean(requestId) || draftComplete;

  const showParsePreview = mode === 'onboarding' && !isSubmitted && inputText.length >= 80;
  const parsedFromInput = showParsePreview
    ? REQUIRED_FIELDS.reduce((acc, f) => { acc[f.key] = Boolean(FIELD_MATCHERS[f.key]?.test(inputText)); return acc; }, {})
    : null;

  const allFieldMap = Object.fromEntries([...REQUIRED_FIELDS, ...OPTIONAL_FIELDS].map((f) => [f.key, f]));
  const draftByCategory = FIELD_CATEGORIES.map((cat) => ({
    cat,
    rows: renderedDraft.filter((row) => allFieldMap[row.key]?.category === cat),
  })).filter((g) => g.rows.length > 0);

  const PHASE_LABELS = {
    collecting: 'Collect',
    offering_optional: 'Add optional',
    confirming: 'Confirm',
    done: 'Done',
  };
  const currentPhaseLabel = PHASE_LABELS[phase] || 'Collect';

  if (authGateVisible) {
    return (
      <div className="login-gate-shell">
        <div className="login-gate-card">
          <div className="login-gate-logo">Insuretek</div>
          <h2>Sign in to continue</h2>
          <p>Insurance Underwriting Intake</p>
          <div className="login-gate-buttons">
            {authConfig.github_enabled ? (
              <a className="login-btn github" href={githubLoginUrl(authConfig.github_client_id)}>
                Continue with GitHub
              </a>
            ) : null}
            {authConfig.linkedin_enabled ? (
              <a className="login-btn linkedin" href={linkedinLoginUrl(authConfig.linkedin_client_id)}>
                Continue with LinkedIn
              </a>
            ) : null}
            {!authConfig.github_enabled && !authConfig.linkedin_enabled ? (
              <div className="auth-warning">No auth providers configured on the server.</div>
            ) : null}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="logo">
          <svg className="logo-mark" width="28" height="28" viewBox="0 0 28 28" fill="none" aria-hidden="true">
            <rect width="28" height="28" rx="8" fill="#4F46E5"/>
            <path d="M8 14h4.5a1.5 1.5 0 0 0 0-3H8v10h2.5v-3.5H12l2 3.5H17l-2.3-4a3.5 3.5 0 0 0-2.2-6.5H8v10" fill="white" opacity=".9"/>
            <path d="M19 8h-2v12h2V8Z" fill="white" opacity=".6"/>
          </svg>
          <div className="logo-text">
            <span className="logo-brand">Insuretek</span>
            <span className="logo-tagline">Intelligent underwriting intake. Powered by agents.</span>
          </div>
        </div>
        <nav className="pipeline-nav" role="tablist" aria-label="Workflow steps">
          <button
            type="button"
            className={`pipeline-step${mode === 'onboarding' ? ' pipeline-step--active' : ''}${requestId ? ' pipeline-step--done' : ''}`}
            onClick={() => setModeSafe('onboarding')}
            title="Step 1: Underwrite a new applicant"
          >
            <span className="pipeline-num">{requestId ? '✓' : '1'}</span>
            <span className="pipeline-label">Intake</span>
          </button>
          <span className="pipeline-connector" aria-hidden="true" />
          <button
            type="button"
            className={`pipeline-step${mode === 'track' ? ' pipeline-step--active' : ''}${applicantId ? ' pipeline-step--done' : ''}`}
            onClick={() => setModeSafe('track')}
            title="Step 2: Use applicant_id to track workflow and resolve applicant_id"
          >
            <span className="pipeline-num">{applicantId ? '✓' : '2'}</span>
            <span className="pipeline-label">Track</span>
          </button>
          <span className="pipeline-connector" aria-hidden="true" />
          <button
            type="button"
            className={`pipeline-step${mode === 'servicing' ? ' pipeline-step--active' : ''}${applicantId ? ' pipeline-step--done' : ''}`}
            onClick={() => setModeSafe('servicing')}
            title="Step 3: Use applicant_id for policy servicing and management"
          >
            <span className="pipeline-num">{applicantId ? '✓' : '3'}</span>
            <span className="pipeline-label">Manage</span>
          </button>
          <div className="pipeline-divider" aria-hidden="true" />
          <button
            type="button"
            className={`pipeline-step${mode === 'labs' ? ' pipeline-step--active' : ''}`}
            onClick={() => setModeSafe('labs')}
            title="Insuretek Labs — free-form queries"
          >
            <span className="pipeline-label">Insuretek Labs</span>
          </button>
        </nav>
        <div className="env-selector-wrap">
          <span className={`env-dot ${env === 'prod' ? 'env-dot--live' : 'env-dot--shadow'}`} aria-hidden="true" />
          <select className="env-select" value={env} onChange={(e) => setEnv(e.target.value)} aria-label="Active environment">
            <option value="prod">Live</option>
            <option value="shadow">Shadow</option>
          </select>
        </div>
        <div className="header-actions">
          {auth ? (
            <div className="profile-dropdown-wrap" ref={profileDropdownRef}>
              <button
                type="button"
                className="user-chip"
                onClick={() => setProfileOpen((o) => !o)}
                aria-haspopup="true"
                aria-expanded={profileOpen}
              >
                {auth.avatar_url ? <img className="user-avatar" src={auth.avatar_url} alt="User avatar" /> : null}
                <span className="user-name">{auth.login || auth.email || 'User'}</span>
                <svg className={`profile-chevron${profileOpen ? ' profile-chevron--open' : ''}`} width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M2 4l4 4 4-4" />
                </svg>
              </button>
              {profileOpen && (
                <div className="profile-dropdown-menu" role="menu">
                  <button type="button" className="profile-menu-item profile-menu-item--danger" role="menuitem" onClick={() => { setProfileOpen(false); signOut(); }}>
                    Sign out
                  </button>
                </div>
              )}
            </div>
          ) : null}
          <button type="button" className="session-badge" onClick={() => copyText(sessionId, 'Session ID copied')}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M12 1L3 5v6c0 5.25 3.75 10.25 9 12 5.25-1.75 9-6.75 9-12V5l-9-4z" />
              <path d="M8 11l2 2 4-4" />
            </svg>
            <span>{sessionId || 'Loading…'}</span>
          </button>
        </div>
      </header>

      <main className={`body ${mode === 'track' || mode === 'servicing' ? 'servicing-mode' : ''}${mode === 'labs' ? ' labs-mode' : ''}`}>
        {mode !== 'labs' && <aside className="prompts-pane" aria-labelledby="sample-prompts-title">
          {mode === 'onboarding' && (
          <div className="quick-start-guide">
            <button type="button" className="qs-title qs-toggle" onClick={() => setQsOpen((o) => !o)} aria-expanded={qsOpen}>
              Quick Start
              <svg className={`qs-chevron${qsOpen ? ' qs-chevron--open' : ''}`} width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M2 4l4 4 4-4" />
              </svg>
            </button>
            {qsOpen && (<>
            <div className="qs-step">
              <div className="qs-step-num">1</div>
              <div className="qs-step-body">
                <div className="qs-step-title">Intake</div>
                <div className="qs-step-desc">Describe the applicant — copy <span className="qs-id-pill">request_id</span> from the success card.</div>
              </div>
            </div>
            <div className="qs-arrow">↓</div>
            <div className="qs-step">
              <div className="qs-step-num">2</div>
              <div className="qs-step-body">
                <div className="qs-step-title">Track</div>
                <div className="qs-step-desc">Check underwriting status with your <span className="qs-id-pill">request_id</span> — get <span className="qs-id-pill">applicant_id</span> when complete.</div>
              </div>
            </div>
            <div className="qs-arrow">↓</div>
            <div className="qs-step">
              <div className="qs-step-num">3</div>
              <div className="qs-step-body">
                <div className="qs-step-title">Manage</div>
                <div className="qs-step-desc">Use <span className="qs-id-pill">applicant_id</span> for policy details, coverage, risk profile, and more.</div>
              </div>
            </div>
            </>)}
          </div>
          )}

          {mode === 'track' && requestId && (
            <div className="applicant-scope">
              <div className="scope-header">Tracking</div>
              <div className="scope-row">
                <span className="scope-dot scope-dot--neutral" />
                <span className="scope-label">Request</span>
                <button type="button" className="scope-id-chip" title={requestId} onClick={copyRequestIdToTrack}>
                  {requestId.length > 14 ? `${requestId.slice(0, 8)}…${requestId.slice(-4)}` : requestId}
                </button>
              </div>
              {applicantId && (
                <div className="scope-row">
                  <span className="scope-dot scope-dot--active" />
                  <span className="scope-label">Applicant</span>
                  <button type="button" className="scope-id-chip" title={applicantId} onClick={() => copyText(applicantId, 'Applicant ID copied')}>
                    {applicantId.length > 14 ? `${applicantId.slice(0, 8)}…${applicantId.slice(-4)}` : applicantId}
                  </button>
                </div>
              )}
              <div className="scope-hint-card">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
                Highlighted prompts auto-fill these IDs
              </div>
            </div>
          )}

          {mode === 'servicing' && applicantId && (
            <div className="applicant-scope">
              <div className="scope-header">Active Scope</div>
              <div className="scope-row">
                <span className="scope-dot scope-dot--active" />
                <span className="scope-label">Applicant</span>
                <button type="button" className="scope-id-chip" title={applicantId} onClick={() => copyText(applicantId, 'Applicant ID copied')}>
                  {applicantId.length > 14 ? `${applicantId.slice(0, 8)}…${applicantId.slice(-4)}` : applicantId}
                </button>
                <button type="button" className="scope-clear" title="Clear applicant scope" onClick={() => setApplicantId('')}>×</button>
              </div>
              <div className="scope-hint-card">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
                Highlighted prompts auto-fill this ID
              </div>
            </div>
          )}

          <div className="prompts-header" id="sample-prompts-title">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#7c3aed" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="section-icon"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>
            Prompts Library
          </div>
          <div className="prompts-body">
            {promptItems.some((item) => item.section)
              ? promptItems.map((group) => {
                  const isCollapsed = collapsedSections[group.section] !== false;
                  const toggleSection = () => setCollapsedSections((prev) => ({ ...prev, [group.section]: prev[group.section] === false }));
                  return (
                  <div key={group.section} className="prompt-section prompt-section--accordion">
                    <div
                      className="prompt-section-title prompt-section-title--toggle"
                      onClick={toggleSection}
                      role="button"
                      aria-expanded={!isCollapsed}
                    >
                      {SECTION_ICONS[group.section] && <span className="section-icon">{SECTION_ICONS[group.section]}</span>}
                      {group.section}
                      <svg className={`section-chevron${isCollapsed ? '' : ' section-chevron--open'}`} width="11" height="11" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                        <path d="M2 4l4 4 4-4"/>
                      </svg>
                    </div>
                    {!isCollapsed && (
                    <div className="prompt-section-body prompt-section-body--indented">
                      {group.prompts.map((prompt) => {
                        const resolved = resolvePrompt(prompt.prompt);
                        const autoFilled = resolved !== prompt.prompt && !/<[^>]+>/.test(resolved);
                        return (
                          <button key={prompt.prompt} type="button" className={`prompt-chip${autoFilled ? ' prompt-chip--resolved' : ''}`} onClick={() => fillPrompt(prompt.prompt, prompt.style ?? null)}>
                            <div className="prompt-card-title">{prompt.title}</div>
                            {autoFilled && <span className="chip-resolved-dot" aria-label="auto-fill" />}
                          </button>
                        );
                      })}
                    </div>
                    )}
                  </div>
                  );
                })
              : promptItems.map((prompt) => (
                  <button key={prompt.prompt} type="button" className={mode === 'onboarding' ? 'prompt-card' : 'prompt-chip'} onClick={() => fillPrompt(prompt.prompt, prompt.style ?? null)}>
                    <div className="prompt-card-title">{prompt.title}</div>
                    {prompt.preview ? <div className="prompt-card-preview">{prompt.preview}</div> : null}
                  </button>
                ))}
          </div>
        </aside>}

        <section className="chat-pane" aria-labelledby="chat-panel-title">
          <h2 id="chat-panel-title" className="sr-only">Chat conversation</h2>
          <div className={`chat-pane-toolbar${mode === 'track' && requestId ? ' chat-pane-toolbar--track' : ''}${mode === 'servicing' ? ' chat-pane-toolbar--manage' : ''}${mode === 'labs' ? ' chat-pane-toolbar--labs' : ''}`}>
            {mode === 'track' && (applicantId ? (
              <><span className="wcb-badge">Step 2 · Track</span><span className="wcb-desc">applicant_id resolved — proceed to Manage</span></>
            ) : requestId ? (
              <><span className="wcb-badge">Step 2 · Track</span><span className="wcb-desc">query your applicant_id to resolve applicant_id</span></>
            ) : (
              <><span className="wcb-badge wcb-badge--hint">Step 2 · Track</span><span className="wcb-desc">complete Intake first to get a applicant_id</span></>
            ))}
            {mode === 'servicing' && (applicantId ? (
              <><span className="wcb-badge">Step 3 · Manage</span><span className="wcb-desc">applicant_id loaded — all prompts target this applicant</span></>
            ) : (
              <><span className="wcb-badge wcb-badge--hint">Step 3 · Manage</span><span className="wcb-desc">resolve an applicant_id in Track first</span></>
            ))}
            {mode === 'labs' && !demoLabsLocked && (
              <><span className="wcb-badge wcb-badge--labs">Insuretek Labs</span><span className="wcb-desc">free-form queries — no workflow scope</span></>
            )}
            {demoLabsLocked && (
              <><span className="wcb-badge" style={{background:'#dcfce7',color:'#15803d'}}>Demo limit reached</span><span className="wcb-desc">Sign up to run unlimited queries</span><a href="https://insuretek.ai/" target="_blank" rel="noreferrer" className="wcb-badge" style={{background:'#16a34a',color:'#fff',textDecoration:'none'}}>Get started →</a></>
            )}
            <button
              type="button"
              className="chat-action-btn"
              onClick={resetConversation}
              title="Clear messages in this view — session IDs and collected data are preserved"
            >
              Reset Chat
            </button>
            <button
              type="button"
              className="chat-action-btn chat-action-btn--destructive"
              onClick={clearSession}
              title="Clear all conversations, sessions, applicant IDs, and Application IDs across all modes"
            >
              Clear All
            </button>
          </div>
          <div className="messages" ref={messagesRef} onClick={handleMessagesClick} role="log" aria-live="polite" aria-atomic="false">
            {activeMessages.map((msg) => (
              <div key={msg.id} className={`msg ${msg.role} ${msg.structured ? 'structured-msg' : ''}`}>
                <div className="avatar">
                  {msg.role === 'ai' ? (
                    <svg width="22" height="22" viewBox="0 0 28 28" fill="none" aria-hidden="true">
                      <rect width="28" height="28" rx="8" fill="#4F46E5"/>
                      <path d="M8 14h4.5a1.5 1.5 0 0 0 0-3H8v10h2.5v-3.5H12l2 3.5H17l-2.3-4a3.5 3.5 0 0 0-2.2-6.5H8v10" fill="white" opacity=".9"/>
                      <path d="M19 8h-2v12h2V8Z" fill="white" opacity=".6"/>
                    </svg>
                  ) : '🧑'}
                </div>
                {msg.cardData
                  ? <div className="bubble structured-data-card"><StructuredCard cardData={msg.cardData} /></div>
                  : <div className={`bubble ${msg.structured ? 'structured-data-card' : ''}`} dangerouslySetInnerHTML={{ __html: msg.html || renderBubbleHtml(msg.text) }} />}
              </div>
            ))}
            {typing && (
              <div className="msg ai typing">
                <div className="avatar">
                  <svg width="22" height="22" viewBox="0 0 28 28" fill="none" aria-hidden="true">
                    <rect width="28" height="28" rx="8" fill="#4F46E5"/>
                    <path d="M8 14h4.5a1.5 1.5 0 0 0 0-3H8v10h2.5v-3.5H12l2 3.5H17l-2.3-4a3.5 3.5 0 0 0-2.2-6.5H8v10" fill="white" opacity=".9"/>
                    <path d="M19 8h-2v12h2V8Z" fill="white" opacity=".6"/>
                  </svg>
                </div>
                <div className="bubble"><div className="dots"><span></span><span></span><span></span></div></div>
              </div>
            )}
          </div>

          <div className="input-bar">
            <textarea
              ref={inputRef}
              value={inputText}
              onChange={(event) => {
                setInputText(event.target.value);
              }}
              onKeyDown={(event) => event.key === 'Enter' && !event.shiftKey && (event.preventDefault(), handleSend())}
              onClick={() => demoLabsLocked && setAuthGateVisible(true)}
              placeholder={inputPlaceholder}
              rows={1}
              disabled={textareaDisabled}
            />
            <button type="button" className="send-btn" onClick={handleSend} disabled={sendButtonDisabled}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <line x1="22" y1="2" x2="11" y2="13" />
                <polygon points="22 2 15 22 11 13 2 9 22 2" />
              </svg>
            </button>
          </div>
          {inputText.length > 0 && (
            <div className={`token-counter${overTokenLimit ? ' token-counter--over' : ''}`}>
              {estimatedTokens} / {TOKEN_LIMIT} tokens{overTokenLimit ? ' — message too long' : ''}
            </div>
          )}
          {error ? (
            <div className="error-banner">
              <span>{error}</span>
              {lastMessage ? <button type="button" className="retry-btn" onClick={handleRetry}>Retry</button> : null}
            </div>
          ) : null}
        </section>

        {mode === 'onboarding' && (
        <aside className="draft-pane" aria-labelledby="draft-progress-title">
          {isSubmitted ? (
            <div className="success-card">
              <h4><span className="success-tick">✅</span> Request Received</h4>
              <div className="success-ids">
                <button type="button" className="copy-box" onClick={copyRequestIdToTrack} title="Click to copy and open in Servicing">
                  <span className="copy-box-label">request_id</span>
                  <span className="copy-box-val">{requestId || '—'}</span>
                </button>
                <button type="button" className="copy-box" onClick={copyApplicantIdToServicing} title="Click to copy and open in Manage">
                  <span className="copy-box-label">applicant_id</span>
                  <span className="copy-box-val">{applicantId || 'resolving…'}</span>
                </button>
              </div>
              {!applicantId && requestId && (
                <button type="button" className="next-step-btn" onClick={copyRequestIdToTrack}>
                  Step 2 → Go to Track with applicant_id
                </button>
              )}
              {applicantId && (
                <button type="button" className="next-step-btn next-step-btn--done" onClick={copyApplicantIdToServicing}>
                  Step 3 → Go to Manage with applicant_id
                </button>
              )}
            </div>
          ) : (
            <>
              <div className="phase-indicator" aria-label="Current onboarding phase">
                <span className={`phase-indicator-badge${showParsePreview ? ' phase-indicator-badge--detect' : ''}`}>
                  {showParsePreview ? 'Detect' : currentPhaseLabel}
                </span>
              </div>
              {showParsePreview && parsedFromInput ? (
                <div className="detect-summary">
                  {Object.values(parsedFromInput).filter(Boolean).length} / {REQUIRED_FIELDS.length} required fields found
                </div>
              ) : (
                <div className="progress-bar-wrap">
                  <div className="progress-label"><span>{draftPercent}%</span></div>
                  <div className="progress-bar"><div className="progress-fill" style={{ width: `${draftPercent}%` }} /></div>
                </div>
              )}
            </>
          )}
          <div className="draft-body">
            {draftByCategory.map(({ cat, rows }) => (
              <div key={cat} className="draft-section">
                <div className="draft-section-header">{cat}</div>
                {rows.map((row) => {
                  const active = showParsePreview && parsedFromInput ? parsedFromInput[row.key] : row.filled;
                  return (
                    <div key={row.key} className={`field-row ${active ? 'field-row--filled' : 'field-pending'}`}>
                      <div className={`field-icon ${active ? 'field-icon--filled' : ''}`}>{active ? '✓' : '○'}</div>
                      <div className="field-content">
                        <span className="field-key">{row.label}</span>
                        {!showParsePreview && row.filled ? (
                          <span className="field-val">[{typeof row.value === 'object' ? flattenObject(row.value) : String(row.value)}]</span>
                        ) : null}
                      </div>
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
        </aside>
        )}
      </main>

      <div className={`toast${toast ? ' show' : ''}`} aria-live="polite">{toast}</div>
    </div>
  );
}

export default App;
