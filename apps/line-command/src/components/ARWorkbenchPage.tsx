import { useCallback, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import {
  ARWorkInstructions,
  type ArInstructionResponse,
  type ArInstructionFallbackResponse,
} from "./ARWorkInstructions";
import { apiRequest, ForbiddenError } from "../services/api";

// ---------------------------------------------------------------------------
// Station config — resolved from the device's station configuration store.
// In production this comes from the edge-gateway local config; the values
// below are the defaults that ship with the SPA bundle for development.
// ---------------------------------------------------------------------------

const STATION_ID = import.meta.env.VITE_STATION_ID ?? "MARRIAGE-01";
const DEVICE_TYPE: "HoloLens2" | "iPad" =
  (import.meta.env.VITE_DEVICE_TYPE as "HoloLens2" | "iPad") ?? "HoloLens2";

// ---------------------------------------------------------------------------
// OTel span stub — emits a named span via the browser's Performance API until
// the OpenTelemetry SDK is wired in at the shell level.
// ---------------------------------------------------------------------------

function emitOtelSpan(name: string, attributes: Record<string, string | number | boolean>): void {
  try {
    performance.mark(`${name}:start`);
    performance.mark(`${name}:end`);
    performance.measure(name, {
      start: `${name}:start`,
      end: `${name}:end`,
      detail: attributes,
    });
  } catch {
    // Non-fatal — OTel stub is best-effort
  }
}

// ---------------------------------------------------------------------------
// Generate a per-read idempotency key (UUID v4 via Web Crypto)
// ---------------------------------------------------------------------------

function generateIdempotencyKey(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes).map((b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

async function fetchArInstructions(
  vin: string,
  stationId: string,
  signal: AbortSignal,
  userId: string,
  onForbidden: (resource: string) => void,
): Promise<ArInstructionResponse | ArInstructionFallbackResponse> {
  const resource = `/api/v1/ar/instructions/${vin}?stationId=${encodeURIComponent(stationId)}&deviceType=${encodeURIComponent(DEVICE_TYPE)}`;
  try {
    return await apiRequest<ArInstructionResponse | ArInstructionFallbackResponse>(resource, {
      signal,
      userId,
    });
  } catch (err) {
    if (err instanceof ForbiddenError) {
      onForbidden(err.resource);
      throw err;
    }
    throw err;
  }
}

async function postArSession(
  vin: string,
  stationId: string,
  userId: string,
  idempotencyKey: string,
): Promise<void> {
  try {
    await apiRequest("/api/v1/ar/sessions", {
      method: "POST",
      body: JSON.stringify({ vin, stationId, deviceType: DEVICE_TYPE, idempotencyKey }),
      userId,
    });
  } catch {
    // Session post failure must not block the overlay — best-effort
    console.warn("[ar-workbench] AR_SESSION_START post failed — overlay already rendered");
  }
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface Props {
  vin: string;
  userId: string;
  onForbidden: (resource: string) => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * AR Workbench page — RFID-triggered overlay load.
 *
 * On mount the RFID read timestamp is captured (simulating the hardware event).
 * GET /api/v1/ar/instructions/{vin} is called with a 10 s client-side timeout.
 * On first successful render an AR_SESSION_START OTel span is emitted and
 * POST /api/v1/ar/sessions is called (idempotent, failures are swallowed).
 * A 504 or 10 s timeout yields the fallback panel with VIN + PDF QR code.
 */
export function ARWorkbenchPage({ vin, userId, onForbidden }: Props) {
  const { t } = useTranslation();

  // Record the RFID-read moment so the 5 s render SLA warning can measure latency.
  const rfidReadAtRef = useRef<number | null>(null);

  useEffect(() => {
    rfidReadAtRef.current = performance.now();
    emitOtelSpan("RFID_READ", { vin, stationId: STATION_ID });
  }, [vin]);

  const handleOverlayRendered = useCallback(
    (data: ArInstructionResponse) => {
      const rfidReadAt = rfidReadAtRef.current;
      const renderMs = rfidReadAt !== null ? performance.now() - rfidReadAt : null;

      if (renderMs !== null && renderMs > 5_000) {
        console.warn(
          `[ar-workbench] 5 s render SLA missed: ${renderMs.toFixed(0)} ms (vin=${vin})`,
        );
      }

      emitOtelSpan("AR_SESSION_START", {
        vin,
        stationId: STATION_ID,
        instructionVersion: data.instructionVersion,
        isRareVariant: data.isRareVariant,
        renderMs: renderMs ?? -1,
      });

      const idempotencyKey = generateIdempotencyKey();
      void postArSession(vin, STATION_ID, userId, idempotencyKey);
    },
    [vin, userId],
  );

  const fetchFn = useCallback(
    (v: string, sid: string, signal: AbortSignal) =>
      fetchArInstructions(v, sid, signal, userId, onForbidden),
    [userId, onForbidden],
  );

  return (
    <div className="ar-workbench-page">
      <h2 className="ar-workbench-heading">{t("ar_workbench.title")}</h2>
      <ARWorkInstructions
        vin={vin}
        stationId={STATION_ID}
        fetchInstructions={fetchFn}
        onOverlayRendered={handleOverlayRendered}
      />
    </div>
  );
}
