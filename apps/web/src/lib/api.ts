import type {
  AICompetitiveIntelligenceResponse,
  AIImageIntelligenceResponse,
  AIListingIntelligenceResponse,
  AIListingIntelligenceV2Response,
  ApiErrorBody,
  CompetitorComparisonResponse,
  CompetitorDiscoveryResult,
  CompetitorSearchQueryResponse,
  ListingAnalysis,
  ListingAnalysisResponse,
  ListingAnalysisV2,
  ListingAnalysisV2Response,
  ListingReweightResponse,
  ManualProductInput,
  Product,
  ProductResponse,
  ProductSource,
  ReportAnalysisResponse,
  ScoringProfile,
  ScoringProfileListResponse,
  ScoringWeights,
  UsageDashboardResponse,
  BulkAnalysisMode,
  BulkAISelection,
  BulkIngestStats,
  BulkJobResponse,
  SavedAnalysisDetail,
  SavedAnalysisListResponse,
  CopilotCompactContext,
  CopilotConversationDetail,
  CopilotEvidenceEnvelope,
  CopilotExecutionResult,
  CopilotPlan,
  CopilotSynthesizedResponse,
  ProfitModel,
  ProfitModelListResponse,
  ProfitModelInputs,
  ProfitSnapshot,
  AdvertisingModel,
  AdvertisingModelInputs,
  AdvertisingSnapshot,
  AdvertisingSnapshotListResponse,
  AmazonConnectionOverview,
  AmazonConnectionTestResult,
  AmazonAuthorizationStart,
  AmazonConnectionEnvironment,
  ListingCollectionResponse,
  ListingDetail,
  ListingIssueSeverity,
  ListingSortField,
  ListingsSummary,
  ListingsSyncTriggerResponse,
  SortDirection,
  OrderCollectionResponse,
  OrderDetail,
  OrderFulfilledBy,
  OrderFulfillmentStatus,
  OrderSortField,
  OrdersSummary,
  OrdersSyncJobStatus,
  OrdersSyncTriggerResponse,
  SalesTrafficAsinGranularity,
  SalesTrafficDailyTrendResponse,
  SalesTrafficDateGranularity,
  SalesTrafficFreshness,
  SalesTrafficProductPerformanceResponse,
  SalesTrafficProductSortField,
  SalesTrafficSummary,
  SalesTrafficSyncJobStatus,
  SalesTrafficSyncTriggerResponse,
} from "@/lib/types";

export class ProductLookupError extends Error {
  constructor(
    message: string,
    readonly kind: "invalid" | "not_found" | "unavailable" | "unknown",
  ) {
    super(message);
    this.name = "ProductLookupError";
  }
}

function apiBaseUrl(): string {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL;
  if (!base) {
    throw new ProductLookupError(
      "API URL is not configured. Set NEXT_PUBLIC_API_BASE_URL in .env.local.",
      "unavailable",
    );
  }
  return base.replace(/\/$/, "");
}

function formatDetail(body: ApiErrorBody | null): string {
  if (!body?.detail) {
    return "";
  }
  if (typeof body.detail === "string") {
    return body.detail;
  }
  return body.detail
    .map((item) => item.msg)
    .filter(Boolean)
    .join(" ");
}

async function readError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as ApiErrorBody;
    return formatDetail(body);
  } catch {
    return "";
  }
}

async function parseProductResponse(response: Response): Promise<ProductResponse> {
  if (response.ok) {
    return (await response.json()) as ProductResponse;
  }

  const detail = await readError(response);

  if (response.status === 400) {
    throw new ProductLookupError(
      detail || "Please check the product details and try again.",
      "invalid",
    );
  }

  if (response.status === 404) {
    throw new ProductLookupError(
      detail || "No product was found for that ASIN.",
      "not_found",
    );
  }

  if (response.status === 503) {
    throw new ProductLookupError(
      detail ||
        "This product lookup is temporarily unavailable. Try again later, or enter the listing manually.",
      "unavailable",
    );
  }

  if (response.status === 502) {
    throw new ProductLookupError(
      detail ||
        "Could not retrieve this Amazon.in listing. Try again later, or enter the listing manually.",
      "unavailable",
    );
  }

  throw new ProductLookupError(
    detail || "Something went wrong while processing this product.",
    "unknown",
  );
}

export async function fetchProduct(
  asin: string,
  marketplace = "amazon.in",
): Promise<ProductResponse> {
  const url = `${apiBaseUrl()}/api/v1/products/${encodeURIComponent(asin)}?marketplace=${encodeURIComponent(marketplace)}`;

  let response: Response;
  try {
    response = await fetch(url);
  } catch {
    throw new ProductLookupError(
      "Can't reach the API. Make sure the FastAPI backend is running.",
      "unavailable",
    );
  }

  return parseProductResponse(response);
}

export async function createManualProduct(
  payload: ManualProductInput,
): Promise<ProductResponse> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}/api/v1/products/manual`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch {
    throw new ProductLookupError(
      "Can't reach the API. Make sure the FastAPI backend is running.",
      "unavailable",
    );
  }

  return parseProductResponse(response);
}

export async function analyzeListing(
  product: Product,
  source?: ProductSource,
): Promise<ListingAnalysisResponse> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}/api/v1/analysis/listing`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ product, source: source ?? null }),
    });
  } catch {
    throw new ProductLookupError(
      "Can't reach the API. Make sure the FastAPI backend is running.",
      "unavailable",
    );
  }

  if (response.ok) {
    return (await response.json()) as ListingAnalysisResponse;
  }

  const detail = await readError(response);
  if (response.status === 400) {
    throw new ProductLookupError(
      detail || "This product could not be analyzed.",
      "invalid",
    );
  }
  throw new ProductLookupError(
    detail || "Something went wrong while analyzing this listing.",
    "unknown",
  );
}

