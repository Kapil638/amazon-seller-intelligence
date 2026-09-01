class ProductNotFoundError(Exception):
    def __init__(self, asin: str, marketplace: str) -> None:
        self.asin = asin
        self.marketplace = marketplace
        super().__init__(f"Product {asin} not found for marketplace {marketplace}")


class UnsupportedMarketplaceError(Exception):
    def __init__(self, marketplace: str) -> None:
        self.marketplace = marketplace
        super().__init__(f"Unsupported marketplace: {marketplace}")


class ProductFetchBlockedError(Exception):
    def __init__(self, asin: str, marketplace: str, reason: str = "") -> None:
        self.asin = asin
        self.marketplace = marketplace
        self.reason = reason
        super().__init__(reason or f"Retrieval was blocked for {asin}")


class ProductFetchFailedError(Exception):
    def __init__(self, asin: str, marketplace: str, reason: str = "") -> None:
        self.asin = asin
        self.marketplace = marketplace
        self.reason = reason
        super().__init__(reason or f"Retrieval failed for {asin}")


class ProductParseFailedError(Exception):
    def __init__(self, asin: str, marketplace: str, reason: str = "") -> None:
        self.asin = asin
        self.marketplace = marketplace
        self.reason = reason
        super().__init__(reason or f"Could not parse product page for {asin}")


