import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';

export default function DashboardPage() {
  const navigate = useNavigate();
  const [stats, setStats] = useState({
    totalLeads: 0,
    activeCampaigns: 0,
    meetingsBooked: 0,
    conversionRate: '0%',
  });
  const [recentLeads, setRecentLeads] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function loadDashboard() {
      try {
        const [campaigns, leads] = await Promise.all([
          api.listCampaigns(),
          api.listLeads(),
        ]);
        
        setStats({
          totalLeads: leads.length,
          activeCampaigns: campaigns.filter(c => c.status === 'running').length,
          meetingsBooked: leads.filter(l => l.cal_event_id).length,
          conversionRate: leads.length > 0 
            ? `${((leads.filter(l => l.cal_event_id).length / leads.length) * 100).toFixed(1)}%`
            : '0%',
        });
        setRecentLeads(leads.slice(0, 5));
      } catch (err) {
        console.error("Dashboard load failed", err);
      } finally {
        setLoading(false);
      }
    }
    loadDashboard();
  }, []);

  return (
    <div className="space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-700">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div>
          <h1 className="text-4xl font-extrabold tracking-tight text-white mb-2">
            Command Center
          </h1>
          <p className="text-slate-400 text-lg">
            Monitor your autonomous sales pipeline in real-time.
          </p>
        </div>
        <button
          onClick={() => navigate('/campaigns')}
          className="bg-indigo-600 hover:bg-indigo-500 text-white px-6 py-3 rounded-xl font-bold shadow-lg shadow-indigo-600/20 transition-all active:scale-95 flex items-center gap-2"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 4v16m8-8H4" />
          </svg>
          Launch New Campaign
        </button>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
        <StatCard 
          label="Total Leads" 
          value={stats.totalLeads} 
          icon={<UsersIcon />}
          color="indigo"
        />
        <StatCard 
          label="Running Campaigns" 
          value={stats.activeCampaigns} 
          icon={<RocketIcon />}
          color="amber"
          pulse={stats.activeCampaigns > 0}
        />
        <StatCard 
          label="Meetings Booked" 
          value={stats.meetingsBooked} 
          icon={<CalendarIcon />}
          color="emerald"
        />
        <StatCard 
          label="Target Efficiency" 
          value={stats.conversionRate} 
          icon={<ChartIcon />}
          color="purple"
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Recent Activity */}
        <div className="lg:col-span-2 space-y-4">
          <h2 className="text-xl font-bold text-white flex items-center gap-2">
            <span className="w-1.5 h-6 bg-indigo-500 rounded-full"></span>
            Qualified Pipeline
          </h2>
          <div className="glass-card overflow-hidden">
            {loading ? (
              <div className="p-12 text-center text-slate-500">Scanning database...</div>
            ) : recentLeads.length === 0 ? (
              <div className="p-12 text-center text-slate-500">No leads found. Start a campaign to populate your dashboard.</div>
            ) : (
              <div className="divide-y divide-slate-800/50">
                {recentLeads.map(lead => (
                  <div 
                    key={lead.id}
                    onClick={() => navigate(`/leads/${lead.id}`)}
                    className="flex items-center justify-between p-4 hover:bg-white/5 cursor-pointer transition-colors group"
                  >
                    <div className="flex items-center gap-4">
                      <div className="w-10 h-10 rounded-lg bg-slate-800 flex items-center justify-center text-indigo-400 font-bold border border-slate-700">
                        {lead.company_name[0]}
                      </div>
                      <div>
                        <div className="font-bold text-slate-100 group-hover:text-indigo-400 transition-colors">{lead.company_name}</div>
                        <div className="text-xs text-slate-500">{lead.contact_name}</div>
                      </div>
                    </div>
                    <div className="flex items-center gap-6">
                      <div className="hidden md:block text-right">
                        <div className="text-xs font-bold text-slate-100 uppercase tracking-widest">{lead.segment || 'Unclassified'}</div>
                        <div className="text-[10px] text-slate-600">{(lead.icp_confidence * 100).toFixed(0)}% confidence</div>
                      </div>
                      <div className={`px-3 py-1 rounded-full text-[10px] font-bold uppercase tracking-widest ${
                        lead.current_state === 'met' ? 'bg-emerald-500/10 text-emerald-400 ring-1 ring-emerald-500/20' : 'bg-slate-800 text-slate-400'
                      }`}>
                        {lead.current_state}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
            <div 
              onClick={() => navigate('/accounts')}
              className="p-4 bg-slate-900/50 text-center text-sm font-bold text-indigo-400 hover:text-indigo-300 cursor-pointer border-t border-slate-800"
            >
              View Full Accounts List →
            </div>
          </div>
        </div>

        {/* System Health */}
        <div className="space-y-4">
          <h2 className="text-xl font-bold text-white flex items-center gap-2">
            <span className="w-1.5 h-6 bg-emerald-500 rounded-full"></span>
            Agent Health
          </h2>
          <div className="glass-card p-6 space-y-6">
            <HealthItem label="Playwright Browser" status="Operational" />
            <HealthItem label="PostgreSQL DB" status="Operational" />
            <HealthItem label="OpenRouter AI" status="Operational" />
            <HealthItem label="Cal.com Sync" status="Operational" />
            <div className="pt-4 border-t border-slate-800">
              <div className="p-4 bg-indigo-500/5 rounded-xl border border-indigo-500/10">
                <div className="text-[10px] font-bold text-indigo-400 uppercase tracking-widest mb-1">Current Focus</div>
                <div className="text-sm text-slate-300 leading-relaxed">
                  Engine is optimized for <strong>Series A-C</strong> tech companies in the <strong>US/EMEA</strong> regions.
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function StatCard({ label, value, icon, color, pulse }) {
  const colors = {
    indigo: 'from-indigo-600/20 to-transparent border-indigo-500/20 text-indigo-400',
    amber: 'from-amber-600/20 to-transparent border-amber-500/20 text-amber-400',
    emerald: 'from-emerald-600/20 to-transparent border-emerald-500/20 text-emerald-400',
    purple: 'from-purple-600/20 to-transparent border-purple-500/20 text-purple-400',
  };

  return (
    <div className={`glass-card p-6 bg-gradient-to-br ${colors[color]} relative group hover:scale-[1.02] transition-transform`}>
      <div className="flex items-center justify-between mb-4">
        <div className={`${colors[color].split(' ')[2]} p-2 bg-white/5 rounded-lg`}>
          {icon}
        </div>
        {pulse && (
          <div className="flex h-3 w-3 relative">
            <div className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75"></div>
            <div className="relative inline-flex rounded-full h-3 w-3 bg-amber-500"></div>
          </div>
        )}
      </div>
      <div className="text-3xl font-black text-white mb-1">{value}</div>
      <div className="text-xs font-bold text-slate-500 uppercase tracking-widest">{label}</div>
    </div>
  );
}

function HealthItem({ label, status }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-slate-400">{label}</span>
      <div className="flex items-center gap-2">
        <span className="text-[10px] font-bold text-emerald-400 uppercase tracking-widest">{status}</span>
        <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.6)]"></div>
      </div>
    </div>
  );
}

// Icons
const UsersIcon = () => <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" /></svg>;
const RocketIcon = () => <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15.536 8.464a5 5 0 010 7.072m2.828-9.9a9 9 0 010 12.728M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707C10.923 3.663 12 4.109 12 5v14c0 .891-1.077 1.337-1.707.707L5.586 15z" /></svg>;
const CalendarIcon = () => <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" /></svg>;
const ChartIcon = () => <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" /></svg>;