export async function analyzeListingV2(
  product: Product,
  source?: ProductSource,
  scoringProfileId?: string | null,
): Promise<ListingAnalysisV2Response> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}/api/v1/analysis/listing/v2`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        product,
        source: source ?? null,
        scoring_profile_id: scoringProfileId ?? null,
      }),
    });
  } catch {
    throw new ProductLookupError(
      "Can't reach the API. Make sure the FastAPI backend is running.",
      "unavailable",
    );
  }

  if (response.ok) {
    return (await response.json()) as ListingAnalysisV2Response;
  }

  const detail = await readError(response);
  if (response.status === 400) {
    throw new ProductLookupError(
      detail || "This product could not be analyzed.",
      "invalid",
    );
  }
  throw new ProductLookupError(
    detail || "Something went wrong while analyzing this listing.",
    "unknown",
  );
}

export async function reweightListingV2(payload: {
  scoring_profile_id: string;
  report_id?: string | null;
  analysis?: ListingAnalysisV2;
  persist?: boolean;
}): Promise<ListingReweightResponse> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}/api/v1/analysis/listing/v2/reweight`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        scoring_profile_id: payload.scoring_profile_id,
        report_id: payload.report_id ?? null,
        analysis: payload.analysis ?? null,
        persist: payload.persist ?? false,
      }),
    });
  } catch {
    throw new ProductLookupError(
      "Can't reach the API. Make sure the FastAPI backend is running.",
      "unavailable",
    );
  }

  if (response.ok) {
    return (await response.json()) as ListingReweightResponse;
  }

  const detail = await readError(response);
  throw new ProductLookupError(
    detail || "The custom score could not be recalculated.",
    response.status === 400 ? "invalid" : "unknown",
  );
}

export async function listScoringProfiles(
  includeArchived = false,
): Promise<ScoringProfileListResponse> {
  const query = includeArchived ? "?include_archived=true" : "";
  const response = await fetch(`${apiBaseUrl()}/api/v1/scoring-profiles${query}`);
  if (!response.ok) {
    const detail = await readError(response);
    throw new ProductLookupError(detail || "Scoring profiles could not be loaded.", "unknown");
  }
  return (await response.json()) as ScoringProfileListResponse;
}

export async function createScoringProfile(payload: {
  name: string;
  description?: string | null;
  weights: ScoringWeights;
  is_default?: boolean;
}): Promise<ScoringProfile> {
  const response = await fetch(`${apiBaseUrl()}/api/v1/scoring-profiles`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const detail = await readError(response);
    throw new ProductLookupError(detail || "The scoring profile could not be saved.", "invalid");
  }
  return (await response.json()) as ScoringProfile;
}

export async function updateScoringProfile(
  profileId: string,
  payload: {
    name?: string;
    description?: string | null;
    weights?: ScoringWeights;
    is_default?: boolean;
  },
): Promise<ScoringProfile> {
  const response = await fetch(`${apiBaseUrl()}/api/v1/scoring-profiles/${profileId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const detail = await readError(response);
    throw new ProductLookupError(detail || "The scoring profile could not be updated.", "invalid");
  }
  return (await response.json()) as ScoringProfile;
}

export async function archiveScoringProfile(profileId: string): Promise<ScoringProfile> {
  const response = await fetch(`${apiBaseUrl()}/api/v1/scoring-profiles/${profileId}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    const detail = await readError(response);
    throw new ProductLookupError(detail || "The scoring profile could not be archived.", "invalid");
  }
  return (await response.json()) as ScoringProfile;
}

export async function generateAIListingIntelligence(
  product: Product,
  analysis: ListingAnalysis,
  source?: ProductSource,
): Promise<AIListingIntelligenceResponse> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}/api/v1/analysis/listing/ai`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ product, analysis, source: source ?? null }),
    });
  } catch {
    throw new ProductLookupError(
      "Can't reach the API. Make sure the FastAPI backend is running.",
      "unavailable",
    );
  }

  if (response.ok) {
    return (await response.json()) as AIListingIntelligenceResponse;
  }

  const detail = await readError(response);
  if (response.status === 400 || response.status === 422) {
    throw new ProductLookupError(
      detail || "This listing could not be analyzed by AI.",
      "invalid",
    );
  }
  if (response.status === 503) {
    throw new ProductLookupError(
      detail || "AI analysis is not configured.",
      "unavailable",
    );
  }
  if (response.status === 502) {
    throw new ProductLookupError(
      detail || "AI analysis could not be completed. Try again later.",
      "unavailable",
    );
  }
  throw new ProductLookupError(
    detail || "Something went wrong while generating AI recommendations.",
    "unknown",
  );
}

export async function generateAIListingIntelligenceV2(
  product: Product,
  analysis: ListingAnalysisV2,
  source?: ProductSource,
  reportId?: string | null,
): Promise<AIListingIntelligenceV2Response> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}/api/v1/analysis/listing/v2/ai`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        product,
        analysis,
        source: source ?? null,
        report_id: reportId ?? null,
      }),
    });
  } catch {
    throw new ProductLookupError(
      "Can't reach the API. Make sure the FastAPI backend is running.",
      "unavailable",
    );
  }

  if (response.ok) {
    return (await response.json()) as AIListingIntelligenceV2Response;
  }

  const detail = await readError(response);
  if (response.status === 400 || response.status === 422) {
    throw new ProductLookupError(
      detail || "This listing could not be analyzed by AI.",
      "invalid",
    );
  }
  if (response.status === 503) {
    throw new ProductLookupError(
      detail || "AI analysis is not configured.",
      "unavailable",
    );
  }
  if (response.status === 502) {
    throw new ProductLookupError(
      detail || "AI analysis could not be completed. Try again later.",
      "unavailable",
    );
  }
  throw new ProductLookupError(
    detail || "Something went wrong while generating AI strategy.",
    "unknown",
  );
}

