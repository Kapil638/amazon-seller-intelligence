"""Deterministic listing scoring rules (score version v1).

These thresholds are internal heuristics for explainable quality signals.
They are not Amazon policy requirements.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re

from app.models.listing_analysis import (
    AnalysisSection,
    AnalysisSections,
    Finding,
    FindingSeverity,
    ListingAnalysis,
    Recommendation,
    SectionStatus,
)
from app.models.product import Product

SCORE_VERSION = "v1"

SECTION_WEIGHTS: dict[str, float] = {
    "title": 0.20,
    "bullets": 0.25,
    "description": 0.15,
    "images": 0.15,
    "completeness": 0.15,
    "social_proof": 0.10,
}

TITLE_PREFERRED_MIN_CHARS = 80
TITLE_PREFERRED_MAX_CHARS = 180
TITLE_UNUSUALLY_SHORT_CHARS = 40
TITLE_UNUSUALLY_LONG_CHARS = 200
TITLE_MIN_WORDS = 4
TITLE_MAX_WORDS = 30

BULLET_TARGET_COUNT = 5
BULLET_SHORT_CHARS = 20
BULLET_LONG_CHARS = 250

DESCRIPTION_SHORT_CHARS = 80
DESCRIPTION_PREFERRED_MIN_CHARS = 250
DESCRIPTION_PREFERRED_MAX_CHARS = 2000
DESCRIPTION_LONG_CHARS = 3000

IMAGE_FEW_COUNT = 3
IMAGE_STRONG_COUNT = 6

STOP_WORDS = {
    "a",
    "an",
    "and",
    "for",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}

BENEFIT_STARTS = {
    "designed",
    "easy",
    "features",
    "helps",
    "ideal",
    "includes",
    "made",
    "perfect",
    "protects",
    "provides",
    "reduces",
    "supports",
}

COMPLETENESS_FIELDS = (
    "title",
    "brand",
    "price",
    "rating",
    "review_count",
    "bullet_points",
    "description",
    "images",
    "category",
    "bsr",
    "availability",
    "seller",
)

RECOMMENDATION_COPY: dict[str, str] = {
    "TITLE_MISSING": "Add a product title.",
    "TITLE_SHORT": "Expand the product title so it describes the item more clearly.",
    "TITLE_LONG": "Shorten the unusually long product title.",
    "TITLE_FEW_WORDS": "Add more descriptive words to the title.",
    "TITLE_MANY_WORDS": "Reduce repeated or unnecessary words in the title.",
    "TITLE_EXCESSIVE_CAPS": "Reduce excessive capitalization in the title.",
    "TITLE_REPEATED_WORDS": "Remove repeated words from the title.",
    "TITLE_KEYWORD_STUFFING": "Reduce repeated keywords in the title.",
    "NO_BULLETS": "Add product bullet points.",
    "FEW_BULLETS": "Add more product bullet points.",
    "SHORT_BULLET": "Expand unusually short bullet points.",
    "LONG_BULLET": "Shorten unusually long bullet points.",
    "DUPLICATE_BULLETS": "Remove duplicate bullet points.",
    "BULLET_EXCESSIVE_CAPS": "Reduce excessive capitalization in bullet points.",
    "BULLET_EXCESSIVE_PUNCTUATION": "Reduce excessive punctuation in bullet points.",
    "NO_DESCRIPTION": "Add a product description.",
    "DESCRIPTION_SHORT": "Expand the product description.",
    "DESCRIPTION_LONG": "Shorten the unusually long product description.",
    "NO_IMAGES": "Add product images.",
    "FEW_IMAGES": "Add additional product images.",
    "DUPLICATE_IMAGES": "Remove duplicate image URLs.",
    "LOW_RATING": "Review listing content that may be contributing to a low visible rating.",
    "FEW_REVIEWS": "Listing has limited visible review volume.",
}


@dataclass
class _SectionOutcome:
    section: AnalysisSection
    findings: list[Finding]


def analyze_listing(product: Product) -> ListingAnalysis:
    """Pure function: Product in, ListingAnalysis out. No I/O, no randomness."""
    title = _score_title(product)
    bullets = _score_bullets(product)
    description = _score_description(product)
    images = _score_images(product)
    completeness = _score_completeness(product)
    social = _score_social_proof(product)

    outcomes = {
        "title": title,
        "bullets": bullets,
        "description": description,
        "images": images,
        "completeness": completeness,
        "social_proof": social,
    }
    overall = _weighted_overall({name: item.section.score for name, item in outcomes.items()})
    findings = _sorted_findings([finding for item in outcomes.values() for finding in item.findings])
    recommendations = _recommendations_from(findings)

    return ListingAnalysis(
        overall_score=overall,
        score_version=SCORE_VERSION,
        sections=AnalysisSections(
            title=title.section,
            bullets=bullets.section,
            description=description.section,
            images=images.section,
            completeness=completeness.section,
            social_proof=social.section,
        ),
        findings=findings,
        recommendations=recommendations,
    )


def _blank(value: str | None) -> bool:
    return value is None or not value.strip()


def _clamp(value: float) -> int:
    return int(round(min(100, max(0, value))))


def _status(score: int) -> SectionStatus:
    if score >= 85:
        return SectionStatus.EXCELLENT
    if score >= 70:
        return SectionStatus.GOOD
    if score >= 50:
        return SectionStatus.FAIR
    return SectionStatus.POOR


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9]+", text)


def _weighted_overall(scores: dict[str, int]) -> int:
    total = sum(scores[name] * weight for name, weight in SECTION_WEIGHTS.items())
    return _clamp(total)


def _sorted_findings(findings: list[Finding]) -> list[Finding]:
    order = {
        FindingSeverity.HIGH: 0,
        FindingSeverity.MEDIUM: 1,
        FindingSeverity.LOW: 2,
        FindingSeverity.INFO: 3,
    }
    return sorted(findings, key=lambda item: (order[item.severity], item.category, item.code))


def _recommendations_from(findings: list[Finding]) -> list[Recommendation]:
    seen: set[str] = set()
    recommendations: list[Recommendation] = []
    for finding in findings:
        if finding.severity == FindingSeverity.INFO:
            continue
        message = RECOMMENDATION_COPY.get(finding.code)
        if not message or finding.code in seen:
            continue
        seen.add(finding.code)
        recommendations.append(
            Recommendation(code=finding.code, category=finding.category, message=message)
        )
    return recommendations


def _score_title(product: Product) -> _SectionOutcome:
    title = product.title.strip()
    findings: list[Finding] = []
    notes: list[str] = []
    words = _words(title)
    char_count = len(title)
    word_count = len(words)
    significant = [word.lower() for word in words if word.lower() not in STOP_WORDS and len(word) > 2]
    counts = Counter(significant)
    repeated = sorted(word for word, count in counts.items() if count >= 2)
    stuffed = sorted(word for word, count in counts.items() if count >= 3)
    alpha_tokens = [token for token in title.split() if any(char.isalpha() for char in token)]
    caps_tokens = [token for token in alpha_tokens if token.isupper() and len(token) > 1]
    caps_ratio = (len(caps_tokens) / len(alpha_tokens)) if alpha_tokens else 0.0
    avg_word_len = (sum(len(word) for word in words) / word_count) if word_count else 0.0

    metrics = {
        "character_count": char_count,
        "word_count": word_count,
        "repeated_words": repeated,
        "keyword_repeats": stuffed,
        "capitalization_ratio": round(caps_ratio, 3),
        "average_word_length": round(avg_word_len, 2),
        "capitalization": "excessive" if caps_ratio >= 0.4 or title.isupper() else "normal",
    }

    if _blank(title):
        findings.append(
            Finding(
                severity=FindingSeverity.HIGH,
                category="title",
                code="TITLE_MISSING",
                message="No product title was provided.",
            )
        )
        notes.append("Title is missing.")
        section = AnalysisSection(
            name="title",
            score=0,
            status=_status(0),
            metrics=metrics,
            findings=notes,
        )
        return _SectionOutcome(section, findings)

    score = 100.0
    if char_count < TITLE_UNUSUALLY_SHORT_CHARS:
        score -= 30
        findings.append(
            Finding(
                severity=FindingSeverity.MEDIUM,
                category="title",
                code="TITLE_SHORT",
                message="Title is unusually short.",
            )
        )
        notes.append("Title is unusually short.")
    elif char_count < TITLE_PREFERRED_MIN_CHARS:
        score -= 12
        notes.append("Title is shorter than the typical descriptive range used by this scorer.")
    elif char_count <= TITLE_PREFERRED_MAX_CHARS:
        notes.append("Title length is within the preferred range used by this scorer.")
        findings.append(
            Finding(
                severity=FindingSeverity.INFO,
                category="title",
                code="TITLE_LENGTH_OK",
                message="Title length is within the preferred range used by this scorer.",
            )
        )
    elif char_count <= TITLE_UNUSUALLY_LONG_CHARS:
        score -= 8
        notes.append("Title is longer than the typical descriptive range used by this scorer.")
    else:
        score -= 22
        findings.append(
            Finding(
                severity=FindingSeverity.MEDIUM,
                category="title",
                code="TITLE_LONG",
                message="Title is unusually long.",
            )
        )
        notes.append("Title is unusually long.")

    if word_count < TITLE_MIN_WORDS:
        score -= 15
        findings.append(
            Finding(
                severity=FindingSeverity.MEDIUM,
                category="title",
                code="TITLE_FEW_WORDS",
                message="Title contains very few words.",
            )
        )
    elif word_count > TITLE_MAX_WORDS:
        score -= 10
        findings.append(
            Finding(
                severity=FindingSeverity.LOW,
                category="title",
                code="TITLE_MANY_WORDS",
                message="Title contains an unusually large number of words.",
            )
        )

    if caps_ratio >= 0.4 or title.isupper():
        score -= 15
        findings.append(
            Finding(
                severity=FindingSeverity.MEDIUM,
                category="title",
                code="TITLE_EXCESSIVE_CAPS",
                message="Title uses excessive capitalization.",
            )
        )
        notes.append("Capitalization is excessive.")
    else:
        notes.append("Capitalization is normal.")

    if stuffed:
        score -= 20
        findings.append(
            Finding(
                severity=FindingSeverity.MEDIUM,
                category="title",
                code="TITLE_KEYWORD_STUFFING",
                message="Title repeats the same keyword three or more times.",
            )
        )
        notes.append("Possible keyword stuffing pattern detected.")
    elif repeated:
        score -= min(16, 8 * len(repeated))
        findings.append(
            Finding(
                severity=FindingSeverity.LOW,
                category="title",
                code="TITLE_REPEATED_WORDS",
                message="Title contains repeated words.",
            )
        )
        notes.append(f"Duplicate words: {len(repeated)}.")

    if avg_word_len >= 12:
        score -= 6
        notes.append("Average word length is high, which can reduce scannability.")

    score_int = _clamp(score)
    section = AnalysisSection(
        name="title",
        score=score_int,
        status=_status(score_int),
        metrics=metrics,
        findings=notes,
    )
    return _SectionOutcome(section, findings)


def _score_bullets(product: Product) -> _SectionOutcome:
    bullets = [item.strip() for item in product.bullet_points if item.strip()]
    findings: list[Finding] = []
    notes: list[str] = []
    lengths = [len(item) for item in bullets]
    average_length = round(sum(lengths) / len(lengths), 1) if lengths else 0.0
    short_indexes = [i + 1 for i, item in enumerate(bullets) if len(item) < BULLET_SHORT_CHARS]
    long_indexes = [i + 1 for i, item in enumerate(bullets) if len(item) > BULLET_LONG_CHARS]
    normalized = [re.sub(r"\s+", " ", item.lower()) for item in bullets]
    duplicate_count = len(normalized) - len(set(normalized))
    benefit_starts = sum(1 for item in bullets if item.split(" ", 1)[0].lower().strip(",.") in BENEFIT_STARTS)
    caps_indexes = []
    punct_indexes = []
    for index, item in enumerate(bullets, start=1):
        tokens = [token for token in item.split() if any(char.isalpha() for char in token)]
        caps = [token for token in tokens if token.isupper() and len(token) > 1]
        if tokens and len(caps) / len(tokens) >= 0.5:
            caps_indexes.append(index)
        if item.count("!") + item.count("?") > 2:
            punct_indexes.append(index)

    metrics = {
        "bullet_count": len(bullets),
        "empty_bullets_ignored": len(product.bullet_points) - len(bullets),
        "average_length": average_length,
        "short_bullet_indexes": short_indexes,
        "long_bullet_indexes": long_indexes,
        "duplicate_count": duplicate_count,
        "benefit_oriented_starts": benefit_starts,
        "excessive_caps_indexes": caps_indexes,
        "excessive_punctuation_indexes": punct_indexes,
    }

    if not bullets:
        findings.append(
            Finding(
                severity=FindingSeverity.HIGH,
                category="bullets",
                code="NO_BULLETS",
                message="No bullet points were provided.",
            )
        )
        notes.append("No bullet points were provided.")
        section = AnalysisSection(
            name="bullets",
            score=0,
            status=_status(0),
            metrics=metrics,
            findings=notes,
        )
        return _SectionOutcome(section, findings)

    score = 100.0
    if len(bullets) < 3:
        score -= 30
        findings.append(
            Finding(
                severity=FindingSeverity.MEDIUM,
                category="bullets",
                code="FEW_BULLETS",
                message=f"Only {len(bullets)} bullet point(s) were provided.",
            )
        )
        notes.append("Few bullet points are present.")
    elif len(bullets) < BULLET_TARGET_COUNT:
        score -= 12
        notes.append("Bullet count is below the typical set of 5 used by this scorer.")
    else:
        notes.append("Bullet count meets the typical set used by this scorer.")

    if short_indexes:
        score -= min(24, 8 * len(short_indexes))
        findings.append(
            Finding(
                severity=FindingSeverity.LOW,
                category="bullets",
                code="SHORT_BULLET",
                message=f"Bullet(s) {', '.join(f'#{i}' for i in short_indexes)} are unusually short.",
            )
        )
        notes.append("Some bullets are unusually short.")

    if long_indexes:
        score -= min(30, 10 * len(long_indexes))
        findings.append(
            Finding(
                severity=FindingSeverity.MEDIUM,
                category="bullets",
                code="LONG_BULLET",
                message=f"Bullet(s) {', '.join(f'#{i}' for i in long_indexes)} are unusually long.",
            )
        )
        notes.append("Some bullets are unusually long.")

    if duplicate_count:
        score -= 20
        findings.append(
            Finding(
                severity=FindingSeverity.MEDIUM,
                category="bullets",
                code="DUPLICATE_BULLETS",
                message="One or more bullet points are duplicated.",
            )
        )
        notes.append("Duplicate bullets were found.")

    if caps_indexes:
        score -= min(15, 5 * len(caps_indexes))
        findings.append(
            Finding(
                severity=FindingSeverity.LOW,
                category="bullets",
                code="BULLET_EXCESSIVE_CAPS",
                message="One or more bullets use excessive capitalization.",
            )
        )

    if punct_indexes:
        score -= min(10, 5 * len(punct_indexes))
        findings.append(
            Finding(
                severity=FindingSeverity.LOW,
                category="bullets",
                code="BULLET_EXCESSIVE_PUNCTUATION",
                message="One or more bullets use excessive punctuation.",
            )
        )

    score += min(8, benefit_starts * 2)

    score_int = _clamp(score)
    section = AnalysisSection(
        name="bullets",
        score=score_int,
        status=_status(score_int),
        metrics=metrics,
        findings=notes,
    )
    return _SectionOutcome(section, findings)


def _score_description(product: Product) -> _SectionOutcome:
    description = (product.description or "").strip()
    char_count = len(description)
    findings: list[Finding] = []
    notes: list[str] = []
    metrics = {
        "present": bool(description),
        "character_count": char_count,
    }

    if not description:
        findings.append(
            Finding(
                severity=FindingSeverity.HIGH,
                category="description",
                code="NO_DESCRIPTION",
                message="No product description was provided.",
            )
        )
        notes.append("Description is missing.")
        section = AnalysisSection(
            name="description",
            score=0,
            status=_status(0),
            metrics=metrics,
            findings=notes,
        )
        return _SectionOutcome(section, findings)

    score = 100.0
    if char_count < DESCRIPTION_SHORT_CHARS:
        score = 40
        findings.append(
            Finding(
                severity=FindingSeverity.MEDIUM,
                category="description",
                code="DESCRIPTION_SHORT",
                message="Description is unusually short.",
            )
        )
        notes.append("Description is unusually short.")
    elif char_count < DESCRIPTION_PREFERRED_MIN_CHARS:
        score = 75
        notes.append("Description is present but shorter than the typical range used by this scorer.")
    elif char_count <= DESCRIPTION_PREFERRED_MAX_CHARS:
        score = 95
        notes.append("Description length is within the preferred range used by this scorer.")
    elif char_count <= DESCRIPTION_LONG_CHARS:
        score = 78
        notes.append("Description is longer than the typical range used by this scorer.")
    else:
        score = 55
        findings.append(
            Finding(
                severity=FindingSeverity.LOW,
                category="description",
                code="DESCRIPTION_LONG",
                message="Description is unusually long.",
            )
        )
        notes.append("Description is unusually long.")

    score_int = _clamp(score)
    section = AnalysisSection(
        name="description",
        score=score_int,
        status=_status(score_int),
        metrics=metrics,
        findings=notes,
    )
    return _SectionOutcome(section, findings)


def _score_images(product: Product) -> _SectionOutcome:
    urls = [image.url.strip() for image in product.images if image.url.strip()]
    unique = set(urls)
    duplicate_count = len(urls) - len(unique)
    findings: list[Finding] = []
    notes: list[str] = []
    metrics = {
        "image_count": len(urls),
        "has_images": bool(urls),
        "duplicate_url_count": duplicate_count,
    }

    if not urls:
        findings.append(
            Finding(
                severity=FindingSeverity.HIGH,
                category="images",
                code="NO_IMAGES",
                message="No product images were provided.",
            )
        )
        notes.append("No images were provided. Image quality was not evaluated.")
        section = AnalysisSection(
            name="images",
            score=0,
            status=_status(0),
            metrics=metrics,
            findings=notes,
        )
        return _SectionOutcome(section, findings)

    if len(urls) == 1:
        score = 40
        findings.append(
            Finding(
                severity=FindingSeverity.MEDIUM,
                category="images",
                code="FEW_IMAGES",
                message="Only 1 product image is available.",
            )
        )
        notes.append("Only one image URL is present.")
    elif len(urls) <= IMAGE_FEW_COUNT:
        score = 65
        findings.append(
            Finding(
                severity=FindingSeverity.LOW,
                category="images",
                code="FEW_IMAGES",
                message=f"Only {len(urls)} product images are available.",
            )
        )
        notes.append("Image count is limited. Quality was not evaluated.")
    elif len(urls) <= IMAGE_STRONG_COUNT:
        score = 88
        notes.append("Several image URLs are present. Quality was not evaluated.")
    else:
        score = 95
        notes.append("A large set of image URLs is present. Quality was not evaluated.")

    if duplicate_count:
        score -= 15
        findings.append(
            Finding(
                severity=FindingSeverity.MEDIUM,
                category="images",
                code="DUPLICATE_IMAGES",
                message="One or more image URLs are duplicated.",
            )
        )
        notes.append("Duplicate image URLs were found.")

    score_int = _clamp(score)
    section = AnalysisSection(
        name="images",
        score=score_int,
        status=_status(score_int),
        metrics=metrics,
        findings=notes,
    )
    return _SectionOutcome(section, findings)


def _score_completeness(product: Product) -> _SectionOutcome:
    populated = {
        "title": not _blank(product.title),
        "brand": not _blank(product.brand),
        "price": product.price is not None,
        "rating": product.rating is not None,
        "review_count": product.review_count is not None,
        "bullet_points": bool(product.bullet_points),
        "description": not _blank(product.description),
        "images": bool(product.images),
        "category": not _blank(product.category),
        "bsr": product.bsr is not None,
        "availability": not _blank(product.availability),
        "seller": product.seller is not None and not _blank(product.seller.name),
    }
    present_count = sum(1 for value in populated.values() if value)
    total = len(COMPLETENESS_FIELDS)
    score = _clamp(100 * present_count / total)
    missing = [name for name, value in populated.items() if not value]
    notes = [f"{present_count} of {total} listing fields are populated."]
    findings = [
        Finding(
            severity=FindingSeverity.INFO,
            category="completeness",
            code="COMPLETENESS_SUMMARY",
            message=f"{present_count} of {total} listing fields are populated.",
        )
    ]
    if missing:
        findings.append(
            Finding(
                severity=FindingSeverity.LOW,
                category="completeness",
                code="COMPLETENESS_GAPS",
                message=f"Missing data: {', '.join(missing)}.",
            )
        )
        notes.append(f"Missing data: {', '.join(missing)}.")

    section = AnalysisSection(
        name="completeness",
        score=score,
        status=_status(score),
        metrics={
            "fields_present": present_count,
            "fields_total": total,
            "populated": populated,
            "missing": missing,
        },
        findings=notes,
    )
    return _SectionOutcome(section, findings)


def _score_social_proof(product: Product) -> _SectionOutcome:
    findings: list[Finding] = []
    notes: list[str] = []
    rating = product.rating
    reviews = product.review_count
    metrics = {
        "rating": rating,
        "review_count": reviews,
        "data_available": rating is not None or reviews is not None,
    }

    if rating is None and reviews is None:
        score = 50
        notes.append("Rating and review count were not provided, so social proof could not be assessed.")
        findings.append(
            Finding(
                severity=FindingSeverity.INFO,
                category="social_proof",
                code="SOCIAL_PROOF_UNAVAILABLE",
                message="Rating and review count were not provided, so social proof could not be assessed.",
            )
        )
        section = AnalysisSection(
            name="social_proof",
            score=score,
            status=_status(score),
            metrics=metrics,
            findings=notes,
        )
        return _SectionOutcome(section, findings)

    review_value = reviews if reviews is not None else 0
    rating_value = rating if rating is not None else 0.0

    if review_value == 0:
        score = 25
        findings.append(
            Finding(
                severity=FindingSeverity.MEDIUM,
                category="social_proof",
                code="FEW_REVIEWS",
                message="No reviews are visible on this listing.",
            )
        )
        notes.append("No visible reviews.")
    elif rating_value >= 4.5 and review_value >= 500:
        score = 95
        notes.append("Strong visible social proof relative to a new/unreviewed listing.")
        findings.append(
            Finding(
                severity=FindingSeverity.INFO,
                category="social_proof",
                code="STRONG_SOCIAL_PROOF",
                message="Strong visible social proof relative to a new/unreviewed listing.",
            )
        )
    elif rating_value >= 4.3 and review_value >= 100:
        score = 86
        notes.append("Solid visible rating and review volume relative to a new/unreviewed listing.")
    elif rating_value >= 4.0 and review_value >= 50:
        score = 74
        notes.append("Moderate visible social proof.")
    elif rating_value >= 4.0 and review_value < 20:
        score = 58
        findings.append(
            Finding(
                severity=FindingSeverity.LOW,
                category="social_proof",
                code="FEW_REVIEWS",
                message="Visible review volume is still limited.",
            )
        )
        notes.append("Rating is available, but review volume is limited.")
    elif rating_value >= 3.5:
        score = 52
        notes.append("Visible rating is mixed relative to a new/unreviewed listing.")
    else:
        score = 32
        findings.append(
            Finding(
                severity=FindingSeverity.MEDIUM,
                category="social_proof",
                code="LOW_RATING",
                message="Visible customer rating is low relative to a typical well-reviewed listing.",
            )
        )
        notes.append("Visible rating is low. This does not by itself explain conversion.")

    if rating is None:
        notes.append("Rating was not provided; review count alone was used.")
    if reviews is None:
        notes.append("Review count was not provided; rating alone was used.")
        score = min(score, 60)

    score_int = _clamp(score)
    section = AnalysisSection(
        name="social_proof",
        score=score_int,
        status=_status(score_int),
        metrics=metrics,
        findings=notes,
    )
    return _SectionOutcome(section, findings)
