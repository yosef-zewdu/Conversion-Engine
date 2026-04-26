const BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

async function request(method, path, body) {
  let apiKey = localStorage.getItem('CONVERSION_ENGINE_KEY');
  
  // Only prompt for key on actions (POST, DELETE, etc)
  if (!apiKey && method !== 'GET' && !path.includes('/health')) {
    apiKey = prompt("Please enter your Admin API Key to perform this action:");
    if (apiKey) localStorage.setItem('CONVERSION_ENGINE_KEY', apiKey);
  }

  const opts = {
    method,
    headers: { 
      'Content-Type': 'application/json',
      'X-API-Key': apiKey 
    },
  };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const res = await fetch(`${BASE_URL}${path}`, opts);
  
  if (res.status === 401) {
    localStorage.removeItem('CONVERSION_ENGINE_KEY');
    alert("Invalid API Key. Please refresh and try again.");
    throw new Error("Unauthorized");
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export const api = {
  health: () => request('GET', '/health'),

  // Campaigns
  listCampaigns: () => request('GET', '/campaigns'),
  runCampaign: (body) => request('POST', '/campaigns/run', body),
  getCampaign: (id) => request('GET', `/campaigns/${id}`),
  getCampaignAccounts: (id) => request('GET', `/campaigns/${id}/accounts`),

  // Leads
  getLead: (id) => request('GET', `/leads/${id}`),
  getLeadBriefs: (id) => request('GET', `/leads/${id}/briefs`),
  getLeadMessages: (id) => request('GET', `/leads/${id}/messages`),
  getLeadTraces: (id) => request('GET', `/leads/${id}/traces`),
  startOutreach: (id, body) => request('POST', `/leads/${id}/start-outreach`, body),

  // Dev
  simulateReply: (body) => request('POST', '/dev/simulate-reply', body),
  replayEvent: (eventId) => request('POST', '/dev/replay-event', { event_id: eventId }),

  // Traces
  getTrace: (id) => request('GET', `/traces/${id}`),
};
