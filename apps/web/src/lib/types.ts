export type Price = {
  amount: number;
  currency: string;
};

export type ProductImage = {
  url: string;
  alt: string | null;
  variant?: string | null;
  is_main?: boolean;
};

export type ProductVideo = {
  title: string | null;
  thumbnail_url: string | null;
  video_url: string | null;
  duration_seconds: number | null;
};

export type BSR = {
  rank: number;
  category: string;
};

export type Seller = {
  name: string;
  id: string | null;
  is_fba: boolean | null;
  rating: number | null;
};

export type Variation = {
  asin: string;
  label: string;
  attributes: Record<string, string>;
};

export type Product = {
  asin: string;
  marketplace: string;
  title: string;
  brand: string | null;
  price: Price | null;
  rating: number | null;
  review_count: number | null;
  bullet_points: string[];
  description: string | null;
  images: ProductImage[];
  videos?: ProductVideo[];
  category: string | null;
  bsr: BSR | null;
  availability: string | null;
  seller: Seller | null;
  variations: Variation[];
  last_fetched_at: string;
};

export type ProductSource = "mock" | "manual" | "amazon_public" | "rainforest";

export type ProductMeta = {
  source: ProductSource;
};

export type ProductResponse = {
  product: Product;
  meta: ProductMeta;
};

export type ManualProductInput = {
  asin: string;
  title: string;
  brand?: string | null;
  price?: number | null;
  currency?: string;
  rating?: number | null;
  review_count?: number | null;
  category?: string | null;
  bsr_rank?: number | null;
  bsr_category?: string | null;
  availability?: string | null;
  seller?: string | null;
  description?: string | null;
  bullet_points?: string[];
  image_urls?: string[];
  marketplace?: string | null;
};

export type ApiErrorBody = {
  detail?: string | Array<{ loc?: unknown[]; msg?: string }>;
};

export type FindingSeverity = "high" | "medium" | "low" | "info";

export type SectionStatus = "excellent" | "good" | "fair" | "poor";

export type Finding = {
  severity: FindingSeverity;
  category: string;
  code: string;
  message: string;
};

export type Recommendation = {
  code: string;
  category: string;
  message: string;
};

export type AnalysisSection = {
  name: string;
  score: number;
  max_score: number;
  status: SectionStatus;
  metrics: Record<string, unknown>;
  findings: string[];
};

export type AnalysisSections = {
  title: AnalysisSection;
  bullets: AnalysisSection;
  description: AnalysisSection;
  images: AnalysisSection;
  completeness: AnalysisSection;
  social_proof: AnalysisSection;
};

export type ListingAnalysis = {
  overall_score: number;
  score_version: string;
  sections: AnalysisSections;
  findings: Finding[];
  recommendations: Recommendation[];
};

export type AnalysisMeta = {
  engine: string;
  score_version: string;
  source: ProductSource | null;
};

export type ListingAnalysisResponse = {
  product: Product;
  analysis: ListingAnalysis;
  meta: AnalysisMeta;
};

export type ActionPriority = "high" | "medium" | "low";

export type PriorityAction = {
  priority: ActionPriority;
  title: string;
  reason: string;
  recommended_action: string;
};

export type TitleRecommendation = {
  current_title: string;
  suggested_title: string;
  rationale: string;
};

export type BulletRecommendation = {
  current: string;
  suggested: string;
  rationale: string;
};

export type SellerActionStep = {
  step: number;
  action: string;
  reason: string;
};

export type AIListingIntelligence = {
  executive_summary: string;
  strengths: string[];
  weaknesses: string[];
  priority_actions: PriorityAction[];
  title_recommendation: TitleRecommendation;
  bullet_recommendations: BulletRecommendation[];
  positioning_opportunities: string[];
  conversion_opportunities: string[];
  risks_and_cautions: string[];
  seller_action_plan: SellerActionStep[];
};

export type AITokenUsage = {
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
};

