import { useEffect, useState } from 'react';
import { api } from '../../api';
import Spinner from '../../components/Spinner';

export default function EvidenceTab({ leadId }) {
  const [traces, setTraces] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.getLeadTraces(leadId)
      .then(setTraces)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [leadId]);

  if (loading) return <div className="flex py-20 justify-center"><Spinner /></div>;
  if (error) return <div className="glass-card p-6 text-red-400 text-sm">{error}</div>;

  if (traces.length === 0) {
    return (
      <div className="glass-card py-24 text-center space-y-4">
        <div className="w-16 h-16 bg-slate-950 rounded-full flex items-center justify-center mx-auto border border-slate-900 ring-8 ring-slate-900/50">
           <svg className="w-8 h-8 text-slate-800" fill="none" stroke="currentColor" viewBox="0 0 24 24">
             <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
           </svg>
        </div>
        <div className="space-y-1">
          <p className="text-slate-500 font-bold uppercase tracking-[0.2em] text-[10px]">Registry Empty</p>
          <p className="text-slate-600 italic text-xs">No observability traces have been emitted for this lead.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-700">
      <div className="flex items-center justify-between px-2">
        <h3 className="text-[10px] font-bold text-slate-500 uppercase tracking-[0.3em]">
          Trace Log Summary ({traces.length} Nodes)
        </h3>
        <div className="h-px flex-1 mx-6 bg-slate-900"></div>
      </div>
      {traces.map((t, idx) => (
        <TraceCard key={t.id} trace={t} index={traces.length - idx} />
      ))}
    </div>
  );
}

function TraceCard({ trace, index }) {
  const [expanded, setExpanded] = useState(false);
  const hasViolation = trace.bench_overcommitment || trace.pricing_violation || trace.unsupported_claim_count > 0;

  return (
    <div className={`glass-card overflow-hidden transition-all duration-300 ${hasViolation ? 'border-red-500/30 ring-1 ring-red-500/10' : ''} ${expanded ? 'shadow-2xl ring-2 ring-indigo-500/20 shadow-indigo-500/10' : ''}`}>
      <div
        className="px-6 py-4 flex items-center gap-6 cursor-pointer hover:bg-white/5 transition-colors"
        onClick={() => setExpanded((e) => !e)}
      >
        <div className="flex flex-col">
           <span className="text-[9px] font-bold text-slate-600 uppercase tracking-widest mb-0.5">Instance #{index}</span>
           <span className="font-mono text-[10px] font-bold text-indigo-400 truncate max-w-[140px]">{trace.id.split('-').pop()}</span>
        </div>

        <div className="hidden md:flex flex-col">
           <span className="text-[9px] font-bold text-slate-600 uppercase tracking-widest mb-0.5">Engine Model</span>
           <span className="text-[11px] font-bold text-slate-300">{trace.model ? trace.model.split('/').pop() : 'Direct-FSM'}</span>
        </div>

        <div className="flex flex-col items-center">
           <span className="text-[9px] font-bold text-slate-600 uppercase tracking-widest mb-0.5">Cost</span>
           <span className="text-[11px] font-mono font-bold text-slate-200">${trace.cost_usd ? trace.cost_usd.toFixed(4) : '0.0000'}</span>
        </div>

        <div className="flex flex-col items-center">
           <span className="text-[9px] font-bold text-slate-600 uppercase tracking-widest mb-0.5">Latency</span>
           <span className="text-[11px] font-mono font-bold text-slate-200">{trace.latency_seconds ? trace.latency_seconds.toFixed(2) : '--'}s</span>
        </div>

        <div className="ml-auto flex items-center gap-3">
           {hasViolation && (
             <div className="flex items-center gap-1.5 px-3 py-1 bg-red-500/10 border border-red-500/20 rounded-full">
               <div className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse"></div>
               <span className="text-[9px] font-bold text-red-500 uppercase tracking-widest">Policy Violation</span>
             </div>
           )}
           <div className={`px-3 py-1 rounded-full border text-[9px] font-bold uppercase tracking-widest ${trace.destination === 'staff_sink' ? 'bg-slate-950 border-slate-800 text-slate-500' : 'bg-emerald-500/10 border-emerald-500/20 text-emerald-500'}`}>
              {trace.destination || 'Internal'}
           </div>
           <div className={`transition-transform duration-300 ${expanded ? 'rotate-180' : 'rotate-0'}`}>
              <svg className="w-4 h-4 text-slate-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 9l-7 7-7-7" />
              </svg>
           </div>
        </div>
      </div>

      {expanded && (
        <div className="px-6 py-6 border-t border-slate-900 bg-slate-950/30 space-y-6 animate-in slide-in-from-top-1">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
             <MetricCard label="Prompt Tokens" value={trace.prompt_tokens ?? '0'} />
             <MetricCard label="Compl. Tokens" value={trace.completion_tokens ?? '0'} />
             <MetricCard label="Policy Score" value={hasViolation ? 'Action Blocked' : 'Pass'} alert={hasViolation} />
             <MetricCard label="CRM Sync" value={trace.hs_status || 'Offline'} />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <CodeSection title="Policy Decision Logic" data={trace.policy_decision} />
            <CodeSection title="Runtime Trace Metadata" data={trace.source_refs} />
          </div>
        </div>
      )}
    </div>
  );
}

function MetricCard({ label, value, alert }) {
  return (
    <div className="bg-slate-950/50 p-3 rounded-xl border border-slate-900">
      <div className="text-[9px] font-bold text-slate-600 uppercase tracking-widest mb-1">{label}</div>
      <div className={`text-xs font-bold ${alert ? 'text-red-400' : 'text-slate-100'}`}>{value}</div>
    </div>
  );
}

function CodeSection({ title, data }) {
  return (
    <div className="space-y-2">
      <div className="text-[9px] font-bold text-slate-500 uppercase tracking-[0.2em]">{title}</div>
      <pre className="p-4 bg-slate-950 border border-slate-800 rounded-xl text-[10px] font-mono leading-relaxed text-indigo-300/60 overflow-auto max-h-48 custom-scrollbar whitespace-pre-wrap">
        {data ? JSON.stringify(data, null, 2) : '// No metadata emitted'}
      </pre>
    </div>
  );
}
