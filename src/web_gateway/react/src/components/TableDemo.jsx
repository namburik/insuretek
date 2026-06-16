import { useState } from 'react';
import { DataTable } from './Table';

// ── Sample data ───────────────────────────────────────────────────────────────

const APPLICANT_COLUMNS = [
  { key: 'id',           label: 'Applicant ID', type: 'id' },
  { key: 'name',         label: 'Name' },
  { key: 'policy_type',  label: 'Policy Type' },
  { key: 'coverage',     label: 'Coverage',     type: 'currency' },
  { key: 'status',       label: 'Status',       type: 'status' },
];

const APPLICANT_ROWS = [
  { id: '3fa85f64-5717-4562-b3fc-2c963f66afa6', name: 'Smith Family Trust',     policy_type: 'HOME',       coverage: 300000,  status: 'Active' },
  { id: 'a1b2c3d4-e5f6-7890-abcd-ef1234567890', name: 'Rivera, Maria L.',       policy_type: 'AUTO',       coverage: 75000,   status: 'Pending' },
  { id: '9d8c7b6a-5e4f-3210-fedc-ba9876543210', name: 'Northstar Holdings LLC', policy_type: 'COMMERCIAL', coverage: 1200000, status: 'Approved' },
  { id: '1234abcd-ef56-7890-1234-abcdef567890', name: 'Chen, David K.',         policy_type: 'HOME',       coverage: 450000,  status: 'In Progress' },
  { id: 'deadbeef-dead-beef-dead-beefdeadbeef', name: 'Greenfield Properties',  policy_type: 'COMMERCIAL', coverage: 850000,  status: 'Failed' },
  { id: 'cafebabe-cafe-babe-cafe-babecafebabe', name: 'Okafor, Amara J.',       policy_type: 'LIFE',       coverage: 500000,  status: 'Inactive' },
  { id: 'f00dcafe-f00d-cafe-f00d-cafef00dcafe', name: 'Pacific Coastal Trust',  policy_type: 'HOME',       coverage: 650000,  status: 'Verified' },
];

const LOG_COLUMNS = [
  { key: 'timestamp',    label: 'Timestamp' },
  { key: 'applicant_id', label: 'Application ID', type: 'id' },
  { key: 'event',        label: 'Event' },
  { key: 'duration_ms',  label: 'Duration',   type: 'number', align: 'right' },
  { key: 'outcome',      label: 'Outcome',    type: 'status' },
];

const LOG_ROWS = [
  { timestamp: '2026-05-23 09:14:02', applicant_id: 'req_01jvd8hn2e4x9q7bk3mc', event: 'applicant.create',        duration_ms: 312,  outcome: 'Success' },
  { timestamp: '2026-05-23 09:15:44', applicant_id: 'req_01jvd8hn2e4x9q7bk4ab', event: 'kyb.check.initiate',      duration_ms: 88,   outcome: 'Pending' },
  { timestamp: '2026-05-23 09:16:11', applicant_id: 'req_01jvd8hn2e4x9q7bk5cd', event: 'underwriting.decision',   duration_ms: 1042, outcome: 'Approved' },
  { timestamp: '2026-05-23 09:18:55', applicant_id: 'req_01jvd8hn2e4x9q7bk6ef', event: 'policy.bind',             duration_ms: 204,  outcome: 'Error' },
  { timestamp: '2026-05-23 09:20:01', applicant_id: 'req_01jvd8hn2e4x9q7bk7gh', event: 'coverage.tier.assign',    duration_ms: 57,   outcome: 'Success' },
];

const WORKFLOW_COLUMNS = [
  { key: 'workflow_id', label: 'Workflow ID',   type: 'id' },
  { key: 'type',        label: 'Type' },
  { key: 'applicant',   label: 'Applicant' },
  { key: 'step',        label: 'Current Step' },
  { key: 'status',      label: 'Status',        type: 'status' },
];

const WORKFLOW_ROWS = [
  { workflow_id: 'wf-aa1f2e3d4c5b6a7f', type: 'Underwriting', applicant: 'Smith Family Trust',     step: 'Risk Assessment',   status: 'Awaiting' },
  { workflow_id: 'wf-bb2e3d4c5b6a7f8e', type: 'Risk Review',  applicant: 'Rivera, Maria L.',       step: 'Manual Review',     status: 'In Progress' },
  { workflow_id: 'wf-cc3d4c5b6a7f8e9d', type: 'Claims',       applicant: 'Chen, David K.',         step: 'Evidence Review',   status: 'Pending' },
  { workflow_id: 'wf-dd4c5b6a7f8e9d0c', type: 'Renewal',      applicant: 'Pacific Coastal Trust',  step: 'Completed',         status: 'Success' },
  { workflow_id: 'wf-ee5b6a7f8e9d0c1b', type: 'Cancellation', applicant: 'Greenfield Properties',  step: 'Policy Closure',    status: 'Failed' },
];

// ── Demo shell ────────────────────────────────────────────────────────────────

const THEMES = ['auto', 'light', 'dark'];

export default function TableDemo() {
  const [theme, setTheme] = useState('auto');
  const [loading, setLoading] = useState(false);

  const toggleLoading = () => {
    setLoading(true);
    setTimeout(() => setLoading(false), 2000);
  };

  return (
    <div style={{ padding: 32, background: theme === 'dark' ? '#0B1120' : '#F8FAFC', minHeight: '100vh' }}>
      <div style={{ marginBottom: 24, display: 'flex', alignItems: 'center', gap: 12 }}>
        <strong style={{ color: theme === 'dark' ? '#F9FAFB' : '#0F172A' }}>Theme:</strong>
        {THEMES.map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTheme(t)}
            style={{
              padding: '4px 14px',
              borderRadius: 6,
              border: '1px solid #CBD5E1',
              background: theme === t ? '#4F46E5' : 'transparent',
              color: theme === t ? '#fff' : theme === 'dark' ? '#D1D5DB' : '#334155',
              cursor: 'pointer',
              fontWeight: 500,
            }}
          >
            {t}
          </button>
        ))}
        <button
          type="button"
          onClick={toggleLoading}
          style={{ marginLeft: 16, padding: '4px 14px', borderRadius: 6, border: '1px solid #CBD5E1', cursor: 'pointer' }}
        >
          Simulate loading
        </button>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 32 }}>
        <DataTable
          columns={APPLICANT_COLUMNS}
          rows={APPLICANT_ROWS}
          theme={theme}
          caption="Applicants"
          loading={loading}
          onRowClick={(row) => alert(`Clicked: ${row.name}`)}
        />

        <DataTable
          columns={LOG_COLUMNS}
          rows={LOG_ROWS}
          theme={theme}
          caption="Event Log"
          loading={loading}
        />

        <DataTable
          columns={WORKFLOW_COLUMNS}
          rows={WORKFLOW_ROWS}
          theme={theme}
          caption="Active Workflows"
          loading={loading}
        />

        <DataTable
          columns={APPLICANT_COLUMNS}
          rows={[]}
          theme={theme}
          caption="Empty State Example"
          emptyMessage="No applicants match the current filter."
        />
      </div>
    </div>
  );
}