class ProviderConfigurationError(Exception):
    """Raised when a provider cannot run because required settings are missing."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class AIConfigurationError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class AIAuthenticationError(Exception):
    def __init__(self, message: str = "AI provider authentication failed.") -> None:
        super().__init__(message)


class AIRateLimitedError(Exception):
    def __init__(self, message: str = "AI service is temporarily rate-limited or quota-limited.") -> None:
        super().__init__(message)


class AIRequestFailedError(Exception):
    def __init__(self, message: str = "AI analysis could not be completed.") -> None:
        super().__init__(message)


class AIStructuredOutputError(Exception):
    def __init__(self, message: str = "AI returned an unusable structured response.") -> None:
        super().__init__(message)


class AISafetyRefusalError(Exception):
    def __init__(self, message: str = "AI declined to analyze this listing.") -> None:
        super().__init__(message)


class NoValidMediaError(Exception):
    def __init__(
        self,
        message: str = "No valid listing images were available for visual analysis.",
    ) -> None:
        super().__init__(message)


class CompetitorValidationError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class NoCompetitorsRetrievedError(Exception):
    def __init__(self, message: str = "No competitor listings could be retrieved.") -> None:
        super().__init__(message)


class SearchQueryValidationError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class SearchBlockedError(Exception):
    def __init__(self, message: str = "Competitor discovery is temporarily unavailable.") -> None:
        super().__init__(message)


class SearchFetchFailedError(Exception):
    def __init__(self, message: str = "Competitor discovery is temporarily unavailable.") -> None:
        super().__init__(message)


class SearchParseFailedError(Exception):
    def __init__(self, message: str = "Amazon search results could not be read.") -> None:
        super().__init__(message)


class ReportUploadError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class ReportUnknownTypeError(Exception):
    def __init__(self, message: str = "This file is not a supported Amazon Seller Central report.") -> None:
        super().__init__(message)


class ReportAmbiguousTypeError(Exception):
    def __init__(self, message: str = "This file matches more than one Amazon report type.") -> None:
        super().__init__(message)


class ReportSchemaError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class ReportParseError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class BulkIngestError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class BulkLimitExceededError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class BulkLiveProviderForbiddenError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class PersistenceError(Exception):
    def __init__(self, message: str = "The report could not be saved.") -> None:
        super().__init__(message)


class PersistenceNotConfiguredError(Exception):
    def __init__(self, message: str = "Report history is not configured.") -> None:
        super().__init__(message)


class ReportNotFoundError(Exception):
    def __init__(self, report_id: str) -> None:
        self.report_id = report_id
        super().__init__(f"Saved analysis {report_id} was not found.")


class ConversationNotFoundError(Exception):
    def __init__(self, conversation_id: str) -> None:
        self.conversation_id = conversation_id
        super().__init__(f"Conversation {conversation_id} was not found.")


class PlanNotFoundError(Exception):
    def __init__(self, plan_id: str) -> None:
        self.plan_id = plan_id
        super().__init__(f"Validated plan {plan_id} was not found.")


class PlanInvalidError(Exception):
    def __init__(self, message: str = "The plan cannot be executed.") -> None:
        super().__init__(message)


class PlanHashMismatchError(Exception):
    def __init__(self, message: str = "The confirmation does not match this plan.") -> None:
        super().__init__(message)


class ConfirmationNonceInvalidError(Exception):
    def __init__(self, message: str = "This confirmation is not valid.") -> None:
        super().__init__(message)


class ConfirmationNonceExpiredError(Exception):
    def __init__(self, message: str = "This confirmation has expired. Ask again to continue.") -> None:
        super().__init__(message)


class ConfirmationNonceConsumedError(Exception):
    def __init__(self, message: str = "This confirmation was already used.") -> None:
        super().__init__(message)


class ScoringProfileValidationError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class ScoringProfileNotFoundError(Exception):
    def __init__(self, profile_id: str) -> None:
        self.profile_id = profile_id
        super().__init__(f"Scoring profile {profile_id} was not found.")


class ScoringProfileImmutableError(Exception):
    def __init__(self, message: str = "The Standard V2 scoring profile cannot be changed.") -> None:
        super().__init__(message)


class ScoringProfileConflictError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class PdfGenerationError(Exception):
    def __init__(self, message: str = "The PDF report could not be generated.") -> None:
        super().__init__(message)


class PdfNotGeneratedError(Exception):
    def __init__(self, report_id: str) -> None:
        self.report_id = report_id
        super().__init__(f"A PDF has not been generated for report {report_id}.")


class ArtifactStorageError(Exception):
    def __init__(self, message: str = "The generated report could not be stored.") -> None:
        super().__init__(message)


class ProfitValidationError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class ProfitModelNotFoundError(Exception):
    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        super().__init__(f"Profit model {model_id} was not found.")


class ProfitModelConflictError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class AdvertisingValidationError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class SpApiConfigurationError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class SpApiAuthenticationError(Exception):
    def __init__(self, message: str = "Amazon SP-API authentication failed.") -> None:
        super().__init__(message)


class SpApiRateLimitedError(Exception):
    """`retry_after_seconds` is populated (12B.3G) only when Amazon's
    response included a usable `Retry-After` header, so a higher-level
    durable-retry scheduler can honor it. `None` means no such signal was
    present — never guessed or defaulted here; the caller decides its own
    fallback (bounded exponential backoff with jitter)."""

    def __init__(
        self,
        message: str = "Amazon SP-API rate limit reached.",
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class SpApiRequestFailedError(Exception):
    def __init__(self, message: str = "Amazon SP-API request failed.") -> None:
        super().__init__(message)


class SpApiParseFailedError(Exception):
    def __init__(self, message: str = "Amazon SP-API response could not be parsed.") -> None:
        super().__init__(message)


class AmazonListingsParticipationNotFoundError(Exception):
    """A marketplace participation could not be resolved for this request.

    Deliberately identical whether the id is missing, malformed-but-valid-
    UUID, belongs to another organization, or is simply unknown — the
    caller must never be able to distinguish "doesn't exist" from
    "belongs to someone else" from this error alone. Only echoes the
    identifier the caller already supplied, never anything internal.
    """

    def __init__(self, marketplace_participation_id: str) -> None:
        self.marketplace_participation_id = marketplace_participation_id
        super().__init__(f"Marketplace participation {marketplace_participation_id} was not found.")


class AmazonSellerListingNotFoundError(Exception):
    """A listing could not be resolved within its (already-validated)
    marketplace participation — same sanitized shape for missing, foreign,
    or cross-participation listing ids."""

    def __init__(self, listing_id: str) -> None:
        self.listing_id = listing_id
        super().__init__(f"Listing {listing_id} was not found.")


class SpApiInvalidRequestError(Exception):
    """A non-transient 4xx response (not authentication, not rate limiting).

    Distinct from `SpApiRequestFailedError`, which covers transient failures
    (5xx, timeouts, transport errors) that were retried and ultimately
    exhausted. This one is never retried: repeating an identical request
    that Amazon rejected as malformed/unsupported (400, 404, 413, 415, ...)
    will not produce a different outcome.
    """

    def __init__(self, message: str = "Amazon SP-API rejected the request.") -> None:
        super().__init__(message)