export type AIListingIntelligenceMeta = {
  engine: string;
  provider: string;
  model: string;
  prompt_version: string;
  source: ProductSource | null;
  usage: AITokenUsage | null;
  latency_ms: number | null;
};

export type AIListingIntelligenceResponse = {
  product: Product;
  analysis: ListingAnalysis;
  ai_intelligence: AIListingIntelligence;
  meta: AIListingIntelligenceMeta;
};

export type GapSeverity = "high" | "medium" | "low";

export type GapDirection = "below" | "above" | "missing";

export type FailedCompetitor = {
  asin: string;
  reason: string;
};

export type ComparisonMetric = {
  key: string;
  label: string;
  target_value: unknown;
  competitor_values: Record<string, unknown>;
  comparable: boolean;
  note: string | null;
};

export type PriceDelta = {
  competitor_asin: string;
  target_amount: number;
  competitor_amount: number;
  currency: string;
  absolute_difference: number;
  percentage_difference: number;
};

export type CompetitiveGap = {
  dimension: string;
  target_value: unknown;
  competitor_reference: unknown;
  competitor_asin: string | null;
  direction: GapDirection;
  severity: GapSeverity;
  evidence: string;
};

export type ComparedListing = {
  product: Product;
  analysis: ListingAnalysis;
};

export type ComparisonSummary = {
  requested_count: number;
  retrieved_count: number;
  listing_score_average: number | null;
  listing_score_median: number | null;
  target_listing_score: number;
  target_vs_average: number | null;
};

export type CompetitorComparison = {
  metrics: ComparisonMetric[];
  gaps: CompetitiveGap[];
  price_deltas: PriceDelta[];
  summary: ComparisonSummary;
};

export type CompetitorComparisonMeta = {
  source: string | null;
  comparison_version: string;
  score_version: string;
};

export type CompetitorComparisonResponse = {
  target: ComparedListing;
  competitors: ComparedListing[];
  comparison: CompetitorComparison;
  failed_competitors: FailedCompetitor[];
  meta: CompetitorComparisonMeta;
};

export type CompetitivePoint = {
  title: string;
  evidence: string;
  implication: string;
};

export type CompetitivePriorityGap = {
  priority: ActionPriority;
  dimension: string;
  evidence: string;
  recommended_action: string;
};

export type CompetitorObservation = {
  asin: string;
  observations: string[];
};

export type PricePositioning = {
  observation: string;
  caution: string;
};

export type CompetitiveActionStep = {
  step: number;
  action: string;
  evidence: string;
  reason: string;
};

export type AICompetitiveIntelligence = {
  executive_summary: string;
  competitive_position: string;
  target_advantages: CompetitivePoint[];
  target_disadvantages: CompetitivePoint[];
  priority_gaps: CompetitivePriorityGap[];
  competitor_observations: CompetitorObservation[];
  content_opportunities: string[];
  price_positioning: PricePositioning;
  seller_action_plan: CompetitiveActionStep[];
};

export type AICompetitiveIntelligenceMeta = {
  engine: string;
  provider: string;
  model: string;
  prompt_version: string;
  comparison_version: string;
  usage: AITokenUsage | null;
  latency_ms: number | null;
};

export type AICompetitiveIntelligenceResponse = {
  comparison: CompetitorComparisonResponse;
  ai_intelligence: AICompetitiveIntelligence;
  meta: AICompetitiveIntelligenceMeta;
};

export type DiscoveredProductCandidate = {
  asin: string;
  title: string;
  brand: string | null;
  price: number | null;
  currency: string | null;
  rating: number | null;
  review_count: number | null;
  image: string | null;
  position: number | null;
  is_sponsored: boolean | null;
  category: string | null;
  search_query: string;
  relevance_score: number;
};

export type CompetitorDiscoveryMeta = {
  provider: string;
  marketplace: string;
  discovery_version: string;
  query_generated: boolean;
  query_version: string;
  relevance_version: string;
  result_count: number;
  displayed_count: number;
};