export async function generateImageIntelligence(
  product: Product,
  analysis: ListingAnalysisV2,
  source?: ProductSource,
  reportId?: string | null,
): Promise<AIImageIntelligenceResponse> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}/api/v1/analysis/listing/v2/images/ai`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        product,
        analysis,
        source: source ?? null,
        report_id: reportId ?? null,
      }),
    });
  } catch {
    throw new ProductLookupError(
      "Can't reach the API. Make sure the FastAPI backend is running.",
      "unavailable",
    );
  }

  if (response.ok) {
    return (await response.json()) as AIImageIntelligenceResponse;
  }

  const detail = await readError(response);
  if (response.status === 400 || response.status === 422) {
    throw new ProductLookupError(
      detail || "No valid listing images were available for visual analysis.",
      "invalid",
    );
  }
  if (response.status === 503) {
    throw new ProductLookupError(
      detail || "AI analysis is not configured.",
      "unavailable",
    );
  }
  if (response.status === 502) {
    throw new ProductLookupError(
      detail || "Image analysis could not be completed. Try again later.",
      "unavailable",
    );
  }
  throw new ProductLookupError(
    detail || "Something went wrong while analyzing listing images.",
    "unknown",
  );
}

export async function analyzeCompetitors(
  targetProduct: Product,
  competitorAsins: string[],
  source?: ProductSource,
): Promise<CompetitorComparisonResponse> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}/api/v1/analysis/competitors`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target_product: targetProduct,
        competitor_asins: competitorAsins,
        source: source ?? null,
      }),
    });
  } catch {
    throw new ProductLookupError(
      "Can't reach the API. Make sure the FastAPI backend is running.",
      "unavailable",
    );
  }

  if (response.ok) {
    return (await response.json()) as CompetitorComparisonResponse;
  }

  const detail = await readError(response);
  if (response.status === 400 || response.status === 422) {
    throw new ProductLookupError(
      detail || "These competitor ASINs could not be compared.",
      "invalid",
    );
  }
  if (response.status === 503) {
    throw new ProductLookupError(
      detail || "Competitor lookup is not configured.",
      "unavailable",
    );
  }
  if (response.status === 502) {
    throw new ProductLookupError(
      detail || "Competitor listings could not be retrieved. Try again later.",
      "unavailable",
    );
  }
  throw new ProductLookupError(
    detail || "Something went wrong while comparing competitors.",
    "unknown",
  );
}

export async function generateAICompetitiveIntelligence(
  comparison: CompetitorComparisonResponse,
): Promise<AICompetitiveIntelligenceResponse> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}/api/v1/analysis/competitors/ai`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ comparison }),
    });
  } catch {
    throw new ProductLookupError(
      "Can't reach the API. Make sure the FastAPI backend is running.",
      "unavailable",
    );
  }

  if (response.ok) {
    return (await response.json()) as AICompetitiveIntelligenceResponse;
  }

  const detail = await readError(response);
  if (response.status === 400 || response.status === 422) {
    throw new ProductLookupError(
      detail || "This comparison could not be analyzed by AI.",
      "invalid",
    );
  }
  if (response.status === 503) {
    throw new ProductLookupError(
      detail || "AI analysis is not configured.",
      "unavailable",
    );
  }
  if (response.status === 502) {
    throw new ProductLookupError(
      detail || "AI analysis could not be completed. Try again later.",
      "unavailable",
    );
  }
  throw new ProductLookupError(
    detail || "Something went wrong while generating competitive insights.",
    "unknown",
  );
}

export async function generateCompetitorSearchQuery(
  targetProduct: Product,
): Promise<CompetitorSearchQueryResponse> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}/api/v1/competitors/query`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_product: targetProduct }),
    });
  } catch {
    throw new ProductLookupError(
      "Can't reach the API. Make sure the FastAPI backend is running.",
      "unavailable",
    );
  }

  if (response.ok) {
    return (await response.json()) as CompetitorSearchQueryResponse;
  }

  const detail = await readError(response);
  if (response.status === 400) {
    throw new ProductLookupError(detail || "This search query could not be generated.", "invalid");
  }
  throw new ProductLookupError(
    detail || "Something went wrong while generating a search query.",
    "unknown",
  );
}

export async function discoverCompetitors(
  targetProduct: Product,
  searchQuery?: string | null,
): Promise<CompetitorDiscoveryResult> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}/api/v1/competitors/discover`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target_product: targetProduct,
        search_query: searchQuery ?? null,
      }),
    });
  } catch {
    throw new ProductLookupError(
      "Can't reach the API. Make sure the FastAPI backend is running.",
      "unavailable",
    );
  }

  if (response.ok) {
    return (await response.json()) as CompetitorDiscoveryResult;
  }

  const detail = await readError(response);
  if (response.status === 400 || response.status === 422) {
    throw new ProductLookupError(detail || "This search query could not be used.", "invalid");
  }
  if (response.status === 503) {
    throw new ProductLookupError(
      detail || "Competitor discovery is temporarily unavailable.",
      "unavailable",
    );
  }
  if (response.status === 502) {
    throw new ProductLookupError(
      detail || "Competitor discovery is temporarily unavailable.",
      "unavailable",
    );
  }
  throw new ProductLookupError(
    detail || "Something went wrong while discovering competitors.",
    "unknown",
  );
}

export class ReportAnalysisError extends Error {
  constructor(
    message: string,
    readonly kind: "invalid" | "unavailable" | "unknown",
  ) {
    super(message);
    this.name = "ReportAnalysisError";
  }
}

export async function fetchUsageDashboard(
  refresh = false,
): Promise<UsageDashboardResponse> {
  const url = `${apiBaseUrl()}/api/v1/usage/dashboard${refresh ? "?refresh=true" : ""}`;
  let response: Response;
  try {
    response = await fetch(url, { cache: "no-store" });
  } catch {
    throw new ProductLookupError(
      "Can't reach the API. Make sure the FastAPI backend is running.",
      "unavailable",
    );
  }
  if (!response.ok) {
    throw new ProductLookupError(
      "Usage temporarily unavailable",
      "unavailable",
    );
  }
  return (await response.json()) as UsageDashboardResponse;
}

export async function analyzeReport(file: File): Promise<ReportAnalysisResponse> {
  const body = new FormData();
  body.append("file", file);

  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}/api/v1/reports/analyze`, {
      method: "POST",
      body,
    });
  } catch {
    throw new ReportAnalysisError(
      "Can't reach the API. Make sure the FastAPI backend is running.",
      "unavailable",
    );
  }

  if (response.ok) {
    return (await response.json()) as ReportAnalysisResponse;
  }

  const detail = await readError(response);
  if (response.status === 400 || response.status === 422) {
    throw new ReportAnalysisError(
      detail || "This report could not be analyzed. Check the file type and columns.",
      "invalid",
    );
  }
  throw new ReportAnalysisError(
    detail || "Something went wrong while analyzing this report.",
    "unknown",
  );
}

