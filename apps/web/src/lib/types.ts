export type Price = {
  amount: number;
  currency: string;
};

export type ProductImage = {
  url: string;
  alt: string | null;
  variant?: string | null;
  is_main?: boolean;
  width?: number | null;
  height?: number | null;
};

export type ProductVideo = {
  title: string | null;
  thumbnail_url: string | null;
  video_url: string | null;
  duration_seconds: number | null;
  group_type?: string | null;
  group_id?: string | null;
  width?: number | null;
  height?: number | null;
};

export type BSR = {
  rank: number;
  category: string;
};

export type CategoryNode = {
  name: string;
  category_id: string | null;
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
  is_current_product?: boolean | null;
};

export type ProductSpecification = {
  name: string;
  value: string;
};

export type ProductAttributes = {
  manufacturer: string | null;
  ingredients: string[];
  diet_type: string[];
  listed: ProductSpecification[];
};

export type APlusImage = {
  url: string;
  alt: string | null;
};

export type BrandStory = {
  hero_image: string | null;
  brand_logo: string | null;
  description: string | null;
  images: string[];
};

export type APlusContent = {
  has_a_plus_content: boolean | null;
  has_brand_story: boolean | null;
  third_party: boolean | null;
  company_logo: string | null;
  company_description: string | null;
  body_text: string | null;
  images: APlusImage[];
  brand_story: BrandStory | null;
};

export type RatingBand = {
  percentage: number | null;
  count: number | null;
};

export type RatingBreakdown = {
  five_star: RatingBand | null;
  four_star: RatingBand | null;
  three_star: RatingBand | null;
  two_star: RatingBand | null;
  one_star: RatingBand | null;
};

export type FeaturedReview = {
  id: string | null;
  title: string | null;
  body: string | null;
  rating: number | null;
  profile_name: string | null;
  verified_purchase: boolean | null;
  date_raw: string | null;
  date_utc: string | null;
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
  bsr_ranks?: BSR[];
  category_path?: CategoryNode[];
  is_sold_by_amazon?: boolean | null;
  availability_type?: string | null;
  videos_count?: number | null;
  a_plus?: APlusContent | null;
  specifications?: ProductSpecification[];
  specifications_flat?: string | null;
  attributes?: ProductAttributes | null;
  rating_breakdown?: RatingBreakdown | null;
  featured_reviews?: FeaturedReview[];
  recent_sales_text?: string | null;
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
  report_id?: string | null;
  persisted?: boolean;
  persistence_warning?: string | null;
  scoring_profile?: ScoringProfileSnapshot | null;
};

export type ListingAnalysisResponse = {
  product: Product;
  analysis: ListingAnalysis;
  meta: AnalysisMeta;
};

export type EvidenceState = "observed" | "reported_absent" | "unknown";

export type CoverageField = {
  name: string;
  evidence_state: EvidenceState;
  available: boolean;
  note: string | null;
};

export type CoverageGroup = {
  name: string;
  available: number;
  expected: number;
  percentage: number;
  status: SectionStatus;
  fields: CoverageField[];
  notes: string[];
};

export type DataCoverage = {
  overall_percentage: number;
  core_listing_content: CoverageGroup;
  media: CoverageGroup;
  enhanced_content: CoverageGroup;
  category_context: CoverageGroup;
  market_signals: CoverageGroup;
};

export type MarketSignals = {
  rating: number | null;
  review_count: number | null;
  price: Price | null;
  availability: string | null;
  availability_type: string | null;
  is_sold_by_amazon: boolean | null;
  seller: Seller | null;
  bsr_ranks: BSR[];
  recent_sales_text: string | null;
  rating_breakdown: RatingBreakdown | null;
};

export type ListingQualitySections = {
  title: AnalysisSection;
  bullets: AnalysisSection;
  description_a_plus: AnalysisSection;
  media_coverage: AnalysisSection;
  content_structure: AnalysisSection;
};

export type V2Recommendation = {
  code: string;
  category: string;
  priority: ActionPriority;
  action: string;
  finding_code: string;
};

