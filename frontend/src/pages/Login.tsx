import { FormEvent, useState } from "react";

import { api } from "../api/client";

export default function LoginPage({ onLogin }: { onLogin: () => Promise<void> }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("/api/auth/login", { method: "POST", body: { username, password } });
      await onLogin();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <form onSubmit={submit} className="card w-full max-w-sm p-7">
        <div className="mb-6 flex items-center gap-2">
          <img src="/favicon.svg" alt="" className="h-7 w-7" />
          <h1 className="text-[19px]">InfoPoint</h1>
        </div>
        <label className="label" htmlFor="u">Username</label>
        <input id="u" className="input mb-4" autoFocus value={username} onChange={(e) => setUsername(e.target.value)} />
        <label className="label" htmlFor="p">Password</label>
        <input id="p" type="password" className="input mb-5" value={password}
               onChange={(e) => setPassword(e.target.value)} />
        {error && <p className="mb-4 text-[14px] text-danger" role="alert">{error}</p>}
        <button className="btn-primary w-full justify-center" disabled={busy || !username}>Sign in</button>
      </form>
    </div>
  );
}