export async function previewBulkFile(file: File): Promise<BulkIngestStats> {
  const body = new FormData();
  body.append("file", file);
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}/api/v1/bulk/preview`, { method: "POST", body });
  } catch {
    throw new ReportAnalysisError(
      "Can't reach the API. Make sure the FastAPI backend is running.",
      "unavailable",
    );
  }
  if (response.ok) {
    return (await response.json()) as BulkIngestStats;
  }
  const detail = await readError(response);
  throw new ReportAnalysisError(detail || "This file could not be read.", "invalid");
}

export async function startBulkJob(
  file: File,
  analysisMode: BulkAnalysisMode,
  aiSelection: BulkAISelection,
  topN: number,
): Promise<BulkJobResponse> {
  const body = new FormData();
  body.append("file", file);
  body.append("analysis_mode", analysisMode);
  body.append("ai_selection", aiSelection);
  body.append("top_n", String(topN));
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}/api/v1/bulk/jobs`, { method: "POST", body });
  } catch {
    throw new ReportAnalysisError(
      "Can't reach the API. Make sure the FastAPI backend is running.",
      "unavailable",
    );
  }
  if (response.ok) {
    return (await response.json()) as BulkJobResponse;
  }
  const detail = await readError(response);
  throw new ReportAnalysisError(detail || "This bulk job could not be started.", "invalid");
}

export async function fetchBulkJob(jobId: string): Promise<BulkJobResponse> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}/api/v1/bulk/jobs/${encodeURIComponent(jobId)}`, {
      cache: "no-store",
    });
  } catch {
    throw new ReportAnalysisError(
      "Can't reach the API. Make sure the FastAPI backend is running.",
      "unavailable",
    );
  }
  if (response.ok) {
    return (await response.json()) as BulkJobResponse;
  }
  throw new ReportAnalysisError("Bulk job was not found.", "unknown");
}

export async function downloadBulkReport(jobId: string): Promise<void> {
  const response = await fetch(
    `${apiBaseUrl()}/api/v1/bulk/jobs/${encodeURIComponent(jobId)}/report.xlsx`,
  );
  if (!response.ok) {
    throw new ReportAnalysisError("The Excel report is not available yet.", "unknown");
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `bulk-due-diligence-${jobId.slice(0, 8)}.xlsx`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export async function fetchSavedAnalyses(params?: {
  offset?: number;
  limit?: number;
  asin?: string;
  status?: string;
}): Promise<SavedAnalysisListResponse> {
  const search = new URLSearchParams();
  search.set("offset", String(params?.offset ?? 0));
  search.set("limit", String(params?.limit ?? 20));
  if (params?.asin) {
    search.set("asin", params.asin);
  }
  if (params?.status) {
    search.set("status", params.status);
  }
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}/api/v1/reports?${search.toString()}`, {
      cache: "no-store",
    });
  } catch {
    throw new ProductLookupError(
      "Can't reach the API. Make sure the FastAPI backend is running.",
      "unavailable",
    );
  }
  if (response.ok) {
    return (await response.json()) as SavedAnalysisListResponse;
  }
  const detail = await readError(response);
  if (response.status === 503) {
    throw new ProductLookupError(
      detail || "Report history is not configured.",
      "unavailable",
    );
  }
  throw new ProductLookupError(detail || "Saved analyses could not be loaded.", "unknown");
}

export async function fetchSavedAnalysis(reportId: string): Promise<SavedAnalysisDetail> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}/api/v1/reports/${encodeURIComponent(reportId)}`, {
      cache: "no-store",
    });
  } catch {
    throw new ProductLookupError(
      "Can't reach the API. Make sure the FastAPI backend is running.",
      "unavailable",
    );
  }
  if (response.ok) {
    return (await response.json()) as SavedAnalysisDetail;
  }
  const detail = await readError(response);
  if (response.status === 404) {
    throw new ProductLookupError(detail || "This saved analysis was not found.", "not_found");
  }
  if (response.status === 503) {
    throw new ProductLookupError(
      detail || "Report history is not configured.",
      "unavailable",
    );
  }
  throw new ProductLookupError(detail || "This saved analysis could not be opened.", "unknown");
}

export type ClientPdfGenerateResponse = {
  report_id: string;
  generated: boolean;
  reused: boolean;
  filename: string;
  template_version: string;
};

export async function generateSavedAnalysisPdf(
  reportId: string,
): Promise<ClientPdfGenerateResponse> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}/api/v1/reports/${encodeURIComponent(reportId)}/pdf`, {
      method: "POST",
    });
  } catch {
    throw new ProductLookupError(
      "Can't reach the API. Make sure the FastAPI backend is running.",
      "unavailable",
    );
  }
  if (response.ok) {
    return (await response.json()) as ClientPdfGenerateResponse;
  }
  const detail = await readError(response);
  if (response.status === 404) {
    throw new ProductLookupError(detail || "This saved analysis was not found.", "not_found");
  }
  throw new ProductLookupError(detail || "PDF could not be generated. Please try again.", "unknown");
}

export async function downloadSavedAnalysisPdf(
  reportId: string,
  filename?: string,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}/api/v1/reports/${encodeURIComponent(reportId)}/pdf`, {
      cache: "no-store",
    });
  } catch {
    throw new ProductLookupError(
      "Can't reach the API. Make sure the FastAPI backend is running.",
      "unavailable",
    );
  }
  if (!response.ok) {
    const detail = await readError(response);
    if (response.status === 404) {
      throw new ProductLookupError(detail || "This PDF is not available.", "not_found");
    }
    throw new ProductLookupError(detail || "PDF could not be generated. Please try again.", "unknown");
  }
  const blob = await response.blob();
  const disposition = response.headers.get("Content-Disposition");
  const matched = disposition?.match(/filename="([^"]+)"/);
  const downloadName = filename || matched?.[1] || `Amazon-Listing-Analysis-${reportId.slice(0, 8)}.pdf`;
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = downloadName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export async function deleteSavedAnalysis(
  reportId: string,
): Promise<{ report_id: string; deleted: boolean }> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}/api/v1/reports/${encodeURIComponent(reportId)}`, {
      method: "DELETE",
    });
  } catch {
    throw new ProductLookupError(
      "Can't reach the API. Make sure the FastAPI backend is running.",
      "unavailable",
    );
  }
  if (response.ok) {
    return (await response.json()) as { report_id: string; deleted: boolean };
  }
  const detail = await readError(response);
  if (response.status === 404) {
    throw new ProductLookupError(detail || "This saved analysis was not found.", "not_found");
  }
  throw new ProductLookupError(detail || "This report could not be deleted.", "unknown");
}

export class CopilotError extends Error {
  constructor(
    message: string,
    readonly kind: "unavailable" | "invalid" | "unknown",
  ) {
    super(message);
    this.name = "CopilotError";
  }
}

function copilotUrl(path: string): string {
  return `${apiBaseUrl()}/api/v1/copilot${path}`;
}

