export interface StrategyFeatureConfig {
  status: "enabled" | "disabled" | "not_applicable" | "unknown" | string;
  [key: string]: unknown;
}

export interface StrategySnapshot {
  version: number;
  strategy: string;
  evaluator: string | null;
  profile: string | null;
  profile_fingerprint: string | null;
  features: Record<string, StrategyFeatureConfig>;
  profile_config: Record<string, unknown>;
  config_hash: string;
  runtime?: Record<string, unknown>;
  model_name?: string | null;
  commit_sha?: string | null;
}

export interface DecisionAuditFeature {
  configured?: boolean | null;
  eligible?: boolean | null;
  entered?: boolean | null;
  completed?: boolean | null;
  fallback?: boolean;
  timeout?: boolean;
  skip_reason?: string | null;
  intent?: string | string[] | null;
  candidate_count?: number | null;
  draw_nodes?: number | null;
  winner?: number | string | null;
}

export interface DecisionAudit {
  version: number;
  decision_id?: number | null;
  strategy_config_hash?: string | null;
  decision_scope: string;
  phase?: string | null;
  features: Record<string, DecisionAuditFeature>;
  result: { action: unknown; selected?: unknown };
  runtime: {
    elapsed_ms?: number | null;
    timeout?: boolean;
    partial?: boolean;
    fallback?: boolean;
    fallback_reason?: string | null;
    budget?: Record<string, unknown>;
  };
}
