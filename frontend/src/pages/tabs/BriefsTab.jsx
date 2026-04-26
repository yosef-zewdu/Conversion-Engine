import { useEffect, useState } from 'react';
import { api } from '../../api';
import Spinner from '../../components/Spinner';

export default function BriefsTab({ leadId }) {
  const [briefs, setBriefs] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.getLeadBriefs(leadId)
      .then(setBriefs)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [leadId]);

  if (loading) return <div className="flex py-20 justify-center"><Spinner /></div>;
  if (error) return <div className="glass-card p-6 text-red-400 text-sm">{error}</div>;
  if (!briefs) return null;

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 animate-in fade-in slide-in-from-bottom-4 duration-700">
      <JsonCard title="ICP Classification Output" data={briefs.icp_result} icon="award" />
      <JsonCard title="Engine Bench Alignment" data={briefs.bench_match} icon="users" />
      <JsonCard title="Primary Hiring Signals" data={briefs.hiring_signal_brief} icon="lightning" />
      <JsonCard title="Competitive Landscape" data={briefs.competitor_gap_brief} icon="chart" />
    </div>
  );
}

function JsonCard({ title, data, icon }) {
  const [expanded, setExpanded] = useState(true);

  const icons = {
    award: <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12l2 2 4-4M7.835 4.697a3.42 3.42 0 001.946-.806 3.42 3.42 0 014.438 0 3.42 3.42 0 001.946.806 3.42 3.42 0 013.138 3.138 3.42 3.42 0 00.806 1.946 3.42 3.42 0 010 4.438 3.42 3.42 0 00-.806 1.946 3.42 3.42 0 01-3.138 3.138 3.42 3.42 0 00-1.946.806 3.42 3.42 0 01-4.438 0 3.42 3.42 0 00-1.946-.806 3.42 3.42 0 01-3.138-3.138 3.42 3.42 0 00-.806-1.946 3.42 3.42 0 010-4.438 3.42 3.42 0 00.806-1.946 3.42 3.42 0 013.138-3.138z" />,
    users: <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z" />,
    lightning: <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 10V3L4 14h7v7l9-11h-7z" />,
    chart: <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M11 3.055A9.001 9.001 0 1020.945 13H11V3.055z" />
  };

  return (
    <div className={`glass-card overflow-hidden h-fit transition-all duration-500 ${expanded ? 'opacity-100' : 'opacity-80'}`}>
      <button
        className="w-full flex items-center justify-between px-6 py-4 text-left transition-colors bg-slate-950/20"
        onClick={() => setExpanded((e) => !e)}
      >
        <div className="flex items-center gap-3">
          <div className="w-6 h-6 rounded bg-slate-900 flex items-center justify-center text-indigo-400">
             <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
               {icons[icon]}
             </svg>
          </div>
          <span className="text-[10px] font-bold text-slate-100 uppercase tracking-[0.2em]">{title}</span>
        </div>
        <div className="flex items-center gap-2">
           <span className="text-[9px] font-bold text-slate-500 uppercase tracking-widest">{expanded ? 'Collapse' : 'Expand'}</span>
           <div className={`transition-transform duration-300 ${expanded ? 'rotate-180' : 'rotate-0'}`}>
             <svg className="w-3 h-3 text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
               <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="3" d="M19 9l-7 7-7-7" />
             </svg>
           </div>
        </div>
      </button>
      
      {expanded && (
        <div className="px-6 py-5 bg-slate-950/40 animate-in slide-in-from-top-1 duration-300">
          {data ? (
            <pre className="text-[11px] font-mono leading-relaxed text-indigo-300/80 bg-slate-950/80 p-4 rounded-xl border border-slate-900/50 overflow-auto max-h-[400px] custom-scrollbar">
              {JSON.stringify(data, null, 2)}
            </pre>
          ) : (
            <div className="py-10 text-center">
               <p className="text-[10px] uppercase font-bold tracking-[0.3em] text-slate-700">Data Node Null</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
