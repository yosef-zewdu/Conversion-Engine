const SEGMENT_COLORS = {
  segment_1: 'bg-blue-900 text-blue-200',
  segment_1_series_a_b: 'bg-blue-900 text-blue-200',
  segment_2: 'bg-orange-900 text-orange-200',
  segment_2_mid_market_restructure: 'bg-orange-900 text-orange-200',
  segment_3: 'bg-purple-900 text-purple-200',
  segment_3_leadership_transition: 'bg-purple-900 text-purple-200',
  segment_4: 'bg-green-900 text-green-200',
  segment_4_specialized_capability: 'bg-green-900 text-green-200',
  unqualified: 'bg-slate-700 text-slate-400',
  abstain: 'bg-slate-700 text-slate-400',
};

const STATE_COLORS = {
  cold: 'bg-slate-700 text-slate-300',
  contacted: 'bg-blue-900 text-blue-200',
  replied: 'bg-yellow-900 text-yellow-200',
  warm: 'bg-green-900 text-green-200',
  booking: 'bg-indigo-900 text-indigo-200',
  dormant: 'bg-slate-800 text-slate-500',
  opted_out: 'bg-red-900 text-red-300',
};

const DECISION_COLORS = {
  qualified: 'bg-green-900 text-green-200',
  hold: 'bg-yellow-900 text-yellow-200',
  abstain: 'bg-slate-700 text-slate-400',
};

export function SegmentBadge({ segment }) {
  const cls = SEGMENT_COLORS[segment] || 'bg-slate-700 text-slate-400';
  const label = segment ? segment.replace('segment_', 'S').replace(/_.*/, '') : '—';
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-mono font-semibold ${cls}`}>
      {label}
    </span>
  );
}

export function StateBadge({ state }) {
  const cls = STATE_COLORS[state] || 'bg-slate-700 text-slate-400';
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-semibold ${cls}`}>
      {state || '—'}
    </span>
  );
}

export function DecisionBadge({ decision }) {
  const cls = DECISION_COLORS[decision] || 'bg-slate-700 text-slate-400';
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-semibold ${cls}`}>
      {decision || '—'}
    </span>
  );
}
