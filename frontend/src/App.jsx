import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import Layout from './components/Layout';
import CampaignsPage from './pages/CampaignsPage';
import AccountsPage from './pages/AccountsPage';
import LeadDetailPage from './pages/LeadDetailPage';
import DashboardPage from './pages/DashboardPage';

export default function App() {
  return (
    <BrowserRouter>
      <Layout>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/campaigns" element={<CampaignsPage />} />
          <Route path="/campaigns/:campaignId/accounts" element={<AccountsPage />} />
          <Route path="/accounts" element={<AccountsPage />} />
          <Route path="/leads/:leadId" element={<LeadDetailPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  );
}
