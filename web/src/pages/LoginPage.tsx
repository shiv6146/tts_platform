import { FormEvent, useState } from "react";
import { api } from "../api/client";
import { setSession } from "../lib/auth";

type Props = {
  onSuccess: () => void;
  onRegister: () => void;
};

export function LoginPage({ onSuccess, onRegister }: Props) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    const { data, error: err, response } = await api.POST("/v1/auth/login", {
      body: { username, password },
    });
    setLoading(false);
    if (!response.ok || err || !data?.apiKey) {
      setError("Invalid username or password");
      return;
    }
    setSession(data.apiKey, data.username);
    onSuccess();
  }

  return (
    <div className="auth-form card">
      <h1>Sign in</h1>
      <form onSubmit={submit}>
        <div className="field">
          <label htmlFor="user">Username</label>
          <input
            id="user"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            required
          />
        </div>
        <div className="field">
          <label htmlFor="pass">Password</label>
          <input
            id="pass"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </div>
        {error && <p className="status err">{error}</p>}
        <button type="submit" disabled={loading} style={{ width: "100%" }}>
          {loading ? "Signing in…" : "Sign in"}
        </button>
      </form>
      <p className="auth-toggle">
        New here?{" "}
        <button type="button" onClick={onRegister}>
          Create account
        </button>
      </p>
    </div>
  );
}
