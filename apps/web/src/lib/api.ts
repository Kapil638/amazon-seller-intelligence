import type {
  AICompetitiveIntelligenceResponse,
  AIListingIntelligenceResponse,
  ApiErrorBody,
  CompetitorComparisonResponse,
  CompetitorDiscoveryResult,
  CompetitorSearchQueryResponse,
  ListingAnalysis,
  ListingAnalysisResponse,
  ManualProductInput,
  Product,
  ProductResponse,
  ProductSource,
  ReportAnalysisResponse,
  UsageDashboardResponse,
  BulkAnalysisMode,
  BulkAISelection,
  BulkIngestStats,
  BulkJobResponse,
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