async function copilotRequest<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(copilotUrl(path), {
      cache: "no-store",
      ...init,
      headers: {
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...init?.headers,
      },
    });
  } catch {
    throw new CopilotError(
      "Copilot could not reach the server. Make sure the API is running.",
      "unavailable",
    );
  }
  if (response.ok) {
    return (await response.json()) as T;
  }
  const detail = await readError(response);
  if (response.status === 503) {
    throw new CopilotError(
      detail || "Copilot is not configured right now. Please try again later.",
      "unavailable",
    );
  }
  if (response.status === 400 || response.status === 409 || response.status === 404) {
    throw new CopilotError(
      detail || "Copilot could not complete this analysis. Please try again.",
      "invalid",
    );
  }
  throw new CopilotError(
    "Copilot could not complete this analysis. Please try again.",
    "unknown",
  );
}

export async function createCopilotConversation(): Promise<CopilotConversationDetail> {
  return copilotRequest<CopilotConversationDetail>("/conversations", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function fetchCopilotConversation(
  conversationId: string,
): Promise<CopilotConversationDetail> {
  return copilotRequest<CopilotConversationDetail>(`/conversations/${conversationId}`);
}

export async function planCopilotTurn(
  conversationId: string,
  userMessage: string,
  scope?: { marketplaceParticipationId?: string | null; periodDays?: number | null; forceRefresh?: boolean },
): Promise<CopilotPlan> {
  const body: Record<string, string | number | boolean> = { user_message: userMessage };
  if (scope?.marketplaceParticipationId) {
    body.marketplace_participation_id = scope.marketplaceParticipationId;
  }
  if (scope?.periodDays) {
    body.period_days = scope.periodDays;
  }
  if (scope?.forceRefresh) {
    body.force_refresh = true;
  }
  return copilotRequest<CopilotPlan>(`/conversations/${conversationId}/plan`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function executeCopilotPlan(
  conversationId: string,
  payload: { plan_id: string; plan_hash: string; confirmation_nonce?: string },
): Promise<CopilotExecutionResult> {
  const body: Record<string, string> = {
    plan_id: payload.plan_id,
    plan_hash: payload.plan_hash,
  };
  if (payload.confirmation_nonce) {
    body.confirmation_nonce = payload.confirmation_nonce;
  }
  return copilotRequest<CopilotExecutionResult>(`/conversations/${conversationId}/execute`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function confirmCopilotPlan(
  conversationId: string,
  nonce: string,
): Promise<CopilotExecutionResult> {
  return copilotRequest<CopilotExecutionResult>(`/conversations/${conversationId}/confirm`, {
    method: "POST",
    body: JSON.stringify({ nonce }),
  });
}

export async function synthesizeCopilot(payload: {
  user_message: string;
  intent: string;
  evidence: CopilotEvidenceEnvelope[];
  compact_context?: CopilotCompactContext | Record<string, unknown>;
}): Promise<CopilotSynthesizedResponse> {
  const compact = (payload.compact_context ?? {}) as Record<string, unknown>;
  return copilotRequest<CopilotSynthesizedResponse>("/synthesize", {
    method: "POST",
    body: JSON.stringify({
      user_message: payload.user_message,
      intent: payload.intent,
      evidence: payload.evidence,
      compact_context: {
        last_asin: compact.last_asin ?? null,
        last_report_id: compact.last_report_id ?? null,
        previous_intent: compact.previous_intent ?? null,
        evidence_refs: compact.evidence_refs ?? [],
        recent_user_snippets: compact.recent_user_snippets ?? [],
      },
    }),
  });
}

export class ProfitError extends Error {
  constructor(
    message: string,
    readonly kind: "unavailable" | "invalid" | "unknown",
  ) {
    super(message);
    this.name = "ProfitError";
  }
}

function profitUrl(path: string): string {
  return `${apiBaseUrl()}/api/v1/profit${path}`;
}

async function profitRequest<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(profitUrl(path), {
      cache: "no-store",
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers ?? {}),
      },
    });
  } catch {
    throw new ProfitError(
      "Profit Intelligence could not reach the server. Make sure the API is running.",
      "unavailable",
    );
  }
  if (response.ok) {
    return (await response.json()) as T;
  }
  const detail = await readError(response);
  if (response.status === 503) {
    throw new ProfitError(
      detail || "Profit Intelligence is not configured right now.",
      "unavailable",
    );
  }
  if (response.status === 400 || response.status === 409 || response.status === 404) {
    throw new ProfitError(
      detail || "Profit Intelligence could not complete this request.",
      "invalid",
    );
  }
  throw new ProfitError(
    "Profit Intelligence could not complete this request.",
    "unknown",
  );
}

export class AmazonConnectionError extends Error {
  constructor(
    message: string,
    readonly kind: "unavailable" | "unknown",
  ) {
    super(message);
    this.name = "AmazonConnectionError";
  }
}

async function amazonConnectionRequest<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}/api/v1/amazon${path}`, {
      cache: "no-store",
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers ?? {}),
      },
    });
  } catch {
    throw new AmazonConnectionError(
      "Amazon Connection could not reach the server. Make sure the API is running.",
      "unavailable",
    );
  }
  if (response.ok) {
    return (await response.json()) as T;
  }
  const detail = await readError(response);
  throw new AmazonConnectionError(
    detail || "Amazon Connection could not complete this request.",
    response.status === 503 ? "unavailable" : "unknown",
  );
}

export async function fetchAmazonConnection(): Promise<AmazonConnectionOverview> {
  return amazonConnectionRequest<AmazonConnectionOverview>("/connection");
}

export async function testAmazonConnection(): Promise<AmazonConnectionTestResult> {
  return amazonConnectionRequest<AmazonConnectionTestResult>("/connection/test", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

const AUTHORIZE_START_FAILED = "Unable to start Amazon connection. Please try again.";

function isSellerCentralAuthorizationUrl(value: string): boolean {
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== "https:") {
      return false;
    }
    const host = parsed.hostname.toLowerCase();
    const sellerCentral = host.startsWith("sellercentral.") || host.startsWith("sellercentral-");
    if (!sellerCentral || !host.includes("amazon.")) {
      return false;
    }
    return (
      parsed.pathname === "/apps/authorize/consent" ||
      parsed.pathname.endsWith("/apps/authorize/consent")
    );
  } catch {
    return false;
  }
}

export async function authorizeAmazonConnection(
  environment: AmazonConnectionEnvironment = "PRODUCTION",
): Promise<AmazonAuthorizationStart> {
  const result = await amazonConnectionRequest<AmazonAuthorizationStart>("/connection/authorize", {
    method: "POST",
    body: JSON.stringify({ environment }),
  });
  if (
    typeof result?.authorization_url !== "string" ||
    !isSellerCentralAuthorizationUrl(result.authorization_url)
  ) {
    throw new AmazonConnectionError(AUTHORIZE_START_FAILED, "unknown");
  }
  return result;
}

export class ListingsApiError extends Error {
  constructor(
    message: string,
    readonly kind: "not_found" | "unavailable" | "unknown",
  ) {
    super(message);
    this.name = "ListingsApiError";
  }
}

async function listingsRequest<T>(path: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}/api/v1/amazon${path}`, { cache: "no-store" });
  } catch {
    throw new ListingsApiError(
      "Seller Listings could not reach the server. Make sure the API is running.",
      "unavailable",
    );
  }
  if (response.ok) {
    return (await response.json()) as T;
  }
  const detail = await readError(response);
  if (response.status === 404) {
    throw new ListingsApiError(detail || "This was not found.", "not_found");
  }
  if (response.status === 503) {
    throw new ListingsApiError(detail || "Seller Listings is not configured right now.", "unavailable");
  }
  throw new ListingsApiError(detail || "Seller Listings could not complete this request.", "unknown");
}

