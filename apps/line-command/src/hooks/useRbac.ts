import { useAuth } from "./useAuth";

/** Console-level role types for Line Command. */
export type LineCommandRole =
  | "shift_lead"
  | "assembly_associate"
  | "forbidden";

export interface RbacState {
  role: LineCommandRole;
  userId: string;
  loading: boolean;
}

function resolveRole(roles: string[]): LineCommandRole {
  if (roles.includes("shift_lead")) return "shift_lead";
  if (roles.includes("assembly_associate")) return "assembly_associate";
  return "forbidden";
}

/**
 * Derives the Line Command RBAC role from the current Entra ID JWT.
 * Re-evaluates automatically on each 5-minute token refresh.
 */
export function useRbac(): RbacState {
  const { roles, userId, loading } = useAuth();
  return { role: resolveRole(roles), userId, loading };
}