export type ListingAnalysisV2 = {
  listing_quality_score: number;
  score_version: string;
  status: SectionStatus;
  sections: ListingQualitySections;
  market_signals: MarketSignals;
  data_coverage: DataCoverage;
  findings: Finding[];
  recommendations: V2Recommendation[];
};

export type ListingAnalysisV2Response = {
  product: Product;
  analysis: ListingAnalysisV2;
  custom_score?: CustomScoreResult | null;
  meta: AnalysisMeta;
};

export type ScoringWeights = {
  title: number;
  bullets: number;
  description_a_plus: number;
  media: number;
  content_structure: number;
};

export type ScoringProfileSnapshot = {
  profile_id: string;
  profile_name: string;
  type: "standard" | "custom";
  weights: ScoringWeights;
};

export type CustomScoreResult = {
  custom_listing_quality_score: number;
  profile: ScoringProfileSnapshot;
};

export type ScoringProfile = {
  id: string;
  name: string;
  description: string | null;
  weights: ScoringWeights;
  is_system: boolean;
  is_default: boolean;
  is_archived: boolean;
  editable: boolean;
  deletable: boolean;
  created_at: string | null;
  updated_at: string | null;
  archived_at: string | null;
};

export type ScoringProfileListResponse = {
  items: ScoringProfile[];
};

export type ListingReweightResponse = {
  analysis: ListingAnalysisV2;
  standard_listing_quality_score: number;
  custom_score: CustomScoreResult | null;
  persisted: boolean;
  preview: boolean;
};

export const STANDARD_SCORING_PROFILE_ID = "standard-v2";

export const STANDARD_V2_WEIGHTS: ScoringWeights = {
  title: 20,
  bullets: 25,
  description_a_plus: 20,
  media: 20,
  content_structure: 15,
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
  report_id?: string | null;
  persisted?: boolean;
  persistence_warning?: string | null;
};

export type AIListingIntelligenceResponse = {
  product: Product;
  analysis: ListingAnalysis;
  ai_intelligence: AIListingIntelligence;
  meta: AIListingIntelligenceMeta;
};

export type PriorityActionV2 = {
  priority: ActionPriority;
  area: string;
  issue: string;
  why_it_matters: string;
  recommended_action: string;
  evidence_codes: string[];
};

export type TitleContentInsight = {
  assessment: string;
  strengths: string[];
  gaps: string[];
};

export type BulletContentInsight = TitleContentInsight & {
  seo_readiness_notes: string[];
};

export type DescriptionContentInsight = TitleContentInsight;

export type APlusContentInsight = TitleContentInsight & {
  evidence_state: EvidenceState;
};

export type StructureContentInsight = {
  assessment: string;
  redundancy_notes: string[];
  coverage_gaps: string[];
};

export type ContentAnalysisV2 = {
  title: TitleContentInsight;
  bullets: BulletContentInsight;
  description: DescriptionContentInsight;
  a_plus: APlusContentInsight;
  structure: StructureContentInsight;
};

export type SpecificationCoverage = {
  represented: string[];
  missing_from_customer_copy: string[];
  not_recommended_for_copy: string[];
};

export type RewriteSuggestions = {
  suggested_title: string;
  suggested_bullets: string[];
  optional_description_excerpt: string | null;
};

export type SellerActionStepV2 = {
  step: number;
  action: string;
  priority: ActionPriority;
  rationale: string;
};

export type AIListingIntelligenceV2 = {
  executive_assessment: string;
  priority_actions: PriorityActionV2[];
  content_analysis: ContentAnalysisV2;
  specification_coverage: SpecificationCoverage;
  rewrite_suggestions: RewriteSuggestions;
  seller_action_plan: SellerActionStepV2[];
  confidence_notes: string[];
};

export type AIListingIntelligenceV2Response = {
  product: Product;
  analysis: ListingAnalysisV2;
  ai_intelligence: AIListingIntelligenceV2;
  meta: AIListingIntelligenceMeta;
};

