export type ResearchEvidence = { title: string; detail?: string | null; source?: string | null; observedAt?: string | null };
export type ResearchArtifact = {
  version: string;
  stockCode: string;
  stockName?: string | null;
  generatedAt?: string | null;
  thesis: string[];
  evidence: ResearchEvidence[];
  risks: string[];
  invalidationConditions: string[];
  dataQuality: Record<string, unknown>;
  sources: string[];
};
