export const SESSION_KEY = 'insuretek_intake_session';

const DEFAULT_API_ORIGIN = window.location.protocol === 'file:'
  ? (import.meta.env.VITE_API_ORIGIN || 'http://0.0.0.0:6001')
  : window.location.origin;

const INSURANCE_INTAKE_SAMPLE_DATA = `Applicant name: Smith Family Trust
Policy type: HOME
Contact email: smith@example.com
Property address: 123 Oak St, Austin, TX 78701, US
Coverage amount: $300,000
Deductible: $2,500
Declared value: $450,000
Property type: single_family
Year built: 1998
Prior claims: 0`;

export const API_BASE_URL = DEFAULT_API_ORIGIN;

export const MODE_CONFIG = {
  onboarding: {
    title: "Insurance Underwriting Intake",
    welcome: "What applicant would you like to underwrite?",
    placeholder: "Describe the applicant or answer my questions…",
  },
  track: {
    title: "Underwriting Workflow Tracking",
    welcome: "Hi! Provide a request_id or applicant_id to track your underwriting status.",
    placeholder: "Enter a request_id or applicant_id to track…",
  },
  servicing: {
    title: "Policy Servicing",
    welcome: "Hi! Ask me about an applicant — policy details, coverage, risk profile, and I'll fetch the answer.",
    placeholder: "Ask about an applicant, e.g. \"Get policy details for applicant Smith Family Trust\"",
  },
  labs: {
    title: "Insuretek Labs",
    welcome: "Labs mode — Experiment with open-ended prompts, explore underwriting data, and uncover insights.",
    placeholder: "Type anything…",
  },
};

export const FIELD_CATEGORIES = ['Applicant', 'Contact', 'Coverage', 'Risk'];

export const REQUIRED_FIELDS = [
  { key: 'applicant_name',   label: 'Applicant / business name',            category: 'Applicant' },
  { key: 'policy_type',      label: 'Policy type  (HOME | AUTO | COMMERCIAL | LIFE)', category: 'Applicant' },
  { key: 'contact_info',     label: 'Contact email',                         category: 'Contact' },
  { key: 'location_address', label: 'Property / risk address',               category: 'Contact' },
  { key: 'coverage_request', label: 'Coverage amount & deductible',          category: 'Coverage' },
  { key: 'declared_value',   label: 'Declared asset value',                  category: 'Coverage' },
  { key: 'risk_profile',     label: 'Property type, year built, prior claims', category: 'Risk' },
];

export const OPTIONAL_FIELDS = [];

export const SAMPLE_PROMPTS = {
  onboarding: [
    {
      title: 'Interactive',
      preview: 'Step-by-step guided field collection and confirmation',
      prompt: `Please underwrite a new applicant with the following details:\n\n${INSURANCE_INTAKE_SAMPLE_DATA}`,
    },
    {
      title: 'Single-Shot',
      preview: 'Single-shot setup with all required fields',
      prompt: `Underwrite a new applicant in one shot without asking follow-up questions and submit directly.\n\n${INSURANCE_INTAKE_SAMPLE_DATA}`,
    },
  ],
  track: [
    {
      section: 'Workflow Status',
      prompts: [
        { title: 'By Request ID', prompt: 'Get the orchestrator underwriting job status for request_id <request_id>. Show each step result (PASS/FAIL/SKIP) and any HITL suspension detail.' },
        { title: 'By Applicant ID', prompt: 'Get the latest workflow job for applicant <applicant_id>' },
      ],
    },
    {
      section: 'Audit Trail',
      prompts: [
        { title: 'Underwriting Audit for Session', prompt: 'Show me policy audit records for session <boarding_session_id>' },
        { title: 'Underwriting Audit for Request', prompt: 'Show policy audit records for request <request_id>' },
        { title: 'Servicing Audit for Session', prompt: 'Show me policy audit records for session <session_id>' },
        { title: 'ISAR Evidence by Applicant', prompt: 'Show ISAR evidence for applicant <applicant_id>' },
        { title: 'ISAR Evidence by Correlation', prompt: 'Show ISAR evidence for correlation_id <correlation_id>' },
      ],
    },
  ],
  servicing: [
    {
      section: 'Applicant Information',
      prompts: [
        { title: 'Applicant Details', prompt: 'Get applicant details for <applicant_id>' },
        { title: 'Active Policy', prompt: 'Get the active policy for applicant <applicant_id>' },
        { title: 'Applicant Summary', prompt: 'Summarize applicant <applicant_id> — coverage, risk profile, and underwriting gaps' },
        { title: 'Policy History', prompt: 'Get policy history for applicant <applicant_id>' },
        { title: 'Quote Status', prompt: 'Get quote status for applicant <applicant_id>' },
      ],
    },
    {
      section: 'Analytics & Insights',
      prompts: [
        { title: 'Similar Applicants', prompt: 'Find applicants similar to <applicant_id>' },
        { title: 'Geographic Distribution', prompt: 'Get geographic distribution of applicants by state or province' },
        { title: 'High-Risk Applicants', prompt: 'Get applicants with a HIGH_RISK or DECLINED risk tier' },
      ],
    },
  ],
};
