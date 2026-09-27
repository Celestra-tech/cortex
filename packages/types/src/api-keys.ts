/** Mirrors `cortex_api.schemas.api_key`. Keep both sides in sync. */

import type { Id, Timestamp } from "./memory";

/** `admin` keys can also manage the organization's API keys. */
export type ApiKeyRole = "admin" | "member";

export type ApiKeyStatus = "active" | "expired" | "revoked";

export interface ApiKey {
  id: Id;
  organization_id: Id;
  name: string;
  /** Leading characters of the secret, safe to display. */
  prefix: string | null;
  role: ApiKeyRole;
  status: ApiKeyStatus;
  created_at: Timestamp;
  last_used_at: Timestamp | null;
  expires_at: Timestamp | null;
  revoked_at: Timestamp | null;
  rotated_from_id: Id | null;
}

/** Returned once, at creation or rotation. Store `secret` immediately. */
export interface ApiKeyWithSecret extends ApiKey {
  secret: string;
}

export interface ApiKeyCreate {
  name: string;
  role?: ApiKeyRole;
  expires_in_days?: number | null;
}

export interface ApiKeyRotate {
  /** How long the old key keeps working; 0 revokes it immediately. Defaults to 3600. */
  grace_period_seconds?: number;
  expires_in_days?: number | null;
}

export interface ApiKeyListResponse {
  items: ApiKey[];
}
