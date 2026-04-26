import { useEffect, useState, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { api } from '../api';
import { SegmentBadge, StateBadge, DecisionBadge } from '../components/Badge';
import Spinner from '../components/Spinner';

export default function AccountsPage() {
  const { campaignId } = useParams();
  const navigate = useNavigate();
  const [accounts, setAccounts] = useState([]);
  const [campaign, setCampaign] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const pollRef = useRef(null);

  useEffect(() => {
    async function load() {
      try {
        const [c, accs] = await Promise.all([
          api.getCampaign(campaignId),
          api.getCampaignAccounts(campaignId),
        ]);
        setCampaign(c);
        setAccounts(accs);
        // Auto-poll while the campaign is still running
        if (c?.status === 'running') {
          pollRef.current = setTimeout(load, 5000);
        }
      } catch (e) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
    }
    if (campaignId) load();
    return () => clearTimeout(pollRef.current);
  }, [campaignId]);

  if (loading) return <Loading />;
  if (error) return <ErrorBanner msg={error} />;

  return (
    <div className="space-y-8 animate-in fade-in duration-700">
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-6">
        <div className="space-y-4">
          <button
            onClick={() => navigate('/')}
            className="group flex items-center gap-2 text-slate-500 hover:text-indigo-400 text-xs font-bold uppercase tracking-widest transition-colors"
          >
            <svg className="w-4 h-4 group-hover:-translate-x-1 transition-transform" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
            </svg>
            Back to Campaigns
          </button>
          <div className="space-y-1">
            <h1 className="text-3xl font-bold text-white tracking-tight">
              Ranked Accounts
            </h1>
            <p className="text-slate-500 text-sm">
              Campaign Identifier: <span className="text-indigo-400 font-mono">{campaign?.campaign_id}</span>
            </p>
          </div>
        </div>

        {campaign && (
          <div className="flex gap-2">
            <CampaignStat label="Candidates" value={campaign.candidate_count} />
            <CampaignStat label="Qualified" value={campaign.qualified_count} highlight="emerald" />
            <CampaignStat label="Status" value={campaign.status} highlight={campaign.status === 'complete' ? 'emerald' : campaign.status === 'failed' ? 'red' : 'indigo'} />
          </div>
        )}
      </div>

      {/* Live Progress Banner */}
      {campaign?.status === 'running' && (
        <div className="flex items-center gap-4 px-5 py-3 bg-indigo-500/10 border border-indigo-500/20 rounded-xl animate-pulse">
          <Spinner size="sm" />
          <div>
            <div className="text-xs font-bold text-indigo-300">Campaign Running</div>
            <div className="text-[10px] text-indigo-400/70">Leads are being enriched and will appear below automatically. No need to refresh.</div>
          </div>
          <span className="ml-auto text-[10px] font-mono text-indigo-400">{accounts.length} found so far</span>
        </div>
      )}

      {accounts.length === 0 ? (
        <div className="glass-card py-20 text-center space-y-4">
           <div className="w-16 h-16 bg-slate-950 rounded-full flex items-center justify-center mx-auto border border-slate-800">
             <svg className="w-8 h-8 text-slate-700" fill="none" stroke="currentColor" viewBox="0 0 24 24">
               <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0a2 2 0 01-2 2H6a2 2 0 01-2-2m16 0l-1 1m-14-1l1 1m5 4v1m8-1l-1 1m-10-1l1 1" />
             </svg>
           </div>
           <p className="text-slate-400 font-medium italic">
             {campaign?.status === 'running' ? 'Engine is processing candidates...' : 'No accounts synchronized for this campaign run.'}
           </p>
        </div>
      ) : (
        <div className="glass-card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-left border-collapse">
              <thead>
                <tr className="bg-slate-950/50 border-b border-slate-800">
                  <th className="px-6 py-4 text-[10px] font-bold text-slate-500 uppercase tracking-widest">Company</th>
                  <th className="px-6 py-4 text-[10px] font-bold text-slate-500 uppercase tracking-widest">Segment</th>
                  <th className="px-6 py-4 text-[10px] font-bold text-slate-500 uppercase tracking-widest text-center">AI Maturity</th>
                  <th className="px-6 py-4 text-[10px] font-bold text-slate-500 uppercase tracking-widest text-center">Velocity</th>
                  <th className="px-6 py-4 text-[10px] font-bold text-slate-500 uppercase tracking-widest text-center">Bench</th>
                  <th className="px-6 py-4 text-[10px] font-bold text-slate-500 uppercase tracking-widest text-center">State</th>
                  <th className="px-6 py-4 text-[10px] font-bold text-slate-500 uppercase tracking-widest text-center">Decision</th>
                  <th className="px-6 py-4"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/50">
                {accounts.map((acc) => (
                  <tr
                    key={acc.id}
                    className="group hover:bg-white/5 cursor-pointer transition-colors border-b border-slate-800/30"
                    onClick={() => navigate(`/leads/${acc.id}`)}
                  >
                    <td className="px-6 py-4">
                      <div className="font-bold text-slate-100 group-hover:text-indigo-400 transition-colors">{acc.company_name}</div>
                      <div className="text-[10px] text-slate-500 font-mono tracking-tighter">{acc.id}</div>
                    </td>
                    <td className="px-6 py-4">
                      <SegmentBadge segment={acc.segment} />
                    </td>
                    <td className="px-6 py-4 text-center">
                      <AiScore score={acc.ai_maturity_score} />
                    </td>
                    <td className="px-6 py-4 text-center">
                      <span className="font-mono text-[10px] text-slate-400 bg-slate-950 px-2 py-1 rounded border border-slate-800 group-hover:border-slate-700 transition-colors">
                        {acc.job_velocity || 'Stable'}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-center">
                      {acc.bench_mismatch === null || acc.bench_mismatch === undefined ? (
                        <span className="text-slate-700">—</span>
                      ) : acc.bench_mismatch ? (
                        <span className="w-2 h-2 rounded-full bg-amber-500 inline-block shadow-lg shadow-amber-500/20" title="Bench Mismatch"></span>
                      ) : (
                        <span className="w-2 h-2 rounded-full bg-emerald-500 inline-block shadow-lg shadow-emerald-500/20" title="Bench Match"></span>
                      )}
                    </td>
                    <td className="px-6 py-4 text-center">
                      <StateBadge state={acc.current_state} />
                    </td>
                    <td className="px-6 py-4 text-center">
                      <DecisionBadge decision={acc.decision} />
                    </td>
                    <td className="px-6 py-4 text-right">
                      <button className="px-3 py-1.5 rounded-lg border border-slate-800 text-[10px] font-bold text-slate-500 uppercase tracking-widest hover:border-indigo-500/50 hover:text-indigo-400 hover:bg-indigo-500/5 transition-all">
                        View Profile
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function CampaignStat({ label, value, highlight }) {
  const colors = {
    indigo: 'text-indigo-400 ring-indigo-500/20 bg-indigo-500/5',
    emerald: 'text-emerald-400 ring-emerald-500/20 bg-emerald-500/5',
    default: 'text-slate-400 ring-slate-800 bg-slate-950/50'
  };
  return (
    <div className={`px-4 py-2 rounded-xl ring-1 border-none flex flex-col items-center min-w-[100px] ${colors[highlight] || colors.default}`}>
      <span className="text-[9px] font-bold uppercase tracking-widest opacity-60 mb-0.5">{label}</span>
      <span className="text-sm font-bold">{value ?? '—'}</span>
    </div>
  );
}

function AiScore({ score }) {
  if (score === null || score === undefined) return <span className="text-slate-700">—</span>;
  const bars = [1, 2, 3];
  return (
    <div className="flex gap-1 justify-center items-end h-3">
      {bars.map((b) => (
        <div
          key={b}
          className={`w-1 rounded-t-full transition-all ${
            b <= score 
              ? (score === 3 ? 'bg-emerald-500 h-3' : score === 2 ? 'bg-amber-500 h-2' : 'bg-orange-500 h-1') 
              : 'bg-slate-800 h-1'
          }`}
        />
      ))}
    </div>
  );
}

function Loading() {
  return (
    <div className="flex flex-col items-center justify-center py-32 space-y-4">
      <Spinner size="lg" />
      <span className="text-xs font-bold text-slate-500 uppercase tracking-widest animate-pulse">Loading Ranked Accounts...</span>
    </div>
  );
}

function ErrorBanner({ msg }) {
  return (
    <div className="bg-red-500/10 border border-red-500/20 text-red-400 rounded-2xl px-6 py-4 flex items-center gap-4">
      <svg className="w-6 h-6 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
      </svg>
      <div className="flex-1">
        <div className="font-bold">Sync Error</div>
        <div className="text-sm opacity-80">{msg}</div>
      </div>
    </div>
  );
}