export type VisualRole =
  | "product_only"
  | "feature"
  | "benefit"
  | "lifestyle"
  | "dimensions"
  | "how_to_use"
  | "packaging"
  | "comparison"
  | "detail_closeup"
  | "other";

export type ImageFinding = {
  severity: FindingSeverity;
  image_ids: string[];
  evidence_type: string;
  observation: string;
  recommendation: string;
};

export type ImageAreaAnalysis = {
  assessment: string;
  strengths: string[];
  concerns: string[];
  image_ids: string[];
  product_visibility?: string | null;
  background_characteristics?: string | null;
  embedded_text_notes?: string | null;
};

export type GalleryVisualAnalysis = {
  assessment: string;
  observed_roles: VisualRole[];
  coverage_opportunities: string[];
  image_ids: string[];
};

export type APlusVisualAnalysis = {
  evidence_state: EvidenceState;
  assessment: string;
  strengths: string[];
  gaps: string[];
  image_ids: string[];
};

export type BrandStoryVisualAnalysis = APlusVisualAnalysis;

export type MediaRoleCoverage = {
  observed: VisualRole[];
  not_observed: VisualRole[];
  notes: string[];
};

export type RecommendedImagePlanStep = {
  step: number;
  slot: string;
  purpose: string;
  grounded_in: string;
};

export type PriorityVisualImprovement = {
  priority: ActionPriority;
  issue: string;
  why_it_matters: string;
  recommended_action: string;
  image_ids: string[];
};

export type AIImageIntelligence = {
  executive_assessment: string;
  visual_strengths: string[];
  priority_improvements: PriorityVisualImprovement[];
  main_image_analysis: ImageAreaAnalysis;
  gallery_analysis: GalleryVisualAnalysis;
  a_plus_visual_analysis: APlusVisualAnalysis;
  brand_story_analysis: BrandStoryVisualAnalysis;
  media_role_coverage: MediaRoleCoverage;
  redundancy_analysis: string[];
  image_findings: ImageFinding[];
  recommended_image_plan: RecommendedImagePlanStep[];
  confidence_notes: string[];
};

export type AIImageIntelligenceMeta = AIListingIntelligenceMeta & {
  engine: string;
  images_available: number;
  images_selected: number;
  images_skipped: number;
  selection_reason: string | null;
  warnings: string[];
};

