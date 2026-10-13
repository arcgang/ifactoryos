import { useEffect, useRef, useState } from "react";

const TOKEN_KEY = "qs_access_token";
const REFRESH_MS = 5 * 60 * 1000;

export interface AuthState {
  roles: string[];
  userId: string;
  loading: boolean;
}

function parseJwtPayload(token: string): Record<string, unknown> | null {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return null;
    const payload = parts[1];
    const base64 = payload.replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64.padEnd(
      base64.length + ((4 - (base64.length % 4)) % 4),
      "=",
    );
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
  const roles = Array.isArray(raw)
    ? raw.filter((r): r is string => typeof r === "string")
    : [];
  const userId =
    typeof payload["sub"] === "string" ? payload["sub"] : "";
  return { roles, userId };
}

export function useAuth(): AuthState {
  const [state, setState] = useState<AuthState>(() => ({
    ...extractAuthState(localStorage.getItem(TOKEN_KEY)),
    loading: true,
  }));
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    setState({
      ...extractAuthState(localStorage.getItem(TOKEN_KEY)),
      loading: false,
    });
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
