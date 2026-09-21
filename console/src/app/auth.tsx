import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { ApiError, getJson, postJson, postNoContent } from "../api/client";
import type { Me } from "../api/types";

// The console never sees its session token: the server keeps it in an HttpOnly cookie. What the
// browser knows is only what `/v1/console/me` says, so that call *is* the auth state
// (security model §4). Nothing here touches localStorage or sessionStorage.

export type AuthState =
  | { status: "loading" }
  | { status: "signed_out" }
  | { status: "signed_in"; me: Me }
  | { status: "error"; error: ApiError };

export interface SignUpInput {
  email: string;
  password: string;
  display_name: string;
  organization_name: string;
}

interface AuthApi {
  state: AuthState;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (input: SignUpInput) => Promise<void>;
  signOut: () => Promise<void>;
  createOrganization: (name: string) => Promise<void>;
  selectOrganization: (organizationId: string) => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthApi | null>(null);

async function loadMe(): Promise<AuthState> {
  try {
    return { status: "signed_in", me: await getJson<Me>("/v1/console/me") };
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return { status: "signed_out" };
    return {
      status: "error",
      error: error instanceof ApiError ? error : new ApiError(0, "internal", "Could not load your account"),
    };
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "loading" });

  const refresh = useCallback(async () => setState(await loadMe()), []);

  useEffect(() => {
    let active = true;
    void loadMe().then((next) => {
      if (active) setState(next);
    });
    return () => {
      active = false;
    };
  }, []);

  const api = useMemo<AuthApi>(() => {
    const signedIn = (me: Me) => setState({ status: "signed_in", me });
    return {
      state,
      refresh,
      signIn: async (email, password) =>
        signedIn(await postJson<Me>("/v1/console/auth/login", { email, password })),
      signUp: async (input) => signedIn(await postJson<Me>("/v1/console/auth/signup", input)),
      signOut: async () => {
        await postNoContent("/v1/console/auth/logout");
        setState({ status: "signed_out" });
      },
      createOrganization: async (name) =>
        signedIn(await postJson<Me>("/v1/console/organizations", { name })),
      selectOrganization: async (organizationId) =>
        signedIn(
          await postJson<Me>("/v1/console/session/organization", { organization_id: organizationId }),
        ),
    };
  }, [state, refresh]);

  return <AuthContext.Provider value={api}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthApi {
  const auth = useContext(AuthContext);
  if (auth === null) throw new Error("useAuth must be used inside <AuthProvider>");
  return auth;
}

/** The signed-in user. Only valid inside the protected shell, which guarantees it. */
export function useMe(): Me {
  const { state } = useAuth();
  if (state.status !== "signed_in") throw new Error("useMe requires a signed-in user");
  return state.me;
}
