from app.models.listing_analysis import FindingSeverity, ListingAnalysis
from app.models.bulk import BulkPriority


def classify_priority(analysis: ListingAnalysis) -> BulkPriority:
    """Deterministic portfolio priority. AI never assigns this."""

    high_findings = [item for item in analysis.findings if item.severity == FindingSeverity.HIGH]
    medium_findings = [item for item in analysis.findings if item.severity == FindingSeverity.MEDIUM]
    if analysis.overall_score < 50 or high_findings:
        return "high"
    if analysis.overall_score <= 69 or len(medium_findings) >= 2:
        return "medium"
    return "low"
