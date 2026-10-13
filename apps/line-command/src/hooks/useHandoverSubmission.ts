import { useCallback, useRef, useState } from "react";
import { apiRequest } from "../services/api";

// ---------------------------------------------------------------------------
// Type definitions — aligned with LLD contract
// ---------------------------------------------------------------------------

export interface AlertDisposition {
  alertId: string;
  disposition: "resolved" | "escalated" | "monitoring";
  note?: string;
}

export interface HandoverRequest {
  shiftId: string;
  lineId: string;
  shiftStartTs: string;
  shiftEndTs: string;
  alertDispositions: AlertDisposition[];
  oeeDeltaPct: number;
  heldVins?: string[];
  notes?: string;
  idempotencyKey?: string;
}

export interface HandoverResponse {
  handoverId: string;
  submittedAt: string;
  submittedByUserId: string;
}

// ---------------------------------------------------------------------------
// Hook state types
// ---------------------------------------------------------------------------

export type HandoverSubmissionState =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "success"; handoverId: string; submittedAt: string }
  | { kind: "error"; code: string; message: string };

// ---------------------------------------------------------------------------
// Generate a per-session idempotency key (UUID v4 via Web Crypto)
// ---------------------------------------------------------------------------

function generateIdempotencyKey(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes)
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

/**
 * Manages handover form submission to POST /handover.
 *
 * Generates a stable idempotency key at hook instantiation (i.e., when the
 * handover form opens) and reuses it across retries and duplicate submissions.
 * Server-side adoption events are emitted synchronously within the HTTP
 * response.
 *
 * Error codes:
 *   "409" — Handover already submitted for this shift (idempotency)
 *   "403" — Caller does not have shift-lead role
 *   "422" — Invalid handover payload
 *   "401" — Unauthorised
 */
export function useHandoverSubmission() {
  const [state, setState] = useState<HandoverSubmissionState>({ kind: "idle" });

  // Stable idempotency key — generated once per hook instance, reused across retries
  const idempotencyKeyRef = useRef<string | null>(null);
  const ensureIdempotencyKey = useCallback(() => {
    if (!idempotencyKeyRef.current) {
      idempotencyKeyRef.current = generateIdempotencyKey();
    }
    return idempotencyKeyRef.current;
  }, []);

  const submit = useCallback(
    async (request: Omit<HandoverRequest, "idempotencyKey">) => {
      setState({ kind: "submitting" });
      try {
        const idempotencyKey = ensureIdempotencyKey();
        const response = await apiRequest<HandoverResponse>("/api/v1/handover", {
          method: "POST",
          body: JSON.stringify({ ...request, idempotencyKey }),
        });

        setState({
          kind: "success",
          handoverId: response.handoverId,
          submittedAt: response.submittedAt,
        });
      } catch (err) {
        const error = err as Error & { status?: number };
        let code = "500";
        let message = "Handover submission failed";

        if (error.message.includes("409")) {
          code = "409";
          message = "Handover already submitted for this shift";
        } else if (error.message.includes("403")) {
          code = "403";
          message = "Shift Lead role required";
        } else if (error.message.includes("422")) {
          code = "422";
          message = "Invalid handover data";
        } else if (error.message.includes("401")) {
          code = "401";
          message = "Unauthorised";
        }

        setState({ kind: "error", code, message });
      }
    },
    [ensureIdempotencyKey],
  );

  const reset = useCallback(() => {
    setState({ kind: "idle" });
  }, []);

  return { state, submit, reset };
}
