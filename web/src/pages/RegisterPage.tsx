import { FormEvent, useState } from "react";
import { api } from "../api/client";
import { setSession } from "../lib/auth";

type Props = {
  onSuccess: () => void;
  onLogin: () => void;
};

export function RegisterPage({ onSuccess, onLogin }: Props) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    const { data, error: err, response } = await api.POST("/v1/auth/register", {
      body: { username, password },
    });
    setLoading(false);
    if (response.status === 409) {
      setError("Username already taken");
      return;
    }
    if (!response.ok || err || !data?.apiKey) {
      setError("Registration failed");
      return;
    }
    setSession(data.apiKey, data.username);
    onSuccess();
  }

  return (
    <div className="auth-form card">
      <h1>Create account</h1>
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
            autoComplete="new-password"
            required
          />
        </div>
        {error && <p className="status err">{error}</p>}
        <button type="submit" disabled={loading} style={{ width: "100%" }}>
          {loading ? "Creating…" : "Sign up"}
        </button>
      </form>
      <p className="auth-toggle">
        Have an account?{" "}
        <button type="button" onClick={onLogin}>
          Sign in
        </button>
      </p>
    </div>
  );
}
