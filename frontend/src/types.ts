export type Role = "sales" | "mgmt";

export interface Session {
  token: string;
  role: Role;
  name: string;
  email: string;
}

export interface Candidate {
  code: string;
  desc: string;
  rel: string;
  grade: string | null;
  brand: string | null;
  score: number | null;
  reason: string;
  attributes: Record<string, unknown>;
}

export interface Economics {
  cost: number | null;
  list_price: number | null;
  recommended: number | null;
  quoted: number | null;
  margin: number | null;
  below_floor: boolean;
}

export interface LineStatus {
  kind: "ready" | "technical" | "operational" | "commercial";
  label: string;
}

export interface LineFlags {
  attention: boolean;
  procurement: boolean;
  missingBooks: boolean;
  manualReview: boolean;
  unresolved: boolean;
  substituted: boolean;
}

export interface Line {
  id: string;
  raw: string;
  reqCode: string;
  reqDesc: string;
  reqQty: number;
  rel: string;
  relLabel: string;
  supplyCode: string | null;
  supplyDesc: string;
  sel: "AUTO" | "USER" | "MANUAL";
  avail: number | null;
  availUnknown: boolean;
  inBooks: boolean | null;
  shortage: number | null;
  quoted: number | null;
  recommended: number | null;
  lineTotal: number | null;
  createPhase: string | null;
  service: string | null;
  incompatReason: string | null;
  status: LineStatus;
  flags: LineFlags;
  candidates: Candidate[];
  notes: string[];
  substituted: boolean;
  economics?: Economics;
}

export interface QuoteSummary {
  subtotal: number;
  gst: number;
  grand: number;
  total: number;
}

export interface MarginFloor {
  count: number;
  worst: number;
  floor: number;
}

export interface Quote {
  id: string;
  customer: string;
  number: string;
  savedAt: string | null;
  lines: Line[];
  summary: QuoteSummary;
  filterCounts: Record<string, number>;
  marginFloor: MarginFloor | null;
}
