import { Link, useLocation } from 'react-router-dom';

const NAV = [
  { to: '/', label: 'Dashboard' },
  { to: '/campaigns', label: 'Campaigns' },
  { to: '/accounts', label: 'Accounts' },
];

export default function Layout({ children }) {
  const { pathname } = useLocation();

  return (
    <div className="min-h-screen flex flex-col font-sans">
      <header className="sticky top-0 z-50 bg-slate-950/80 backdrop-blur-xl border-b border-slate-800/50 px-8 py-4 flex items-center gap-10">
        <div className="flex items-center gap-2 group cursor-pointer">
          <div className="w-8 h-8 bg-indigo-600 rounded-lg flex items-center justify-center shadow-lg shadow-indigo-500/30 group-hover:rotate-12 transition-transform">
            <span className="text-white font-bold">⚡</span>
          </div>
          <span className="text-slate-100 font-bold text-xl tracking-tight">
            Conversion Engine
          </span>
        </div>
        
        <nav className="flex gap-1 p-1 bg-slate-900/50 rounded-full border border-slate-800/50">
          {NAV.map(({ to, label }) => {
            const isActive = pathname === to || (to !== '/' && pathname.startsWith(to));
            return (
              <Link
                key={to}
                to={to}
                className={`nav-link ${isActive ? 'nav-link-active' : 'nav-link-inactive'}`}
              >
                {label}
              </Link>
            );
          })}
        </nav>
        
        <div className="ml-auto flex items-center gap-4">
          <div className="flex flex-col items-end">
            <span className="text-[10px] text-indigo-400 font-bold uppercase tracking-widest">Operator Console</span>
            <span className="text-xs text-slate-500 font-medium">Tenacious Consulting</span>
          </div>
          <div className="w-8 h-8 rounded-full bg-gradient-to-tr from-slate-800 to-slate-700 border border-slate-700 flex items-center justify-center">
            <span className="text-[10px] font-bold text-slate-400">YZ</span>
          </div>
        </div>
      </header>
      
      <main className="flex-1 max-w-7xl mx-auto w-full p-8">
        {children}
      </main>
      
      <footer className="px-8 py-6 border-t border-slate-900 text-center">
        <span className="text-xs text-slate-600">Built for Advanced Agentic Coding • Act II Store</span>
      </footer>
    </div>
  );
}