export type AIImageIntelligenceResponse = {
  product: Product;
  analysis: ListingAnalysisV2;
  image_intelligence: AIImageIntelligence;
  meta: AIImageIntelligenceMeta;
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

export type SavedAnalysisSummary = {
  report_id: string;
  asin: string;
  product_title: string | null;
  brand: string | null;
  marketplace: string;
  listing_quality_score: number | null;
  custom_listing_quality_score?: number | null;
  scoring_profile_name?: string | null;
  source: string | null;
  has_ai_strategy: boolean;
  has_image_intelligence: boolean;
  created_at: string;
  completed_at: string | null;
  status: string;
  display_name: string | null;
};

export type SavedAnalysisListResponse = {
  items: SavedAnalysisSummary[];
  total: number;
  offset: number;
  limit: number;
};

export type SavedAnalysisMetadata = {
  historical: boolean;
  analyzed_at: string | null;
  product_fetched_at: string | null;
  product_source: ProductSource | string | null;
  listing_score_version: string | null;
  ai_prompt_version: string | null;
  image_prompt_version: string | null;
  ai_provider: string | null;
  ai_model: string | null;
  image_provider: string | null;
  image_model: string | null;
  images_available: number | null;
  images_selected: number | null;
  images_skipped: number | null;
  status: string;
};

export type SavedAnalysisDetail = {
  report_id: string;
  display_name: string | null;
  product: Product;
  analysis: ListingAnalysisV2;
  custom_score?: CustomScoreResult | null;
  ai_intelligence: AIListingIntelligenceV2 | null;
  image_intelligence: AIImageIntelligence | null;
  meta: SavedAnalysisMetadata;
};

export type CopilotEvidenceClaim = {
  key: string;
  value: unknown;
  kind: string;
  source: string;
  confidence?: string;
  as_of?: string | null;
  notes?: string | null;
};

export type CopilotEvidenceEnvelope = {
  evidence_id: string;
  tool_name: string;
  organization_id: string;
  produced_at: string;
  claims: CopilotEvidenceClaim[];
};

export type CopilotApprovedToolCall = {
  name: string;
  arguments: Record<string, unknown>;
};

export type CopilotPlan = {
  plan_id: string;
  conversation_id: string;
  organization_id: string;
  intent: string;
  tool_calls: CopilotApprovedToolCall[];
  needs_confirmation: boolean;
  confirm_summary: string | null;
  plan_hash: string;
  validation_status: string;
  source: string;
};

export type CopilotToolCallResult = {
  name: string;
  status: string;
  evidence: CopilotEvidenceEnvelope | null;
  error_code: string | null;
  error_message: string | null;
};

export type CopilotExecutionResult = {
  plan_id: string;
  conversation_id: string;
  organization_id: string;
  plan_hash: string;
  status: "succeeded" | "blocked_confirmation" | "failed";
  confirmation_required: boolean;
  confirmation_nonce: string | null;
  confirm_summary: string | null;
  evidence: CopilotEvidenceEnvelope[];
  tool_results: CopilotToolCallResult[];
};

export type CopilotCitation = {
  evidence_id: string;
  claim_key: string;
  tool_name: string;
  label: string;
};

export type CopilotSynthesizedResponse = {
  summary: string;
  findings: string[];
  recommendations: string[];
  citations: CopilotCitation[];
  confidence: string;
  unknowns: string[];
  source: "synthesis_llm" | "template_fallback" | "rewritten_citations";
  prompt_version: string | null;
  synthesis_model: string | null;
  message: string;
};

export type CopilotPendingConfirmationPublic = {
  plan_id: string | null;
  nonce_present: boolean;
  summary: string | null;
};

export type CopilotCompactContext = {
  last_asin: string | null;
  last_report_id: string | null;
  previous_intent: string | null;
  pending_confirmation: CopilotPendingConfirmationPublic | null;
  evidence_refs: { evidence_id: string; claim_keys: string[] }[];
  recent_user_snippets: string[];
};

export type CopilotConversationDetail = {
  id: string;
  organization_id: string;
  status: string;
  title: string | null;
  last_asin: string | null;
  last_report_id: string | null;
  previous_intent: string | null;
  compact_context: CopilotCompactContext;
  pending_confirmation: CopilotPendingConfirmationPublic | null;
};

export type ProfitEvidenceClaim = {
  key: string;
  value: unknown;
  kind: string;
  source: string;
  confidence: string;
  notes: string | null;
};

export type ProfitEvidence = {
  evidence_id: string;
  tool_name: string;
  organization_id: string;
  produced_at: string;
  claims: ProfitEvidenceClaim[];
};

export type ProfitInputs = {
  selling_price: string | null;
  cogs: string | null;
  referral_fee: string | null;
  fba_fee: string | null;
  shipping_cost: string | null;
  packaging_cost: string | null;
  other_cost: string | null;
};

export type ProfitOutputs = {
  amazon_fees: string | null;
  operating_costs: string | null;
  landed_cost: string | null;
  net_profit_before_ads: string | null;
  margin_before_ads: string | null;
  roi_on_cogs: string | null;
};

export type ProfitCompleteness = {
  unknown: string[];
  messages: string[];
};

export type ProfitSnapshot = {
  id: string;
  organization_id: string;
  profit_model_id: string;
  status: string;
  profit_formula_version: string;
  inputs: ProfitInputs;
  outputs: ProfitOutputs;
  completeness: ProfitCompleteness;
  evidence: ProfitEvidence;
  calculated_at: string;
};

export type ProfitModel = {
  id: string;
  organization_id: string;
  asin: string;
  marketplace: string;
  currency: string;
  selling_price: string | null;
  selling_price_source: string;
  cogs: string | null;
  shipping_cost: string | null;
  packaging_cost: string | null;
  other_cost: string | null;
  referral_fee_amount: string | null;
  fba_fee_amount: string | null;
  fee_category_key: string | null;
  latest_snapshot: ProfitSnapshot | null;
  created_at: string;
  updated_at: string;
};

export type ProfitModelSummary = {
  id: string;
  asin: string;
  marketplace: string;
  currency: string;
  latest_status: string | null;
  unknown: string[];
  updated_at: string;
};

export type ProfitModelListResponse = {
  items: ProfitModelSummary[];
  total: number;
};

export type ProfitModelInputs = {
  selling_price?: string | null;
  cogs?: string | null;
  shipping_cost?: string | null;
  packaging_cost?: string | null;
  other_cost?: string | null;
  referral_fee_amount?: string | null;
  fba_fee_amount?: string | null;
  selling_price_source?: string | null;
  fee_category_key?: string | null;
};

export type AdvertisingInputs = {
  ad_spend: string | null;
  ad_sales: string | null;
  total_sales: string | null;
  units_in_period: string | null;
  period_start: string | null;
  period_end: string | null;
};

export type AdvertisingOutputs = {
  acos: string | null;
  tacos: string | null;
  roas: string | null;
};

export type AdvertisingImpact = {
  ad_spend_per_unit: string | null;
  net_profit_after_ads: string | null;
  break_even_acos: string | null;
  profit_snapshot_id: string | null;
  unknown: string[];
  messages: string[];
};

export type AdvertisingSnapshot = {
  id: string;
  organization_id: string;
  advertising_model_id: string;
  profit_model_id: string;
  status: string;
  ads_formula_version: string;
  inputs: AdvertisingInputs;
  outputs: AdvertisingOutputs;
  completeness: ProfitCompleteness;
  impact: AdvertisingImpact | null;
  evidence: ProfitEvidence;
  calculated_at: string;
};

export type AdvertisingSnapshotSummary = {
  id: string;
  status: string;
  period_start: string | null;
  period_end: string | null;
  acos: string | null;
  tacos: string | null;
  calculated_at: string;
};

export type AdvertisingSnapshotListResponse = {
  items: AdvertisingSnapshotSummary[];
  total: number;
};

export type AdvertisingModel = {
  id: string;
  organization_id: string;
  profit_model_id: string;
  asin: string;
  marketplace: string;
  currency: string;
  source: string;
  period_start: string | null;
  period_end: string | null;
  ad_spend: string | null;
  ad_sales: string | null;
  total_sales: string | null;
  units_in_period: string | null;
  latest_snapshot: AdvertisingSnapshot | null;
  impact: AdvertisingImpact | null;
  profit_snapshot_stale: boolean;
  created_at: string;
  updated_at: string;
};

export type AdvertisingModelInputs = {
  period_start?: string | null;
  period_end?: string | null;
  ad_spend?: string | null;
  ad_sales?: string | null;
  total_sales?: string | null;
  units_in_period?: string | null;
};

export type AmazonConnectionStatus = "CONNECTED" | "NOT_CONNECTED" | "FAILED";

export type AmazonConnectionLifecycleStatus =
  | "not_connected"
  | "pending_authorization"
  | "pending_validation"
  | "connected"
  | "degraded"
  | "revoked"
  | "error";

export type AmazonConnectionEnvironment = "SANDBOX" | "PRODUCTION";

export type AmazonAdsConnectionPlaceholder = {
  provider: "ADS_API";
  status: "NOT_CONNECTED";
};

export type AmazonSellerMarketplace = {
  // 12B.3F: the marketplace participation's own id — the value the
  // Seller Listings Read API's `{marketplace_participation_id}` path
  // segment expects. Optional only so a response from before this field
  // existed doesn't crash older cached data; the UI must not attempt to
  // address the Listings API for a marketplace missing this field.
  id?: string;
  marketplace_id: string;
  name: string | null;
  country_code: string | null;
  domain_name: string | null;
  is_participating: boolean;
  has_suspended_listings: boolean;
  is_active: boolean;
  last_seen_at: string;
};

// --- 12B.3F: Seller Listings Read API (12B.3E backend contract) -----------

export type ListingsSyncStatus =
  | "never_synchronized"
  | "queued"
  | "running"
  | "waiting_to_retry"
  | "succeeded"
  | "failed"
  | "partial"
  | "timed_out";

export type ListingIssueSeverity = "ERROR" | "WARNING" | "INFO";

export type ListingSortField =
  | "last_seen_at"
  | "first_seen_at"
  | "seller_sku"
  | "asin"
  | "issue_count"
  | "price_amount";

export type SortDirection = "asc" | "desc";

export type ListingsSyncEvidence = {
  status: ListingsSyncStatus;
  failure_class: string | null;
  // 12B.3G follow-up: the only truthful "how long has this been waiting"
  // signal while `status === "queued"` (started_at is null until a
  // worker actually claims the run) — used to detect a stale queue.
  queued_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  pages_fetched: number | null;
  records_received: number | null;
  records_accepted: number | null;
  records_rejected: number | null;
  reported_total_results: number | null;
  pagination_complete: boolean | null;
  last_successful_synchronized_at: string | null;
  // 12B.3G: only meaningful while `status === "waiting_to_retry"`.
  next_retry_at: string | null;
};

export type ListingsSummary = {
  marketplace_participation_id: string;
  total_listings: number;
  active_count: number;
  inactive_count: number;
  buyable_count: number;
  not_buyable_count: number;
  discoverable_count: number;
  not_discoverable_count: number;
  with_issues_count: number;
  without_issues_count: number;
  issue_severity_error_count: number;
  issue_severity_warning_count: number;
  issue_severity_info_count: number;
  with_asin_count: number;
  with_consumer_price_count: number;
  with_fulfillment_availability_count: number;
  sync: ListingsSyncEvidence;
};

export type ListingCollectionItem = {
  id: string;
  seller_sku: string;
  asin: string | null;
  product_type: string | null;
  is_active: boolean;
  is_buyable: boolean;
  is_discoverable: boolean;
  // Serialized as a decimal string by the backend (never a float), so
  // precision is never lost in transit. Format for display; never treat
  // a missing value as zero.
  price_amount: string | null;
  price_currency: string | null;
  issue_count: number;
  highest_issue_severity: ListingIssueSeverity | null;
  first_seen_at: string;
  last_seen_at: string;
  last_successful_sync_at: string | null;
};

export type ListingCollectionResponse = {
  items: ListingCollectionItem[];
  total: number;
  offset: number;
  limit: number;
};

// Approved, already-normalized SP-API shapes (12B.3D/12B.3E). Fields are
// optional/nullable defensively — these are Amazon's own reported
// structures, not something ASI controls the shape of, and the UI must
// never crash rendering one it hasn't seen before.
export type ListingOfferPrice = {
  amount: string;
  currencyCode: string;
};

export type ListingOffer = {
  marketplaceId: string;
  offerType: string;
  price?: ListingOfferPrice | null;
  points?: unknown;
  audience?: { value: string; displayName: string } | null;
};

export type ListingFulfillmentAvailability = {
  fulfillmentChannelCode: string;
  quantity?: number | null;
};

export type ListingIssue = {
  code: string;
  message: string;
  severity: ListingIssueSeverity | string;
  categories?: string[];
  enforcements?: unknown;
  attributeNames?: string[] | null;
  marketplaceIds?: string[] | null;
};

export type ListingProductTypeEntry = {
  productType: string;
  marketplaceId: string;
};

export type ListingDetail = {
  id: string;
  seller_sku: string;
  asin: string | null;
  item_name: string | null;
  product_type: string | null;
  is_active: boolean;
  is_buyable: boolean;
  is_discoverable: boolean;
  price_amount: string | null;
  price_currency: string | null;
  status: string[];
  offers: ListingOffer[];
  fulfillment_availability: ListingFulfillmentAvailability[];
  issues: ListingIssue[];
  product_types: ListingProductTypeEntry[];
  issue_count: number;
  highest_issue_severity: ListingIssueSeverity | null;
  first_seen_at: string;
  last_seen_at: string;
  last_successful_sync_at: string | null;
};

// The durable run's own raw status vocabulary — distinct from
// `ListingsSyncStatus`, which is the *summary* endpoint's display-mapped
// vocabulary (`started` -> `running`, plus a synthetic `never_synchronized`
// that no actual run row ever has). The job-status endpoint and trigger
// response return this raw vocabulary directly from `AmazonIngestionRun.
// status` — see `_job_status_from_row` in `app.amazon.listings_sync`.
export type ListingsRunStatus =
  | "queued"
  | "started"
  | "waiting_to_retry"
  | "succeeded"
  | "partial"
  | "failed"
  | "timed_out";

// 12B.3G: the trigger no longer runs synchronization itself — it enqueues
// (or reports the existing) durable job and returns immediately. Never
// carries an organization id, seller id, connection id, lease owner,
// credential, token reference, or page token.
export type ListingsSyncJobStatus = {
  run_id: string;
  run_type: string;
  status: ListingsRunStatus;
  marketplace_participation_id: string;
  pages_fetched: number;
  records_received: number;
  records_accepted: number;
  records_rejected: number;
  reported_total_results: number | null;
  pagination_complete: boolean;
  attempt_count: number;
  queued_at: string;
  started_at: string | null;
  last_heartbeat_at: string | null;
  next_retry_at: string | null;
  completed_at: string | null;
  failure_class: string | null;
};

export type ListingsSyncTriggerReason =
  | "queued"
  | "already_running"
  | "cooldown"
  // Queue-backlog safety valve only — never a worker-execution-capacity
  // rejection. A legitimate new job is never rejected merely because
  // workers are busy; only an unreasonably large *queue* triggers this.
  | "queue_backlog_limit_reached"
  | "scope_not_found"
  | "scope_inactive"
  | "identity_missing"
  | "connection_unresolvable";

export type ListingsSyncTriggerResponse = {
  reason: ListingsSyncTriggerReason;
  message: string | null;
  job: ListingsSyncJobStatus | null;
  // Populated only for reason "cooldown" — the server-computed moment
  // after which a new trigger will be accepted again.
  retry_allowed_at?: string | null;
};

export type AmazonIngestionRunStatus = "started" | "succeeded" | "partial" | "failed" | "timed_out";

export type AmazonIngestionStatus = {
  status: AmazonIngestionRunStatus;
  started_at: string;
  completed_at: string | null;
  records_accepted: number;
  failure_class: string | null;
};

export type AmazonConnectionOverview = {
  status: AmazonConnectionStatus;
  connection_status: AmazonConnectionLifecycleStatus;
  persisted: boolean;
  provider: "SP_API";
  environment: AmazonConnectionEnvironment;
  region: string;
  marketplace: string;
  application: string;
  credentials_configured: boolean;
  selling_partner_id: string | null;
  authorized_at: string | null;
  last_successful_validation_at: string | null;
  last_successful_sync_at: string | null;
  last_error_code: string | null;
  last_test_at: string | null;
  organization_id: string;
  // 12B.2B additions. Optional: a response from before this deploy will not
  // include them, and the UI must render safely without them.
  seller_account_id?: string | null;
  seller_account_display_name?: string | null;
  marketplaces?: AmazonSellerMarketplace[];
  latest_ingestion?: AmazonIngestionStatus | null;
  ads_api: AmazonAdsConnectionPlaceholder;
};

export type AmazonConnectionTestResult = {
  status: AmazonConnectionStatus;
  provider: "SP_API";
  environment: AmazonConnectionEnvironment;
  marketplace: string;
  operation: string;
  tested_at: string;
  message: string | null;
};

export type AmazonAuthorizationStart = {
  authorization_url: string;
  expires_at: string;
  connection_status: AmazonConnectionLifecycleStatus;
  provider: "SP_API";
  environment: AmazonConnectionEnvironment;
  organization_id: string;
};

