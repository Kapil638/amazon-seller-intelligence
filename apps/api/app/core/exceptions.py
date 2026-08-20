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
    def __init__(
        self,
        message: str = (
            "Live provider calls are disabled for bulk analysis. "
            "This protects Rainforest and OpenAI credits. "
            "Keep BULK_LIVE_PROVIDER_CALLS_ENABLED=false during mock testing."
        ),
    ) -> None:
        super().__init__(message)