export type CompetitorDiscoveryResult = {
  target_asin: string;
  search_query: string;
  candidates: DiscoveredProductCandidate[];
  meta: CompetitorDiscoveryMeta;
};

export type CompetitorSearchQueryResponse = {
  search_query: string;
  meta: Record<string, string>;
};

export type DecimalString = string;

export type ReportFindingSeverity = "high" | "medium" | "low" | "info";

export type PpcMetrics = {
  impressions: number;
  clicks: number;
  spend: DecimalString;
  sales: DecimalString;
  orders: number;
  units: number | null;
  ctr: DecimalString | null;
  cpc: DecimalString | null;
  cvr: DecimalString | null;
  acos: DecimalString | null;
  roas: DecimalString | null;
};

export type SearchTermSummary = PpcMetrics & {
  search_term: string;
  campaign_count: number;
};

export type CampaignSummary = PpcMetrics & {
  campaign_name: string;
  campaign_id: string | null;
};

export type WastedSpendRow = {
  search_term: string;
  spend: DecimalString;
  clicks: number;
  orders: number;
  sales: DecimalString;
  reason_code: string;
  reason: string;
  severity: ReportFindingSeverity;
};

export type NegativeKeywordCandidate = {
  search_term: string;
  spend: DecimalString;
  clicks: number;
  orders: number;
  sales: DecimalString;
  reason_code: string;
  severity: ReportFindingSeverity;
  message: string;
};

export type ReportFinding = {
  code: string;
  severity: ReportFindingSeverity;
  message: string;
  entity: string | null;
};

export type ProductPerformanceRow = {
  asin: string;
  title: string | null;
  sku: string | null;
  sessions: number;
  page_views: number | null;
  units_ordered: number | null;
  ordered_product_sales: DecimalString | null;
  conversion: DecimalString | null;
  buy_box_percentage: DecimalString | null;
};

export type BusinessSummary = {
  sessions: number;
  page_views: number | null;
  units_ordered: number | null;
  ordered_product_sales: DecimalString | null;
  conversion: DecimalString | null;
  buy_box_percentage: DecimalString | null;
  asin_count: number;
};

export type ReportAnalysisMeta = {
  parser_version: string;
  analytics_version: string;
  filename: string | null;
  file_size_bytes: number;
  source_format: string;
  valid_rows: number;
  invalid_rows: number;
  currency: string;
};

export type SearchTermReportAnalysis = {
  report_type: "search_term_report";
  summary: PpcMetrics;
  findings: ReportFinding[];
  tables: {
    wasted_spend: WastedSpendRow[];
    negative_keyword_candidates: NegativeKeywordCandidate[];
    search_terms: SearchTermSummary[];
    campaigns: CampaignSummary[];
    strong_search_terms: SearchTermSummary[];
  };
  warnings: string[];
  meta: ReportAnalysisMeta;
};

export type BusinessReportAnalysis = {
  report_type: "business_report";
  summary: BusinessSummary;
  findings: ReportFinding[];
  tables: {
    products: ProductPerformanceRow[];
  };
  warnings: string[];
  meta: ReportAnalysisMeta;
};

export type ReportAnalysisResponse = SearchTermReportAnalysis | BusinessReportAnalysis;

export type UsageWarningLevel = "normal" | "warning" | "critical" | "unknown";
export type AccountStatus = "ok" | "unavailable" | "not_configured";

export type RainforestUsagePoint = {
  date: string;
  credits_used: number;
};

export type RainforestAccountUsage = {
  source: "rainforest_account_api";
  available: boolean;
  status: AccountStatus;
  credits_used: number | null;
  credits_limit: number | null;
  credits_remaining: number | null;
  usage_percentage: number | null;
  warning_level: UsageWarningLevel;
  reset_at: string | null;
  usage_history: RainforestUsagePoint[];
  last_updated: string | null;
  message: string | null;
};