export async function fetchListingsSummary(participationId: string): Promise<ListingsSummary> {
  return listingsRequest<ListingsSummary>(
    `/marketplace-participations/${encodeURIComponent(participationId)}/listings/summary`,
  );
}

export type ListingsQuery = {
  q?: string;
  isActive?: boolean;
  isBuyable?: boolean;
  isDiscoverable?: boolean;
  hasIssues?: boolean;
  highestIssueSeverity?: ListingIssueSeverity;
  productType?: string;
  sortBy?: ListingSortField;
  sortDir?: SortDirection;
  offset?: number;
  limit?: number;
};

export async function fetchListings(
  participationId: string,
  query: ListingsQuery = {},
): Promise<ListingCollectionResponse> {
  const params = new URLSearchParams();
  if (query.q) params.set("q", query.q);
  if (query.isActive !== undefined) params.set("is_active", String(query.isActive));
  if (query.isBuyable !== undefined) params.set("is_buyable", String(query.isBuyable));
  if (query.isDiscoverable !== undefined) params.set("is_discoverable", String(query.isDiscoverable));
  if (query.hasIssues !== undefined) params.set("has_issues", String(query.hasIssues));
  if (query.highestIssueSeverity) params.set("highest_issue_severity", query.highestIssueSeverity);
  if (query.productType) params.set("product_type", query.productType);
  params.set("sort_by", query.sortBy ?? "last_seen_at");
  params.set("sort_dir", query.sortDir ?? "desc");
  params.set("offset", String(query.offset ?? 0));
  params.set("limit", String(query.limit ?? 25));
  return listingsRequest<ListingCollectionResponse>(
    `/marketplace-participations/${encodeURIComponent(participationId)}/listings?${params.toString()}`,
  );
}

export async function fetchListingDetail(
  participationId: string,
  listingId: string,
): Promise<ListingDetail> {
  return listingsRequest<ListingDetail>(
    `/marketplace-participations/${encodeURIComponent(participationId)}/listings/${encodeURIComponent(listingId)}`,
  );
}

export class ListingsSyncError extends Error {
  constructor(
    message: string,
    readonly reason: string | null,
    readonly kind: "unavailable" | "unknown",
  ) {
    super(message);
    this.name = "ListingsSyncError";
  }
}

// 12B.3G: the trigger endpoint enqueues a durable job and returns
// immediately — it never runs synchronization itself, so every outcome
// this function can observe (a newly queued job, an already-running job,
// a cooldown/capacity rejection, or a scope failure) is a normal,
// structured `ListingsSyncTriggerResponse`, not an exception. This only
// throws `ListingsSyncError` for a genuine transport failure or a
// response so malformed it cannot be interpreted at all — never for an
// ordinary business outcome the caller is expected to branch on via
// `reason`.
export async function triggerListingsSync(participationId: string): Promise<ListingsSyncTriggerResponse> {
  let response: Response;
  try {
    response = await fetch(
      `${apiBaseUrl()}/api/v1/amazon/marketplace-participations/${encodeURIComponent(participationId)}/listings/sync`,
      { method: "POST", cache: "no-store" },
    );
  } catch {
    throw new ListingsSyncError("Could not reach the server to start synchronization.", null, "unavailable");
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new ListingsSyncError("Synchronization could not be started.", null, "unknown");
  }

  if (response.ok) {
    return body as ListingsSyncTriggerResponse;
  }
  // Every non-2xx status this endpoint returns wraps the same structured
  // shape in `detail` (see `ListingsSyncTriggerResponse` in `app.api.
  // routes.amazon_listings_sync`) — parsed here directly rather than
  // reused from `formatDetail`, which expects a plain string/list shape.
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (detail && typeof detail === "object") {
    return detail as ListingsSyncTriggerResponse;
  }
  throw new ListingsSyncError("Synchronization could not be started.", null, "unknown");
}

// --- 12B.4D: Seller Orders Read API + Sync Trigger -------------------------

export class OrdersApiError extends Error {
  constructor(
    message: string,
    readonly kind: "not_found" | "unavailable" | "unknown",
  ) {
    super(message);
    this.name = "OrdersApiError";
  }
}

