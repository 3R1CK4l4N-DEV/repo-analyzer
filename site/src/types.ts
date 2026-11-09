export interface PublishSource {
  repo_url: string | null;
  ref: string | null;
  commit: string | null;
  subdir: string | null;
}

export interface LanguageSummary {
  language: string;
  confidence: number;
  files: number;
  loc: number;
}

export interface Technology {
  name: string;
  version: string | null;
}

export interface GraphStats {
  nodes: number;
  edges: number;
  nodes_by_kind: Record<string, number>;
  edges_by_kind: Record<string, number>;
  stereotypes: Record<string, number>;
}

export interface ProjectSummary {
  files: number;
  loc: number;
  languages: LanguageSummary[];
  build_systems: string[];
  versions: Record<string, string>;
  frameworks: Technology[];
  stereotypes: Record<string, number>;
  units: number;
  endpoints: number;
  tables: number;
  dependencies: number;
  rules: number;
  terms: number;
  graph: GraphStats;
}

export interface Artifacts {
  analysis: string;
  graph: string;
  graphml?: string;
  report_html: string;
  report_md: string;
  vault_zip: string;
  kb?: string;
  knowledge?: string;
  changes_md?: string;
  changes_json?: string;
}

export interface Manifest {
  schema_version: number;
  slug: string;
  name: string;
  source: PublishSource;
  generated_at: string;
  devanalyzer_version: string;
  summary: ProjectSummary;
  artifacts: Artifacts;
  sizes: Record<string, number>;
  base: string;
}

export interface ProjectsIndex {
  schema_version: number;
  generated_at: string;
  projects: Manifest[];
}

export interface Dependency {
  name: string;
  version: string | null;
  scope: string;
  source_file: string | null;
}

export interface BuildInfo {
  system: string;
  manifest_path: string;
  versions: Record<string, string>;
  dependencies: Dependency[];
  frameworks: Technology[];
  project_name: string | null;
}

export interface Endpoint {
  http_method: string;
  path: string;
  handler: string;
  file: string;
  line: number;
  framework: string | null;
}

export interface ProjectMetrics {
  files: number;
  loc: number;
  stereotypes: Record<string, number>;
  units_by_kind: Record<string, number>;
  fan_in: Record<string, number>;
  fan_out: Record<string, number>;
}

export interface LanguageAnalysis {
  language: string;
  confidence: number;
  files: number;
  loc: number;
  units: unknown[];
  endpoints: Endpoint[];
  relations: unknown[];
  metrics: ProjectMetrics;
}

export interface Column {
  name: string;
  type: string | null;
  nullable: boolean;
  primary_key: boolean;
}

export interface ForeignKey {
  column: string;
  ref_table: string;
  ref_column: string | null;
}

export interface DatabaseTable {
  name: string;
  columns: Column[];
  foreign_keys: ForeignKey[];
  source_file: string;
  line: number;
  tool: string;
  mapped_entities: string[];
}

export interface ProjectAnalysis {
  project_name: string;
  project_path: string;
  languages: LanguageAnalysis[];
  builds: BuildInfo[];
  metrics: ProjectMetrics;
  db_schema: {
    tables: DatabaseTable[];
    sources: string[];
  };
}

export interface GraphNode {
  id: string;
  kind: string;
  name?: string;
  fqn?: string;
  stereotype?: string | null;
  file?: string;
  line?: number;
  line_end?: number;
  loc?: number;
  [key: string]: unknown;
}

export interface GraphEdge {
  source: string;
  target: string;
  key: string;
  kind?: string;
  [key: string]: unknown;
}

export interface GraphData {
  directed: boolean;
  multigraph: boolean;
  graph: Record<string, unknown>;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface Evidence {
  file: string;
  line_start: number;
  line_end: number;
  unit?: string | null;
  snippet?: string | null;
}

export interface BusinessRule {
  id: string;
  statement: string;
  rationale: string | null;
  category: string;
  domain_terms: string[];
  evidence: Evidence[];
  confidence: number;
  status: "inferred" | "confirmed" | "rejected";
  tags: string[];
  source_unit: string | null;
  model: string | null;
  review_note: string | null;
}

export interface GlossaryTerm {
  term: string;
  definition: string;
  aliases: string[];
  related_units: string[];
  rules: string[];
}

export interface KnowledgeData {
  rules: BusinessRule[];
  glossary: GlossaryTerm[];
  decisions: Array<Record<string, unknown>>;
  groups: Array<Record<string, unknown>>;
}

export interface AnalysisDiff {
  units_added: string[];
  units_removed: string[];
  methods_added: string[];
  methods_removed: string[];
  endpoints_added: string[];
  endpoints_removed: string[];
  dependencies_added: string[];
  dependencies_removed: string[];
  dependencies_changed: string[];
  versions_changed: string[];
  relations_added: number;
  relations_removed: number;
  loc_before: number;
  loc_after: number;
}

export interface ServiceLink {
  kind: string;
  source: string;
  target: string;
  label: string;
  evidence_file: string | null;
  evidence_line: number | null;
  detail: string | null;
}

export interface WorkspaceProject {
  name: string;
  path: string;
  languages: string[];
  frameworks: string[];
  endpoints: number;
  tables: string[];
  loc: number;
}

export interface WorkspaceData {
  name: string;
  projects: WorkspaceProject[];
  links: ServiceLink[];
  topics: Record<string, { producers: string[]; consumers: string[] }>;
  unresolved_calls: Array<Record<string, unknown>>;
}