export type RainforestAppUsage = {
  source: "application_ledger";
  product_calls: number;
  search_calls: number;
  cache_hits: number;
  calls_saved: number;
  failed_calls: number;
};

export type OpenAIAccountUsage = {
  source: "openai_organization_costs_api";
  available: boolean;
  status: AccountStatus;
  spend_usd: number | null;
  budget_usd: number | null;
  usage_percentage: number | null;
  warning_level: UsageWarningLevel;
  period_start: string | null;
  last_updated: string | null;
  message: string | null;
};

export type OpenAIAppUsage = {
  source: "application_ledger";
  estimated_spend_usd: number | null;
  cost_status: "ok" | "unavailable" | "partial";
  requests: number;
  input_tokens: number;
  cached_input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cache_hits: number;
  calls_saved: number;
  failed_calls: number;
  unpriced_requests: number;
};

export type UsageDashboardResponse = {
  rainforest: {
    account: RainforestAccountUsage;
    app: RainforestAppUsage;
  };
  openai: {
    account: OpenAIAccountUsage;
    app: OpenAIAppUsage;
  };
};

export type BulkAnalysisMode = "standard" | "deep_ai";
export type BulkAISelection = "high_priority" | "top_n" | "all";
export type BulkJobStatus =
  | "queued"
  | "running"
  | "completed"
  | "completed_with_errors"
  | "failed";

export type BulkIngestStats = {
  filename: string;
  input_rows: number;
  valid_rows: number;
  invalid_rows: number;
  duplicate_rows_removed: number;
  unique_asins: number;
  asin_column: string | null;
  unique_asin_list?: string[];
  invalid_samples?: BulkFailure[];
};

export type BulkFailure = {
  row: number | null;
  input_asin: string;
  reason: string;
  kind: "invalid" | "not_found" | "transient" | "provider";
};

export type BulkASINProductResult = {
  asin: string;
  status: "success";
  product: Product;
  listing_analysis: ListingAnalysis;
  ai_intelligence: {
    executive_summary: string;
    priority_actions: { title: string }[];
    title_recommendation: { suggested_title: string };
    seller_action_plan: { action: string }[];
  } | null;
  priority: "high" | "medium" | "low";
  cache_hit: boolean;
  ai_status: "not_requested" | "skipped" | "mock" | "cached";
};

export type BulkPortfolioSummary = {
  products_submitted: number;
  products_analyzed: number;
  products_failed: number;
  average_listing_score: number | null;
  median_listing_score: number | null;
  high_priority_count: number;
  medium_priority_count: number;
  low_priority_count: number;
  missing_description_count: number;
  low_image_count: number;
  weak_bullet_count: number;
  low_completeness_count: number;
  average_rating: number | null;
  average_review_count: number | null;
  average_image_count: number | null;
};

export type BulkJobResponse = {
  job_id: string;
  status: BulkJobStatus;
  options: {
    analysis_mode: BulkAnalysisMode;
    ai_selection: BulkAISelection;
    top_n: number;
    marketplace: string;
  };
  ingest: BulkIngestStats;
  progress: {
    total: number;
    processed: number;
    successful: number;
    failed: number;
    cache_hits: number;
    provider_calls: number;
  };
  usage: {
    product_provider: string;
    ai_provider: string | null;
    paid_api_usage: boolean;
    note: string;
    requested_asins: number;
    cache_hits: number;
    provider_calls: number;
    calls_saved: number;
    failures: number;
    retries: number;
    ai_eligible: number;
    ai_cache_hits: number;
    ai_provider_calls: number;
    ai_calls_saved: number;
  };
  summary: BulkPortfolioSummary | null;
  results: BulkASINProductResult[];
  failures: BulkFailure[];
  attention: BulkASINProductResult[];
  error: string | null;
  created_at: string;
  updated_at: string;
  live_providers_enabled: boolean;
};