async function ordersRequest<T>(path: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}/api/v1/amazon${path}`, { cache: "no-store" });
  } catch {
    throw new OrdersApiError("Orders could not reach the server. Make sure the API is running.", "unavailable");
  }
  if (response.ok) {
    return (await response.json()) as T;
  }
  const detail = await readError(response);
  if (response.status === 404) {
    throw new OrdersApiError(detail || "This was not found.", "not_found");
  }
  if (response.status === 503) {
    throw new OrdersApiError(detail || "Orders is not configured right now.", "unavailable");
  }
  throw new OrdersApiError(detail || "Orders could not complete this request.", "unknown");
}

export async function fetchOrdersSummary(participationId: string): Promise<OrdersSummary> {
  return ordersRequest<OrdersSummary>(
    `/marketplace-participations/${encodeURIComponent(participationId)}/orders/summary`,
  );
}

export type OrdersQuery = {
  q?: string;
  fulfillmentStatus?: OrderFulfillmentStatus;
  fulfilledBy?: OrderFulfilledBy;
  createdAfter?: string;
  createdBefore?: string;
  sortBy?: OrderSortField;
  sortDir?: SortDirection;
  offset?: number;
  limit?: number;
};

export async function fetchOrders(
  participationId: string,
  query: OrdersQuery = {},
): Promise<OrderCollectionResponse> {
  const params = new URLSearchParams();
  if (query.q) params.set("q", query.q);
  if (query.fulfillmentStatus) params.set("fulfillment_status", query.fulfillmentStatus);
  if (query.fulfilledBy) params.set("fulfilled_by", query.fulfilledBy);
  if (query.createdAfter) params.set("created_after", query.createdAfter);
  if (query.createdBefore) params.set("created_before", query.createdBefore);
  params.set("sort_by", query.sortBy ?? "amazon_last_updated_at");
  params.set("sort_dir", query.sortDir ?? "desc");
  params.set("offset", String(query.offset ?? 0));
  params.set("limit", String(query.limit ?? 25));
  return ordersRequest<OrderCollectionResponse>(
    `/marketplace-participations/${encodeURIComponent(participationId)}/orders?${params.toString()}`,
  );
}

export async function fetchOrderDetail(participationId: string, orderId: string): Promise<OrderDetail> {
  return ordersRequest<OrderDetail>(
    `/marketplace-participations/${encodeURIComponent(participationId)}/orders/${encodeURIComponent(orderId)}`,
  );
}

export class OrdersSyncError extends Error {
  constructor(
    message: string,
    readonly reason: string | null,
    readonly kind: "unavailable" | "unknown",
  ) {
    super(message);
    this.name = "OrdersSyncError";
  }
}

// Mirrors `triggerListingsSync`'s own contract exactly: the trigger
// endpoint enqueues a durable job and returns immediately, so every
// outcome (queued, already-running, cooldown/backlog, scope failure) is a
// normal structured response, not an exception. Only a genuine transport
// failure or an unparseable response throws.
export async function triggerOrdersSync(
  sellerAccountId: string,
  marketplaceParticipationIds: string[],
): Promise<OrdersSyncTriggerResponse> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}/api/v1/amazon/orders/sync`, {
      method: "POST",
      cache: "no-store",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        seller_account_id: sellerAccountId,
        marketplace_participation_ids: marketplaceParticipationIds,
      }),
    });
  } catch {
    throw new OrdersSyncError("Could not reach the server to start synchronization.", null, "unavailable");
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new OrdersSyncError("Synchronization could not be started.", null, "unknown");
  }

  if (response.ok) {
    return body as OrdersSyncTriggerResponse;
  }
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (detail && typeof detail === "object") {
    return detail as OrdersSyncTriggerResponse;
  }
  throw new OrdersSyncError("Synchronization could not be started.", null, "unknown");
}

export async function fetchOrdersSyncStatus(runId: string): Promise<OrdersSyncJobStatus | null> {
  try {
    return await ordersRequest<OrdersSyncJobStatus>(`/orders/sync/${encodeURIComponent(runId)}`);
  } catch (err) {
    if (err instanceof OrdersApiError && err.kind === "not_found") {
      return null;
    }
    throw err;
  }
}

// --- 12B.6A: Sales and Traffic Business Report Read API + Sync Trigger ----

export class SalesTrafficApiError extends Error {
  constructor(
    message: string,
    readonly kind: "not_found" | "unavailable" | "unknown",
  ) {
    super(message);
    this.name = "SalesTrafficApiError";
  }
}

async function salesTrafficRequest<T>(path: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}/api/v1/amazon${path}`, { cache: "no-store" });
  } catch {
    throw new SalesTrafficApiError(
      "Sales and Traffic could not reach the server. Make sure the API is running.",
      "unavailable",
    );
  }
  if (response.ok) {
    return (await response.json()) as T;
  }
  const detail = await readError(response);
  if (response.status === 404) {
    throw new SalesTrafficApiError(detail || "This was not found.", "not_found");
  }
  if (response.status === 503) {
    throw new SalesTrafficApiError(detail || "Sales and Traffic is not configured right now.", "unavailable");
  }
  throw new SalesTrafficApiError(detail || "Sales and Traffic could not complete this request.", "unknown");
}

export async function fetchSalesTrafficSummary(
  participationId: string,
  start: string,
  end: string,
): Promise<SalesTrafficSummary> {
  const params = new URLSearchParams({ start, end });
  return salesTrafficRequest<SalesTrafficSummary>(
    `/marketplace-participations/${encodeURIComponent(participationId)}/sales-traffic/summary?${params.toString()}`,
  );
}

export async function fetchSalesTrafficDailyTrend(
  participationId: string,
  start: string,
  end: string,
): Promise<SalesTrafficDailyTrendResponse> {
  const params = new URLSearchParams({ start, end });
  return salesTrafficRequest<SalesTrafficDailyTrendResponse>(
    `/marketplace-participations/${encodeURIComponent(participationId)}/sales-traffic/daily-trend?${params.toString()}`,
  );
}

export type SalesTrafficProductQuery = {
  q?: string;
  sortBy?: SalesTrafficProductSortField;
  sortDir?: SortDirection;
  offset?: number;
  limit?: number;
};

export async function fetchSalesTrafficProducts(
  participationId: string,
  start: string,
  end: string,
  query: SalesTrafficProductQuery = {},
): Promise<SalesTrafficProductPerformanceResponse> {
  const params = new URLSearchParams({ start, end });
  if (query.q) params.set("q", query.q);
  params.set("sort_by", query.sortBy ?? "ordered_product_sales_amount");
  params.set("sort_dir", query.sortDir ?? "desc");
  params.set("offset", String(query.offset ?? 0));
  params.set("limit", String(query.limit ?? 25));
  return salesTrafficRequest<SalesTrafficProductPerformanceResponse>(
    `/marketplace-participations/${encodeURIComponent(participationId)}/sales-traffic/products?${params.toString()}`,
  );
}

export async function fetchSalesTrafficFreshness(participationId: string): Promise<SalesTrafficFreshness> {
  return salesTrafficRequest<SalesTrafficFreshness>(
    `/marketplace-participations/${encodeURIComponent(participationId)}/sales-traffic/freshness`,
  );
}

export class SalesTrafficSyncError extends Error {
  constructor(
    message: string,
    readonly reason: string | null,
    readonly kind: "unavailable" | "unknown",
  ) {
    super(message);
    this.name = "SalesTrafficSyncError";
  }
}

// Mirrors `triggerOrdersSync`'s own contract exactly: the trigger
// endpoint enqueues a durable job and returns immediately, so every
// outcome (queued, already-running, cooldown, scope failure, invalid
// request) is a normal structured response, not an exception. Only a
// genuine transport failure or an unparseable response throws.
export async function triggerSalesTrafficSync(
  sellerAccountId: string,
  marketplaceParticipationId: string,
  dataStartTime: string,
  dataEndTime: string,
  options: { dateGranularity?: SalesTrafficDateGranularity; asinGranularity?: SalesTrafficAsinGranularity } = {},
): Promise<SalesTrafficSyncTriggerResponse> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}/api/v1/amazon/sales-traffic/sync`, {
      method: "POST",
      cache: "no-store",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        seller_account_id: sellerAccountId,
        marketplace_participation_id: marketplaceParticipationId,
        data_start_time: dataStartTime,
        data_end_time: dataEndTime,
        date_granularity: options.dateGranularity ?? "DAY",
        asin_granularity: options.asinGranularity ?? "SKU",
      }),
    });
  } catch {
    throw new SalesTrafficSyncError("Could not reach the server to start synchronization.", null, "unavailable");
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new SalesTrafficSyncError("Synchronization could not be started.", null, "unknown");
  }

  if (response.ok) {
    return body as SalesTrafficSyncTriggerResponse;
  }
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (detail && typeof detail === "object") {
    return detail as SalesTrafficSyncTriggerResponse;
  }
  throw new SalesTrafficSyncError("Synchronization could not be started.", null, "unknown");
}

