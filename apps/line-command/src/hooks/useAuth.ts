import { useEffect, useRef, useState } from "react";

const TOKEN_KEY = "lc_access_token";
const REFRESH_MS = 5 * 60 * 1000; // 5-minute role-propagation SLA (US-022)

export interface AuthState {
  /** Raw role strings from the JWT `roles` claim, or empty array when unauthenticated. */
  roles: string[];
  /** Subject claim (user ID) from the JWT. */
  userId: string;
  /** True while the initial token parse is in progress. */
  loading: boolean;
}

function parseJwtPayload(token: string): Record<string, unknown> | null {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return null;
    const payload = parts[1];
    // Base64url → Base64 → decode
    const base64 = payload.replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), "=");
    return JSON.parse(atob(padded)) as Record<string, unknown>;
  } catch {
    return null;
  }
}

function extractAuthState(token: string | null): Omit<AuthState, "loading"> {
  if (!token) return { roles: [], userId: "" };
  const payload = parseJwtPayload(token);
  if (!payload) return { roles: [], userId: "" };

  const raw = payload["roles"];
  const roles = Array.isArray(raw) ? raw.filter((r): r is string => typeof r === "string") : [];
  const userId = typeof payload["sub"] === "string" ? payload["sub"] : "";
  return { roles, userId };
}

/**
 * Reads the Entra ID JWT from localStorage, exposes `roles` and `userId`, and
 * re-evaluates on every REFRESH_MS interval so a role change propagated from
 * Azure AD takes effect in the open session without a page reload.
 */
export function useAuth(): AuthState {
  const [state, setState] = useState<AuthState>(() => ({
    ...extractAuthState(localStorage.getItem(TOKEN_KEY)),
    loading: true,
  }));
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    // Initial evaluation
    setState({
      ...extractAuthState(localStorage.getItem(TOKEN_KEY)),
      loading: false,
    });

    // Re-evaluate on each 5-minute tick — picks up IdP role changes
    intervalRef.current = setInterval(() => {
      setState({
        ...extractAuthState(localStorage.getItem(TOKEN_KEY)),
        loading: false,
      });
    }, REFRESH_MS);

    return () => {
      if (intervalRef.current !== null) clearInterval(intervalRef.current);
    };
  }, []);

  return state;
}
