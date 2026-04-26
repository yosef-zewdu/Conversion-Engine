import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { api } from '../api';
import { SegmentBadge, StateBadge } from '../components/Badge';
import Spinner from '../components/Spinner';
import BriefsTab from './tabs/BriefsTab';
import ConversationTab from './tabs/ConversationTab';
import EvidenceTab from './tabs/EvidenceTab';

const TABS = ['Overview', 'Briefs', 'Conversation', 'Evidence'];

export default function LeadDetailPage() {
  const { leadId } = useParams();
  const navigate = useNavigate();
  const [lead, setLead] = useState(null);
  const [activeTab, setActiveTab] = useState('Overview');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [outreachLoading, setOutreachLoading] = useState(false);
  const [outreachResult, setOutreachResult] = useState(null);

  useEffect(() => {
    async function load() {
      try {
        const l = await api.getLead(leadId);
        setLead(l);
      } catch (e) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
    }
    if (leadId) load();
  }, [leadId]);

  async function handleStartOutreach() {
    setOutreachLoading(true);
    setOutreachResult(null);
    try {
      const res = await api.startOutreach(leadId, {
        inbound_text: "Hello, I'm reaching out to learn more about your engineering services.",
        channel: 'email',
      });
      setOutreachResult(res);
      const updated = await api.getLead(leadId);
      setLead(updated);
    } catch (e) {
      setOutreachResult({ error: e.message });
    } finally {
      setOutreachLoading(false);
    }
  }

  if (loading) return <LoadingState />;
  if (error) return <ErrorState msg={error} />;
  if (!lead) return null;

  return (
    <div className="max-w-6xl mx-auto space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-700">
      {/* Breadcrumb */}
      <nav className="flex items-center gap-3 text-[11px] font-bold uppercase tracking-widest text-slate-500">
        <button onClick={() => navigate('/')} className="hover:text-indigo-400 transition-colors">Campaigns</button>
        <span className="opacity-30">/</span>
        {lead.campaign_id && (
          <>
            <button
              onClick={() => navigate(`/campaigns/${lead.campaign_id}/accounts`)}
              className="hover:text-indigo-400 transition-colors"
            >
              Accounts
            </button>
            <span className="opacity-30">/</span>
          </>
        )}
        <span className="text-slate-300">{lead.company_name}</span>
      </nav>

      {/* Hero Header */}
      <div className="glass-card p-8 group">
        <div className="flex flex-col md:flex-row md:items-start justify-between gap-8">
          <div className="space-y-6">
            <div className="space-y-2">
              <h1 className="text-4xl font-extrabold text-white tracking-tight leading-tight">
                {lead.company_name}
              </h1>
              <div className="flex items-center gap-3 flex-wrap">
                <SegmentBadge segment={lead.segment} />
                <StateBadge state={lead.current_state} />
                <div className="h-4 w-px bg-slate-800 mx-1"></div>
                <div className="flex items-center gap-2 px-2.5 py-1 bg-slate-950 rounded-lg border border-slate-800">
                  <span className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">Confidence</span>
                  <span className="text-xs font-mono font-bold text-indigo-400">
                    {(lead.icp_confidence * 100).toFixed(0)}%
                  </span>
                </div>
              </div>
            </div>

            <div className="grid grid-cols-2 lg:grid-cols-4 gap-6 pt-2">
               <HeroStat label="Decision Maker" value={lead.contact_name} icon="person" />
               <HeroStat label="Platform State" value={lead.current_state} icon="activity" />
               <HeroStat label="CRM Sync" value={lead.hs_contact_id ? 'HubSpot Linked' : 'Not Linked'} icon="database" />
               <HeroStat label="Attempts" value={`${lead.outbound_attempt_count} Sequence(s)`} icon="send" />
            </div>
          </div>

          <div className="flex flex-col items-end gap-4 min-w-[240px]">
             <button
                onClick={handleStartOutreach}
                disabled={outreachLoading || lead.current_state !== 'cold'}
                className={`w-full btn-primary flex items-center justify-center gap-3 py-4 shadow-2xl ${
                  lead.current_state === 'cold' ? 'bg-indigo-600' : 'bg-slate-800 border-slate-700 opacity-60 text-slate-400'
                }`}
              >
                {outreachLoading ? <Spinner size="sm" /> : (
                   <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                     <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 10V3L4 14h7v7l9-11h-7z" />
                   </svg>
                )}
                <span className="text-sm font-bold uppercase tracking-wider">
                  {lead.current_state === 'cold' ? 'Trigger Agent' : 'Agent Active'}
                </span>
              </button>
              
              {outreachResult && (
                <div className={`w-full p-4 rounded-xl text-xs font-medium animate-in zoom-in-95 ${outreachResult.error ? 'bg-red-500/10 text-red-400 border border-red-500/20' : 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'}`}>
                  {outreachResult.error ? outreachResult.error : 'Outreach sequence triggered successfully'}
                </div>
              )}
          </div>
        </div>
      </div>

      {/* Tab Navigation */}
      <div className="flex items-center gap-1 p-1 bg-slate-950/50 backdrop-blur-sm border border-slate-900 rounded-2xl w-fit">
        {TABS.map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`nav-link px-6 py-2 ${
              activeTab === tab ? 'nav-link-active' : 'nav-link-inactive'
            }`}
          >
            {tab}
          </button>
        ))}
      </div>

      {/* Tab Content Area */}
      <div className="min-h-[400px]">
        {activeTab === 'Overview' && <OverviewTab lead={lead} />}
        {activeTab === 'Briefs' && <BriefsTab leadId={leadId} />}
        {activeTab === 'Conversation' && <ConversationTab leadId={leadId} lead={lead} />}
        {activeTab === 'Evidence' && <EvidenceTab leadId={leadId} />}
      </div>
    </div>
  );
}

