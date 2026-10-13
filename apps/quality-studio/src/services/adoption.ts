/**
 * Adoption event emitter — sends usage telemetry to the observability stack
 * via OTLP HTTP endpoint. Best-effort: failures are silently swallowed.
 */

const OTLP_ENDPOINT =
  import.meta.env.VITE_OTLP_ENDPOINT ?? "/api/v1/telemetry/adoption";

export type AdoptionAction =
  | "GATE_COMMAND_OPEN"
  | "CERTIFY";

export interface AdoptionEvent {
  userId: string;
  role: "QUALITY_GATE_VALIDATOR";
  action: AdoptionAction;
  vin: string;
  fabricTxHash?: string;
  timestamp: string;
}

export async function emitAdoptionEvent(event: AdoptionEvent): Promise<void> {
  try {
    await fetch(OTLP_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(event),
    });
  } catch {
    // best-effort — do not surface telemetry failures to the user
  }
}
