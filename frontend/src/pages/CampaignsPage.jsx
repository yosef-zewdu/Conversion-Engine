import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';
import Spinner from '../components/Spinner';

const SEGMENT_OPTIONS = ['S1', 'S2', 'S3', 'S4'];

export default function CampaignsPage() {
  const navigate = useNavigate();
  const [form, setForm] = useState({
    campaign_id: 'series-ab-ai-data-capacity',
    target_segments: ['S1', 'S4'],
    limit: 10,
    mode: 'staff_sink',
    first_channel: 'email',
    auto_outreach: false,
  });
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [history, setHistory] = useState([]);

  useEffect(() => {
    api.listCampaigns().then(setHistory).catch(() => {});
  }, []);

  function toggleSegment(seg) {
    setForm((f) => ({
      ...f,
      target_segments: f.target_segments.includes(seg)
        ? f.target_segments.filter((s) => s !== seg)
        : [...f.target_segments, seg],
    }));
  }

  async function handleRun() {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await api.runCampaign(form);
      setResult(res);
      // Refresh history after starting a run
      api.listCampaigns().then(setHistory).catch(() => {});
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="max-w-3xl mx-auto animate-in fade-in slide-in-from-bottom-4 duration-700">
      <div className="flex items-center gap-3 mb-8">
        <div className="p-2 bg-indigo-600/10 rounded-lg ring-1 ring-indigo-500/20">
          <svg className="w-5 h-5 text-indigo-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M11 5.882V19.24a1.76 1.76 0 01-3.417.592l-2.147-6.15M18 13a3 3 0 100-6M5.436 13.683A4.001 4.001 0 017 6h1.832c4.1 0 7.625-1.234 9.168-3v14c-1.543-1.766-5.067-3-9.168-3H7a3.988 3.988 0 01-1.564-.317z" />
          </svg>
        </div>
        <h1 className="text-3xl font-bold tracking-tight text-white font-sans">Campaigns</h1>
      </div>

      <div className="glass-card p-8 space-y-8">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
          <div className="space-y-6">
            <h2 className="text-lg font-semibold text-slate-100 flex items-center gap-2">
              <span className="w-1.5 h-6 bg-indigo-500 rounded-full"></span>
              New Campaign Configuration
            </h2>

            {/* Campaign ID */}
            <div className="space-y-2">
              <label className="text-[11px] font-bold text-slate-500 uppercase tracking-widest pl-1">Target Campaign ID</label>
              <input
                className="w-full bg-slate-950/50 border border-slate-700 rounded-xl px-4 py-3 text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500/50 transition-all"
                value={form.campaign_id}
                placeholder="e.g. series-ab-ai-data"
                onChange={(e) => setForm((f) => ({ ...f, campaign_id: e.target.value }))}
              />
            </div>

            {/* Mode & Channel */}
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <label className="text-[11px] font-bold text-slate-500 uppercase tracking-widest pl-1">Execution Mode</label>
                <select
                  className="w-full bg-slate-950/50 border border-slate-700 rounded-xl px-4 py-3 text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500/50 transition-all appearance-none"
                  value={form.mode}
                  onChange={(e) => setForm((f) => ({ ...f, mode: e.target.value }))}
                >
                  <option value="staff_sink">Staff Sink (safe)</option>
                  <option value="live">Live Production</option>
                </select>
              </div>
              <div className="space-y-2">
                <label className="text-[11px] font-bold text-slate-500 uppercase tracking-widest pl-1">Autonomy Level</label>
                <select
                  className="w-full bg-slate-950/50 border border-slate-700 rounded-xl px-4 py-3 text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500/50 transition-all appearance-none"
                  value={form.auto_outreach}
                  onChange={(e) => setForm((f) => ({ ...f, auto_outreach: e.target.value === 'true' }))}
                >
                  <option value="false">Human-in-the-Loop</option>
                  <option value="true">Fully Autonomous</option>
                </select>
                <p className="text-[10px] text-slate-500 italic px-1">
                  {form.auto_outreach ? 'Automatically sends messages after qualification' : 'Wait for manual approval before sending'}
                </p>
              </div>
            </div>
          </div>

          <div className="space-y-6">
            <h2 className="text-lg font-semibold text-slate-100 flex items-center gap-2">
               <span className="w-1.5 h-6 bg-purple-500 rounded-full"></span>
               Targeting & Limits
            </h2>

            {/* Target segments */}
            <div className="space-y-3">
              <label className="text-[11px] font-bold text-slate-500 uppercase tracking-widest pl-1">Priority Segments</label>
              <div className="flex gap-2 flex-wrap">
                {SEGMENT_OPTIONS.map((seg) => (
                  <button
                    key={seg}
                    onClick={() => toggleSegment(seg)}
                    className={`px-4 py-2 rounded-lg text-sm font-bold transition-all duration-200 border ${
                      form.target_segments.includes(seg)
                        ? 'bg-indigo-600 border-indigo-500 text-white shadow-lg shadow-indigo-500/20'
                        : 'bg-slate-950/50 border-slate-700 text-slate-400 hover:border-slate-500'
                    }`}
                  >
                    {seg}
                  </button>
                ))}
              </div>
              <p className="text-[10px] text-slate-500 italic px-1">
                {form.target_segments.length === 0 ? 'Select at least one segment' : `Targeting ${form.target_segments.join(', ')}`}
              </p>
            </div>

            {/* Limit */}
            <div className="space-y-2">
              <label className="text-[11px] font-bold text-slate-500 uppercase tracking-widest pl-1">Max Accounts to Enrich</label>
              <div className="flex items-center gap-4">
                <input
                  type="range"
                  min="1"
                  max="100"
                  step="1"
                  className="flex-1 accent-indigo-500 h-1.5 bg-slate-700 rounded-lg appearance-none cursor-pointer"
                  value={form.limit}
                  onInput={(e) => {
                    const val = Number(e.target.value);
                    setForm(f => ({ ...f, limit: val }));
                  }}
                />
                <span className="w-12 text-center text-sm font-mono font-bold text-indigo-400 bg-indigo-500/10 py-1 rounded border border-indigo-500/20">
                  {form.limit}
                </span>
              </div>
            </div>
          </div>
        </div>

        <div className="pt-4 flex items-center justify-between border-t border-slate-800">
           <div className="flex items-center gap-2 text-xs text-slate-500">
              <svg className="w-4 h-4 text-amber-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
              {form.mode === 'live' ? 'Warning: Production delivery enabled' : 'Ready to test with Staff Sink'}
           </div>
           <button
            onClick={handleRun}
            disabled={loading || form.target_segments.length === 0}
            className="btn-primary min-w-[160px] flex items-center justify-center gap-2"
          >
            {loading ? <Spinner size="sm" /> : <span>Start Signal Enrichment</span>}
          </button>
        </div>

        {error && (
          <div className="bg-red-500/10 border border-red-500/20 text-red-400 rounded-xl px-4 py-3 text-sm animate-shake">
            <span className="font-bold mr-2">Error:</span> {error}
          </div>
        )}
      </div>

      {result && (
        <div className="mt-8 bg-emerald-500/5 rounded-2xl border border-emerald-500/20 p-8 space-y-6 animate-in zoom-in-95 duration-500">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 bg-emerald-500/20 rounded-full flex items-center justify-center">
                <svg className="w-6 h-6 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7" />
                </svg>
              </div>
              <div>
                <h2 className="text-xl font-bold text-emerald-300">Campaign Synchronized</h2>
                <p className="text-xs text-emerald-500/70">Run ID: <span className="font-mono">{result.campaign_run_id}</span></p>
              </div>
            </div>
            <button
              onClick={() => navigate(`/campaigns/${result.campaign_run_id}/accounts`)}
              className="bg-emerald-500 hover:bg-emerald-400 text-slate-950 px-5 py-2.5 rounded-xl text-sm font-bold shadow-lg shadow-emerald-500/20 transition-all active:scale-95"
            >
              Analyze Results →
            </button>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <Stat label="Status" value={result.status} highlight="indigo" />
            <Stat label="Total Sample" value={result.candidate_count} />
            <Stat label="Qualified" value={result.qualified_count} highlight="emerald" />
            <Stat label="Sync Success" value={result.leads_registered} />
          </div>
        </div>
      )}
      {/* Campaign History */}
      {history.length > 0 && (
        <div className="mt-8 space-y-4">
          <h2 className="text-xs font-bold text-slate-500 uppercase tracking-widest px-1">Previous Runs</h2>
          <div className="glass-card overflow-hidden divide-y divide-slate-800/50">
            {history.map((run) => {
              const statusColor = run.status === 'complete' ? 'text-emerald-400' : run.status === 'running' ? 'text-indigo-400 animate-pulse' : run.status === 'failed' ? 'text-red-400' : 'text-slate-400';
              return (
                <div
                  key={run.id}
                  className="flex items-center gap-4 px-6 py-4 hover:bg-white/5 cursor-pointer transition-colors group"
                  onClick={() => navigate(`/campaigns/${run.id}/accounts`)}
                >
                  <div className="w-8 h-8 rounded-lg bg-indigo-500/10 flex items-center justify-center text-indigo-400">
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
                    </svg>
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                       <span className="text-[10px] font-black text-indigo-500 uppercase tracking-tighter">Campaign</span>
                       <div className="text-sm font-bold text-slate-100 group-hover:text-indigo-400 transition-colors truncate">{run.campaign_id}</div>
                    </div>
                    <div className="text-[10px] font-mono text-slate-600">Reference: {run.id}</div>
                  </div>
                  <div className="text-right space-y-1 shrink-0">
                    <div className={`text-[10px] font-bold uppercase tracking-widest ${statusColor}`}>{run.status}</div>
                    <div className="text-[10px] text-slate-500 font-medium">{run.qualified_count ?? 0} qualified accounts</div>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="p-2 rounded-lg bg-slate-800/50 group-hover:bg-indigo-500 group-hover:text-white transition-all text-slate-600">
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 5l7 7-7 7" />
                      </svg>
                    </div>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        if (confirm('Delete this campaign and all associated results?')) {
                          api.deleteCampaign(run.id).then(() => {
                            setHistory(prev => prev.filter(h => h.id !== run.id));
                          });
                        }
                      }}
                      className="p-2 rounded-lg bg-slate-800/50 hover:bg-red-500/20 hover:text-red-400 text-slate-600 transition-all border border-transparent hover:border-red-500/30"
                      title="Delete Campaign"
                    >
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                      </svg>
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, highlight }) {
  const colors = {
    indigo: 'text-indigo-400',
    emerald: 'text-emerald-400',
    default: 'text-slate-100'
  };
  
  return (
    <div className="bg-slate-900/50 border border-slate-800 rounded-xl p-4">
      <div className="text-[10px] text-slate-500 uppercase tracking-widest mb-1">{label}</div>
      <div className={`text-xl font-bold ${colors[highlight] || colors.default}`}>
        {value ?? '—'}
      </div>
    </div>
  );
}