export async function fetchSalesTrafficSyncStatus(runId: string): Promise<SalesTrafficSyncJobStatus | null> {
  try {
    return await salesTrafficRequest<SalesTrafficSyncJobStatus>(`/sales-traffic/sync/${encodeURIComponent(runId)}`);
  } catch (err) {
    if (err instanceof SalesTrafficApiError && err.kind === "not_found") {
      return null;
    }
    throw err;
  }
}

export async function createProfitModel(payload: {
  asin: string;
  marketplace?: string;
}): Promise<ProfitModel> {
  return profitRequest<ProfitModel>("/models", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function listProfitModels(asin?: string): Promise<ProfitModelListResponse> {
  const query = asin ? `?asin=${encodeURIComponent(asin)}` : "";
  return profitRequest<ProfitModelListResponse>(`/models${query}`);
}

export async function fetchProfitModel(modelId: string): Promise<ProfitModel> {
  return profitRequest<ProfitModel>(`/models/${modelId}`);
}

export async function updateProfitModel(
  modelId: string,
  payload: ProfitModelInputs,
): Promise<ProfitModel> {
  const body: ProfitModelInputs = {
    selling_price: payload.selling_price ?? null,
    cogs: payload.cogs ?? null,
    shipping_cost: payload.shipping_cost ?? null,
    packaging_cost: payload.packaging_cost ?? null,
    other_cost: payload.other_cost ?? null,
    referral_fee_amount: payload.referral_fee_amount ?? null,
    fba_fee_amount: payload.fba_fee_amount ?? null,
  };
  return profitRequest<ProfitModel>(`/models/${modelId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export async function calculateProfitModel(modelId: string): Promise<ProfitModel> {
  return profitRequest<ProfitModel>(`/models/${modelId}/calculate`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function previewProfit(payload: ProfitModelInputs & {
  referral_fee?: string | null;
  fba_fee?: string | null;
}): Promise<ProfitSnapshot> {
  return profitRequest<ProfitSnapshot>("/preview", {
    method: "POST",
    body: JSON.stringify({
      selling_price: payload.selling_price ?? null,
      cogs: payload.cogs ?? null,
      referral_fee: payload.referral_fee ?? payload.referral_fee_amount ?? null,
      fba_fee: payload.fba_fee ?? payload.fba_fee_amount ?? null,
      shipping_cost: payload.shipping_cost ?? null,
      packaging_cost: payload.packaging_cost ?? null,
      other_cost: payload.other_cost ?? null,
    }),
  });
}

export async function fetchAdvertising(modelId: string): Promise<AdvertisingModel> {
  return profitRequest<AdvertisingModel>(`/models/${modelId}/advertising`);
}

export async function updateAdvertising(
  modelId: string,
  payload: AdvertisingModelInputs,
): Promise<AdvertisingModel> {
  const body: AdvertisingModelInputs = {
    period_start: payload.period_start ?? null,
    period_end: payload.period_end ?? null,
    ad_spend: payload.ad_spend ?? null,
    ad_sales: payload.ad_sales ?? null,
    total_sales: payload.total_sales ?? null,
    units_in_period: payload.units_in_period ?? null,
  };
  return profitRequest<AdvertisingModel>(`/models/${modelId}/advertising`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export async function calculateAdvertising(modelId: string): Promise<AdvertisingModel> {
  return profitRequest<AdvertisingModel>(`/models/${modelId}/advertising/calculate`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function listAdvertisingSnapshots(
  modelId: string,
): Promise<AdvertisingSnapshotListResponse> {
  return profitRequest<AdvertisingSnapshotListResponse>(`/models/${modelId}/advertising/snapshots`);
}

export async function previewAdvertising(
  payload: AdvertisingModelInputs,
): Promise<AdvertisingSnapshot> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}/api/v1/advertising/preview`, {
      cache: "no-store",
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        period_start: payload.period_start ?? null,
        period_end: payload.period_end ?? null,
        ad_spend: payload.ad_spend ?? null,
        ad_sales: payload.ad_sales ?? null,
        total_sales: payload.total_sales ?? null,
        units_in_period: payload.units_in_period ?? null,
      }),
    });
  } catch {
    throw new ProfitError(
      "Advertising Intelligence could not reach the server. Make sure the API is running.",
      "unavailable",
    );
  }
  if (response.ok) {
    return (await response.json()) as AdvertisingSnapshot;
  }
  const detail = await readError(response);
  throw new ProfitError(
    detail || "Advertising Intelligence could not complete this request.",
    response.status === 400 || response.status === 404 ? "invalid" : "unknown",
  );
}