function HeroStat({ label, value, icon }) {
  const icons = {
    person: <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />,
    activity: <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />,
    database: <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 7v10c0 2.21 3.58 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.58 4 8 4s8-1.79 8-4M4 7c0-2.21 3.58-4 8-4s8 1.79 8 4m0 5c0 2.21-3.58 4-8 4s-8-1.79-8-4" />,
    send: <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
  };

  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2 text-[10px] font-bold text-slate-500 uppercase tracking-widest">
        <svg className="w-3.5 h-3.5 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          {icons[icon]}
        </svg>
        {label}
      </div>
      <div className="text-sm font-semibold text-slate-200 truncate pr-4">{value || '—'}</div>
    </div>
  );
}

function OverviewTab({ lead }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 animate-in fade-in slide-in-from-bottom-2 duration-500">
      <InfoCard title="Enrichment Core" icon="sparkles">
        <KV k="ICP Segment" v={lead.segment} highlight />
        <KV k="ICP Confidence" v={lead.icp_confidence ? `${(lead.icp_confidence * 100).toFixed(0)}%` : '—'} />
        <KV k="AI Maturity" v={`${lead.ai_maturity_score ?? '—'} / 3`} />
        <KV k="Bench Fit" v={lead.bench_mismatch === false ? 'Optimal' : lead.bench_mismatch === true ? 'Mismatch' : 'Unknown'} />
      </InfoCard>

      <InfoCard title="Contact Intel" icon="mail">
        <KV k="Lead Name" v={lead.contact_name} />
        <KV k="Email Addr" v={lead.email} mono />
        <KV k="Phone" v={lead.phone || '—'} />
        <KV k="Prefer. Ch." v={lead.preferred_channel} highlight />
        <KV k="Timezone" v={lead.timezone} />
      </InfoCard>

      <InfoCard title="Infrastructure" icon="server">
        <KV k="FSM State" v={lead.current_state} highlight />
        <KV k="HubSpot Ref" v={lead.hs_contact_id || '—'} mono />
        <KV k="Cal.com Event" v={lead.cal_event_id || '—'} mono />
        <KV k="Created" v={new Date(lead.created_at).toLocaleDateString()} />
      </InfoCard>

      <div className="md:col-span-2 lg:col-span-3">
        <InfoCard title="Source Evidence Tracking" icon="link">
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-4 mt-2">
            {lead.source_refs ? Object.entries(lead.source_refs).map(([k, v]) => (
              <div key={k} className="bg-slate-950/50 p-3 rounded-xl border border-slate-800 group hover:border-indigo-500/30 transition-colors">
                <div className="text-[9px] font-bold text-slate-500 uppercase tracking-widest mb-1 group-hover:text-indigo-400">{k}</div>
                <div className="text-xs font-mono text-slate-300 truncate">{String(v)}</div>
              </div>
            )) : <div className="col-span-full py-4 text-center text-slate-600 italic text-xs">No primary source references recorded in current trace.</div>}
          </div>
        </InfoCard>
      </div>
    </div>
  );
}

function InfoCard({ title, children, icon }) {
  const icons = {
    sparkles: <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-7.714 2.143L11 21l-2.286-6.857L1 12l7.714-2.143L11 3z" />,
    mail: <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M16 12a4 4 0 10-8 0 4 4 0 008 0zm0 0v1.5a2.5 2.5 0 005 0V12a9 9 0 10-9 9m4.5-1.206a8.959 8.959 0 01-4.5 1.206" />,
    server: <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 12h14M5 12a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v4a2 2 0 01-2 2M5 12a2 2 0 00-2 2v4a2 2 0 002 2h14a2 2 0 002-2v4a2 2 0 00-2-2" />,
    link: <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.828a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
  };

  return (
    <div className="glass-card p-6 h-full flex flex-col">
      <div className="flex items-center gap-3 mb-6">
        <div className="w-8 h-8 rounded-lg bg-indigo-500/10 flex items-center justify-center text-indigo-400 border border-indigo-500/20">
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            {icons[icon]}
          </svg>
        </div>
        <h3 className="text-xs font-bold text-slate-100 uppercase tracking-widest">{title}</h3>
      </div>
      <div className="space-y-4 flex-1">{children}</div>
    </div>
  );
}

function KV({ k, v, mono, highlight }) {
  return (
    <div className="flex justify-between items-center text-xs pb-3 border-b border-slate-800/50 last:border-0 last:pb-0">
      <span className="text-slate-500 font-medium">{k}</span>
      <span className={`text-right truncate max-w-[180px] ${highlight ? 'text-indigo-400 font-bold' : 'text-slate-200'} ${mono ? 'font-mono text-[10px]' : ''}`}>
        {v ?? '—'}
      </span>
    </div>
  );
}

function LoadingState() {
  return (
    <div className="flex flex-col items-center justify-center py-32 space-y-4">
      <Spinner size="lg" />
      <span className="text-xs font-bold text-slate-500 uppercase tracking-widest animate-pulse">Retreiving Lead Intelligence...</span>
    </div>
  );
}

function ErrorState({ msg }) {
  return (
    <div className="bg-red-500/10 border border-red-500/20 text-red-400 rounded-2xl px-6 py-4">
       <h4 className="font-bold mb-1">Access Failure</h4>
       <p className="text-sm opacity-80">{msg}</p>
    </div>
  );
}
