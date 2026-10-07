import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";

import { api, ApiError } from "./api/client";
import type { AppStatus, User } from "./api/types";
import AskPage from "./pages/Ask";
import LibraryPage from "./pages/Library";
import LoginPage from "./pages/Login";
import SettingsPage from "./pages/Settings";
import SetupPage from "./pages/Setup";

interface Session {
  user: User;
  status: AppStatus;
  refreshStatus: () => Promise<void>;
  logout: () => Promise<void>;
}

const SessionContext = createContext<Session | null>(null);
export const useSession = () => useContext(SessionContext)!;

export default function App() {
  const [user, setUser] = useState<User | null | undefined>(undefined);
  const [status, setStatus] = useState<AppStatus | null>(null);

  const refreshStatus = useCallback(async () => {
    setStatus(await api<AppStatus>("/api/app/status"));
  }, []);

  const loadUser = useCallback(async () => {
    try {
      const u = await api<User>("/api/auth/me");
      setUser(u);
      await refreshStatus();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setUser(null);
      else throw e;
    }
  }, [refreshStatus]);

  useEffect(() => {
    loadUser();
  }, [loadUser]);

  const logout = async () => {
    await api("/api/auth/logout", { method: "POST" });
    setUser(null);
  };

  if (user === undefined || (user && !status)) {
    return <div className="flex h-screen items-center justify-center text-muted">Loading InfoPoint…</div>;
  }
  if (user === null) return <LoginPage onLogin={loadUser} />;

  return (
    <SessionContext.Provider value={{ user, status: status!, refreshStatus, logout }}>
      <Shell />
    </SessionContext.Provider>
  );
}

function Shell() {
  const { user, status } = useSession();
  const location = useLocation();
  const isAdmin = user.role === "admin";

  if (status.first_run && isAdmin && location.pathname !== "/setup") return <Navigate to="/setup" replace />;

  const tab = ({ isActive }: { isActive: boolean }) =>
    `px-3 py-1.5 rounded-md text-[14.5px] ${isActive ? "bg-accent-soft text-accent" : "text-muted hover:text-ink"}`;

  return (
    <div className="flex h-screen flex-col">
      <header className="flex h-14 shrink-0 items-center gap-6 border-b border-line bg-surface px-5">
        <div className="flex items-center gap-2">
          <img src="/favicon.svg" alt="" className="h-6 w-6" />
          <span className="text-[16px] font-semibold tracking-tight">InfoPoint</span>
        </div>
        {location.pathname !== "/setup" && (
          <nav className="flex gap-1" aria-label="Main">
            <NavLink to="/" end className={tab}>Ask</NavLink>
            <NavLink to="/library" className={tab}>Library</NavLink>
            {isAdmin && <NavLink to="/settings" className={tab}>Settings</NavLink>}
          </nav>
        )}
        <div className="ml-auto text-[14px] text-muted">{user.display_name}</div>
      </header>
      <main className="min-h-0 flex-1">
        <Routes>
          <Route path="/" element={<AskPage />} />
          <Route path="/c/:conversationId" element={<AskPage />} />
          <Route path="/library" element={<LibraryPage />} />
          {isAdmin && <Route path="/settings" element={<SettingsPage />} />}
          {isAdmin && <Route path="/setup" element={<SetupPage />} />}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
