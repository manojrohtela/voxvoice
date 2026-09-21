// Wire types for the endpoints the console reads. They mirror the server's response models
// (api/console/routers/meta.py and the M11 /health) and nothing more.

/** The console API revision this build was written against (`/v1/console/meta`). */
export const EXPECTED_CONSOLE_API_REVISION = "m12.1";

export interface RuntimeDescription {
  transport: string;
  webrtc_signalling: boolean;
  ice_policy: string;
}

export interface ConsoleMeta {
  api_version: "v1";
  console_api_revision: string;
  runtime: RuntimeDescription;
  capabilities: string[];
  console_bundle: boolean;
}

/** `GET /health` — owned by the M11 WebRTC application, read as-is. */
export interface Health {
  ok: boolean;
  db: boolean;
  transport?: string;
  webrtc_signalling?: boolean;
  ice_policy?: string;
}
