import { useEffect, useState } from 'react';
import { api } from '../../api';
import Spinner from '../../components/Spinner';

const SCENARIOS = [
  { value: 'interested_positive', label: 'Interested — positive' },
  { value: 'asks_for_pricing', label: 'Asks for pricing' },
  { value: 'asks_for_capacity', label: 'Asks for capacity' },
  { value: 'asks_for_sms', label: 'Requests SMS handoff' },
  { value: 'defensive_about_competitor_gap', label: 'Defensive about competitor gap' },
  { value: 'timezone_confusion', label: 'Timezone confusion' },
  { value: 'interested_with_correction', label: 'Interested — corrects a claim' },
];

export default function ConversationTab({ leadId, lead }) {
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [scenario, setScenario] = useState('interested_positive');
  const [customText, setCustomText] = useState('');
  const [channel, setChannel] = useState('email');
  const [simLoading, setSimLoading] = useState(false);
  const [simResult, setSimResult] = useState(null);

  async function loadMessages() {
    try {
      const msgs = await api.getLeadMessages(leadId);
      setMessages(msgs);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadMessages(); }, [leadId]);

  async function handleSimulate() {
    setSimLoading(true);
    setSimResult(null);
    try {
      const res = await api.simulateReply({
        lead_id: leadId,
        scenario,
        channel,
        custom_text: customText || null,
      });
      setSimResult(res);
      await loadMessages();
    } catch (e) {
      setSimResult({ error: e.message });
    } finally {
      setSimLoading(false);
    }
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-8 animate-in fade-in slide-in-from-bottom-4 duration-700">
      {/* Simulation Control Panel */}
      <div className="lg:col-span-1 space-y-6">
        <div className="glass-card p-6 space-y-6">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-purple-500/10 flex items-center justify-center text-purple-400 border border-purple-500/20">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
              </svg>
            </div>
            <h3 className="text-xs font-bold text-slate-100 uppercase tracking-widest">Synthetic Reply</h3>
          </div>

          <div className="space-y-4">
            <div className="space-y-1.5">
              <label className="text-[10px] font-bold text-slate-500 uppercase tracking-widest pl-1">Response Scenario</label>
              <select
                className="w-full bg-slate-950/50 border border-slate-800 rounded-xl px-3 py-2 text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500/50 transition-all appearance-none"
                value={scenario}
                onChange={(e) => setScenario(e.target.value)}
              >
                {SCENARIOS.map((s) => (
                  <option key={s.value} value={s.value}>{s.label}</option>
                ))}
              </select>
            </div>

            <div className="space-y-1.5">
              <label className="text-[10px] font-bold text-slate-500 uppercase tracking-widest pl-1">Return Channel</label>
              <div className="flex gap-2">
                {['email', 'sms'].map((c) => (
                  <button
                    key={c}
                    onClick={() => setChannel(c)}
                    className={`flex-1 py-2 rounded-xl border text-xs font-bold uppercase tracking-wider transition-all ${
                      channel === c
                        ? 'bg-indigo-600 border-indigo-500 text-white shadow-lg shadow-indigo-500/20'
                        : 'bg-slate-950/50 border-slate-800 text-slate-500 hover:border-slate-700'
                    }`}
                  >
                    {c}
                  </button>
                ))}
              </div>
            </div>

            <div className="space-y-1.5">
              <label className="text-[10px] font-bold text-slate-500 uppercase tracking-widest pl-1">Custom Payload (Optional)</label>
              <textarea
                className="w-full bg-slate-950/50 border border-slate-800 rounded-xl px-3 py-2 text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500/50 transition-all resize-none font-sans"
                rows={3}
                placeholder="Override simulation text..."
                value={customText}
                onChange={(e) => setCustomText(e.target.value)}
              />
            </div>

            <button
              onClick={handleSimulate}
              disabled={simLoading}
              className="w-full btn-primary flex items-center justify-center gap-2 py-3"
            >
              {simLoading ? <Spinner size="sm" /> : <span>Inject Reply</span>}
            </button>
          </div>

          {simResult && (
            <div className={`p-4 rounded-xl text-[11px] font-medium animate-in slide-in-from-top-2 ${simResult.error ? 'bg-red-500/10 text-red-400 border border-red-500/20' : 'bg-slate-950 border border-slate-800 text-slate-400'}`}>
              {simResult.error ? simResult.error : 'Synthetic payload delivered to agent.'}
            </div>
          )}
        </div>
      </div>

      {/* Message Timeline */}
      <div className="lg:col-span-2 space-y-6">
        <div className="flex items-center justify-between px-4">
          <h3 className="text-xs font-bold text-slate-500 uppercase tracking-widest">Full Message Timeline</h3>
          {messages.length > 0 && <span className="text-[10px] font-mono text-indigo-400 font-bold">{messages.length} Events</span>}
        </div>

        {loading ? (
          <div className="flex justify-center py-20"><Spinner /></div>
        ) : error ? (
          <div className="glass-card p-8 text-center text-red-400 text-sm">{error}</div>
        ) : messages.length === 0 ? (
          <div className="glass-card py-20 text-center space-y-4">
            <div className="w-12 h-12 bg-slate-950 rounded-full flex items-center justify-center mx-auto border border-slate-900">
               <svg className="w-6 h-6 text-slate-800" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                 <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
               </svg>
            </div>
            <p className="text-slate-600 italic text-xs">Awaiting first outreach sequence.</p>
          </div>
        ) : (
          <div className="relative space-y-6 before:absolute before:left-6 before:top-4 before:bottom-4 before:w-px before:bg-slate-800/50">
            {messages.map((msg, idx) => (
              <MessageBubble key={msg.id} msg={msg} isLast={idx === messages.length - 1} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function MessageBubble({ msg, isLast }) {
  const isOutbound = msg.direction === 'outbound';
  const isBooking = msg.event_type === 'booking_created' || msg.intent === 'chooses_slot';
  
  const getChannelIcon = () => {
    if (isBooking) return (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
      </svg>
    );
    if (msg.channel === 'sms') return (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 18h.01M8 21h8a2 2 0 002-2V5a2 2 0 00-2-2H8a2 2 0 00-2 2v14a2 2 0 002 2z" />
      </svg>
    );
    return (
      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
      </svg>
    );
  };

  return (
    <div className={`relative pl-14 transition-all duration-500 animate-in fade-in slide-in-from-left-4`}>
      {/* Connector Dot & Icon */}
      <div className={`absolute left-[14px] top-3 w-8 h-8 rounded-xl ring-4 ring-slate-950 z-10 flex items-center justify-center border shadow-xl ${
        isBooking ? 'bg-emerald-500/20 border-emerald-500/30 text-emerald-400' :
        isOutbound ? 'bg-indigo-500/20 border-indigo-500/30 text-indigo-400' : 
        'bg-purple-500/20 border-purple-500/30 text-purple-400'
      }`}>
        {getChannelIcon()}
      </div>
      
      <div className={`glass-card p-5 space-y-4 relative overflow-hidden ${
        isBooking ? 'border-emerald-500/20 bg-emerald-500/5' :
        isOutbound ? 'border-indigo-500/20 bg-indigo-500/5' : 'border-slate-800'
      }`}>
        {/* Background Accent for Booking */}
        {isBooking && (
          <div className="absolute -right-4 -top-4 w-24 h-24 bg-emerald-500/5 rounded-full blur-2xl"></div>
        )}

        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-3">
             <span className={`text-[10px] font-bold uppercase tracking-widest ${
               isBooking ? 'text-emerald-400' :
               isOutbound ? 'text-indigo-400' : 'text-purple-400'
             }`}>
               {isBooking ? 'Event: Booking Confirmed' : isOutbound ? 'Sequence Out' : 'Counter-party In'}
             </span>
             <div className="h-3 w-px bg-slate-800"></div>
             <span className="text-[10px] font-bold text-slate-500 uppercase">{msg.channel || 'system'}</span>
             {msg.intent && (
               <span className={`text-[9px] border px-2 py-0.5 rounded-full font-mono font-bold ${
                 isBooking ? 'bg-emerald-950 border-emerald-500/30 text-emerald-400' : 'bg-slate-900 border-slate-800 text-slate-400'
               }`}>
                 {msg.intent.toUpperCase()}
               </span>
             )}
             {msg.is_draft && (
               <span className="text-[9px] bg-amber-500/10 border border-amber-500/30 text-amber-500 px-2 py-0.5 rounded-full font-bold uppercase tracking-tighter">
                 Draft / Staff Sink
               </span>
             )}
          </div>
          <span className="text-[10px] font-mono text-slate-600">
            {msg.sent_at ? new Date(msg.sent_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : ''}
          </span>
        </div>

        {msg.subject && (
          <div className="text-sm font-bold text-slate-100 border-b border-slate-800 pb-2 mb-2 flex items-center gap-2">
            <span className="text-[10px] font-bold text-slate-600 uppercase">Sub:</span>
            {msg.subject}
          </div>
        )}
        
        <div className="text-sm text-slate-300 whitespace-pre-wrap leading-relaxed font-sans font-medium">
          {msg.body}
        </div>

        {isBooking && msg.payload?.calData && (
          <div className="mt-4 p-3 bg-slate-950/50 border border-emerald-500/10 rounded-lg space-y-2">
            <div className="flex items-center gap-2 text-[10px] text-emerald-400 font-bold uppercase">
              <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
              Appointment Details
            </div>
            <div className="text-xs text-slate-400">
              {msg.payload.calData.startTime} - {msg.payload.calData.endTime} ({msg.payload.calData.timezone})
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
