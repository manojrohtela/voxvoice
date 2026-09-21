import { useState, type FormEvent } from "react";
import { Link, Navigate } from "react-router";

import { useAuth } from "../../app/auth";
import { useConsoleMeta } from "../../app/meta";
import { FormError } from "../../components/FormError";
import { useSubmit } from "../../components/useSubmit";
import { AuthLayout } from "./AuthLayout";

export function SignInPage() {
  const { state, signIn } = useAuth();
  const meta = useConsoleMeta();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const { pending, error, run } = useSubmit();

  if (state.status === "signed_in") return <Navigate to="/" replace />;

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    void run(() => signIn(email, password));
  };

  return (
    <AuthLayout title="Sign in" lede="Sign in to manage your agents and talk to them.">
      <form className="form" onSubmit={onSubmit}>
        <label className="form__field">
          Email
          <input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label className="form__field">
          Password
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <FormError error={error} />
        <button type="submit" className="button button--primary" disabled={pending}>
          {pending ? "Signing in…" : "Sign in"}
        </button>
      </form>
      {meta.status === "ready" && meta.data.signup.available ? (
        <p className="auth__alt">
          New here? <Link to="/signup">Create the first account</Link>
        </p>
      ) : null}
    </AuthLayout>
  );
}
