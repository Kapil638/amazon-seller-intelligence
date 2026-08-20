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
