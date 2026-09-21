import { useState, type FormEvent } from "react";
import { Link, Navigate } from "react-router";

import { useAuth } from "../../app/auth";
import { useConsoleMeta } from "../../app/meta";
import { EmptyState } from "../../components/EmptyState";
import { FormError } from "../../components/FormError";
import { Loading } from "../../components/Loading";
import { useSubmit } from "../../components/useSubmit";
import { AuthLayout } from "./AuthLayout";

const MIN_PASSWORD_LENGTH = 12;

export function SignUpPage() {
  const { state, signUp } = useAuth();
  const meta = useConsoleMeta();
  const [form, setForm] = useState({ email: "", password: "", display_name: "", organization_name: "" });
  const { pending, error, run } = useSubmit();

  if (state.status === "signed_in") return <Navigate to="/" replace />;
  if (meta.status === "loading") return <Loading />;

  if (meta.status === "ready" && !meta.data.signup.available) {
    return (
      <AuthLayout title="Sign-up is closed" lede="This server does not accept new accounts from the console.">
        <EmptyState title="Ask an owner, or create an account from the server">
          <p>
            <code>python -m api.console.bootstrap_owner --email you@example.com --new-org "My org"</code>
          </p>
        </EmptyState>
        <p className="auth__alt">
          <Link to="/login">Back to sign in</Link>
        </p>
      </AuthLayout>
    );
  }

  const update = (field: keyof typeof form) => (value: string) => setForm((f) => ({ ...f, [field]: value }));
  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    void run(() => signUp(form));
  };

  return (
    <AuthLayout
      title="Create your account"
      lede="The first account on this server. It owns the organization you name below."
    >
      <form className="form" onSubmit={onSubmit}>
        <label className="form__field">
          Your name
          <input autoComplete="name" value={form.display_name} onChange={(e) => update("display_name")(e.target.value)} />
        </label>
        <label className="form__field">
          Email
          <input
            type="email"
            autoComplete="email"
            required
            value={form.email}
            onChange={(e) => update("email")(e.target.value)}
          />
        </label>
        <label className="form__field">
          Password
          <input
            type="password"
            autoComplete="new-password"
            required
            minLength={MIN_PASSWORD_LENGTH}
            value={form.password}
            onChange={(e) => update("password")(e.target.value)}
          />
          <span className="form__hint">At least {MIN_PASSWORD_LENGTH} characters.</span>
        </label>
        <label className="form__field">
          Organization name
          <input
            required
            maxLength={100}
            value={form.organization_name}
            onChange={(e) => update("organization_name")(e.target.value)}
          />
        </label>
        <FormError error={error} />
        <button type="submit" className="button button--primary" disabled={pending}>
          {pending ? "Creating…" : "Create account"}
        </button>
      </form>
      <p className="auth__alt">
        Already have an account? <Link to="/login">Sign in</Link>
      </p>
    </AuthLayout>
  );
}
