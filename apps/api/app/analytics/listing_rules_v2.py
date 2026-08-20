"""Deterministic listing-quality scoring (score version v2).

Listing quality is structural coverage of seller-controlled content.
Market outcomes (rating, reviews, BSR, price, seller, availability) are
reported separately and are not mixed into the quality score.

These thresholds are internal heuristics. They are not Amazon policy,
search volume, conversion estimates, or image-quality judgments.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re

from app.models.listing_analysis import (
    AnalysisSection,
    Finding,
    FindingSeverity,
    SectionStatus,
)
from app.models.listing_analysis_v2 import (
    CoverageField,
    CoverageGroup,
    DataCoverage,
    EvidenceState,
    ListingAnalysisV2,
    ListingQualitySections,
    MarketSignals,
    RecommendationPriority,
    V2Recommendation,
)
from app.models.product import Product

SCORE_VERSION = "v2"

SECTION_WEIGHTS: dict[str, float] = {
    "title": 0.20,
    "bullets": 0.25,
    "description_a_plus": 0.20,
    "media_coverage": 0.20,
    "content_structure": 0.15,
}

STOP_WORDS = {
    "a",
    "an",
    "and",
    "for",
    "from",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
    "your",
}

GENERIC_SPEC_NAMES = {
    "asin",
    "best sellers rank",
    "brand",
    "brand name",
    "customer reviews",
    "date first available",
}

SKIP_SPEC_VALUE_TOKENS = {
    "see",
    "top",
    "stars",
    "star",
    "ratings",
    "reviews",
    "included",
    "required",
}

BENEFIT_STARTS = {
    "designed",
    "helps",
    "ideal",
    "includes",
    "made",
    "protects",
    "provides",
    "reduces",
    "supports",
}

RECOMMENDATION_COPY: dict[str, str] = {
    "TITLE_MISSING": "Add a product title that names the item and its primary attributes.",
    "TITLE_TOO_SHORT": "The observed title is unusually short. Add identifying attributes already known from the listing.",
    "TITLE_EXCESSIVELY_LONG": "The observed title is unusually long. Reduce repeated or filler wording.",
    "TITLE_REPETITION": "Remove repeated significant words from the title.",
    "TITLE_CAPS_HEAVY": "Reduce ALL-CAPS wording in the title.",
    "TITLE_PUNCTUATION_HEAVY": "Reduce decorative or repeated punctuation in the title.",
    "TITLE_POSSIBLE_STUFFING": "The title repeats the same significant term many times. Use each term once where it adds information.",
    "NO_BULLETS": "Add product bullet points that describe attributes and use cases.",
    "LOW_BULLET_COVERAGE": "Few bullet points were observed. Add distinct bullets for remaining product attributes.",
    "BULLET_DUPLICATION": "Remove duplicate bullet points.",
    "BULLET_REPETITION": "Several bullets repeat the same wording. Make each bullet cover a distinct attribute.",
    "BULLET_CAPS_HEAVY": "Reduce ALL-CAPS wording in bullet points.",
    "BULLET_PUNCTUATION_HEAVY": "Reduce excessive punctuation in bullet points.",
    "SPECIFICATION_COVERAGE_GAP": "Some structured specifications are not mentioned in the bullets. Add those observed attributes where they are seller-controlled facts.",
    "PRODUCT_TERM_COVERAGE_GAP": "Important terms from the title, category, or attributes are missing from the bullets.",
    "POSSIBLE_BULLET_STUFFING": "The same term is repeated across bullets without adding new information.",
    "DESCRIPTION_MISSING": "No standard product description was observed. Add a description or A+ copy that explains the product.",
    "DESCRIPTION_THIN": "The standard description is very short. Expand it unless A+ already covers the same facts.",
    "A_PLUS_NOT_PRESENT": "A+ Content was reported as not present. Consider adding A+ modules if the category supports them.",
    "MAIN_IMAGE_MISSING": "No main product image was observed.",
    "LIMITED_GALLERY": "Only a small image gallery was observed. Add additional product-detail images if available.",
    "DUPLICATE_MEDIA": "Duplicate image URLs were observed. Remove repeated gallery entries.",
    "STRUCTURE_DUPLICATE_COPY": "The same copy is repeated across title, bullets, or description. Diversify the wording while keeping facts consistent.",
    "STRUCTURE_SPEC_GAP": "Structured specifications are not represented in seller-facing copy (title, bullets, description, or A+ text).",
    "STRUCTURE_REPETITION": "The listing repeats the same terms across fields. Reduce repetition without dropping facts.",
}


@dataclass
class _SectionOutcome:
    section: AnalysisSection
    findings: list[Finding]


def analyze_listing_v2(product: Product) -> ListingAnalysisV2:
    title = _score_title(product)
    bullets = _score_bullets(product)
    description = _score_description_a_plus(product)
    media = _score_media(product)
    structure = _score_content_structure(product)

    outcomes = {
        "title": title,
        "bullets": bullets,
        "description_a_plus": description,
        "media_coverage": media,
        "content_structure": structure,
    }
    overall = _weighted_overall({name: item.section.score for name, item in outcomes.items()})
    findings = _sorted_findings([finding for item in outcomes.values() for finding in item.findings])
    findings.extend(_coverage_info_findings(product))
    findings = _sorted_findings(findings)
    recommendations = _recommendations_from(findings)

    return ListingAnalysisV2(
        listing_quality_score=overall,
        score_version=SCORE_VERSION,
        status=_status(overall),
        sections=ListingQualitySections(
            title=title.section,
            bullets=bullets.section,
            description_a_plus=description.section,
            media_coverage=media.section,
            content_structure=structure.section,
        ),
        market_signals=_market_signals(product),
        data_coverage=_data_coverage(product),
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


def _significant_terms(text: str) -> list[str]:
    return [
        word.lower()
        for word in _words(text)
        if word.lower() not in STOP_WORDS and len(word) > 2
    ]


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
    seen: set[tuple[str, str]] = set()
    unique: list[Finding] = []
    for finding in findings:
        key = (finding.code, finding.message)
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)
    return sorted(unique, key=lambda item: (order[item.severity], item.category, item.code))


def _priority_for(severity: FindingSeverity) -> RecommendationPriority | None:
    if severity == FindingSeverity.HIGH:
        return RecommendationPriority.HIGH
    if severity == FindingSeverity.MEDIUM:
        return RecommendationPriority.MEDIUM
    if severity == FindingSeverity.LOW:
        return RecommendationPriority.LOW
    return None


def _recommendations_from(findings: list[Finding]) -> list[V2Recommendation]:
    seen: set[str] = set()
    recommendations: list[V2Recommendation] = []
    for finding in findings:
        priority = _priority_for(finding.severity)
        if priority is None:
            continue
        action = RECOMMENDATION_COPY.get(finding.code)
        if not action or finding.code in seen:
            continue
        seen.add(finding.code)
        recommendations.append(
            V2Recommendation(
                code=finding.code,
                category=finding.category,
                priority=priority,
                action=action,
                finding_code=finding.code,
            )
        )
    return recommendations


def _score_title(product: Product) -> _SectionOutcome:
    title = product.title.strip()
    findings: list[Finding] = []
    notes: list[str] = []
    words = _words(title)
    char_count = len(title)
    word_count = len(words)
    significant = _significant_terms(title)
    counts = Counter(significant)
    repeated = sorted(word for word, count in counts.items() if count >= 2)
    stuffed = sorted(word for word, count in counts.items() if count >= 3)
    alpha_tokens = [token for token in title.split() if any(char.isalpha() for char in token)]
    caps_tokens = [token for token in alpha_tokens if token.isupper() and len(token) > 1]
    caps_ratio = (len(caps_tokens) / len(alpha_tokens)) if alpha_tokens else 0.0
    punct_marks = title.count("!") + title.count("?") + title.count("|") + title.count("*")
    avg_word_len = (sum(len(word) for word in words) / word_count) if word_count else 0.0

    metrics = {
        "character_count": char_count,
        "word_count": word_count,
        "repeated_words": repeated,
        "keyword_repeats": stuffed,
        "capitalization_ratio": round(caps_ratio, 3),
        "punctuation_marks": punct_marks,
        "average_word_length": round(avg_word_len, 2),
        "scores_structural_health_only": True,
    }

    if _blank(title):
        findings.append(_finding(FindingSeverity.HIGH, "title", "TITLE_MISSING", "No product title was provided."))
        notes.append("Title is missing.")
        return _outcome("title", 0, metrics, notes, findings)

    score = 100.0
    if char_count < 20:
        score -= 35
        findings.append(_finding(FindingSeverity.MEDIUM, "title", "TITLE_TOO_SHORT", "Title is unusually short."))
        notes.append("Title character count is unusually low.")
    elif char_count < 40:
        score -= 22
        findings.append(_finding(FindingSeverity.MEDIUM, "title", "TITLE_TOO_SHORT", "Title is unusually short."))
        notes.append("Title character count is unusually low.")
    elif char_count < 55:
        score -= 8
        findings.append(_finding(FindingSeverity.LOW, "title", "TITLE_TOO_SHORT", "Title is shorter than typical descriptive listings used by this scorer."))
        notes.append("Title is relatively short. This is a structural observation, not an Amazon rule.")
    elif char_count > 280:
        score -= 22
        findings.append(_finding(FindingSeverity.MEDIUM, "title", "TITLE_EXCESSIVELY_LONG", "Title is unusually long."))
        notes.append("Title character count is unusually high.")
    elif char_count > 220:
        score -= 10
        findings.append(_finding(FindingSeverity.LOW, "title", "TITLE_EXCESSIVELY_LONG", "Title is longer than typical descriptive listings used by this scorer."))
        notes.append("Title is relatively long. This is a structural observation, not an Amazon rule.")
    else:
        notes.append("Title length is within a typical descriptive range used by this scorer.")

    if word_count < 3:
        score -= 18
        findings.append(_finding(FindingSeverity.MEDIUM, "title", "TITLE_TOO_SHORT", "Title contains very few words."))
    elif word_count > 32:
        score -= 10
        findings.append(_finding(FindingSeverity.LOW, "title", "TITLE_EXCESSIVELY_LONG", "Title contains an unusually large number of words."))

    if caps_ratio >= 0.4 or title.isupper():
        score -= 15
        findings.append(_finding(FindingSeverity.MEDIUM, "title", "TITLE_CAPS_HEAVY", "Title uses a high share of capitalized tokens."))
        notes.append("Capitalization is heavy.")
    if punct_marks >= 3 or "!!" in title or "??" in title:
        score -= 10
        findings.append(_finding(FindingSeverity.LOW, "title", "TITLE_PUNCTUATION_HEAVY", "Title uses heavy punctuation."))
    if stuffed:
        score -= 20
        findings.append(_finding(FindingSeverity.MEDIUM, "title", "TITLE_POSSIBLE_STUFFING", "Title repeats the same significant term three or more times."))
        notes.append("Possible term-stuffing pattern in the title.")
    elif repeated:
        score -= min(16, 8 * len(repeated))
        findings.append(_finding(FindingSeverity.LOW, "title", "TITLE_REPETITION", "Title contains repeated significant words."))
    if avg_word_len >= 14:
        score -= 6
        notes.append("Average word length is high, which can reduce scannability.")

    return _outcome("title", _clamp(score), metrics, notes, findings)


def _score_bullets(product: Product) -> _SectionOutcome:
    bullets = [item.strip() for item in product.bullet_points if item.strip()]
    findings: list[Finding] = []
    notes: list[str] = []
    blob = " ".join(bullets).lower()
    lengths = [len(item) for item in bullets]
    average_length = round(sum(lengths) / len(lengths), 1) if lengths else 0.0
    short_indexes = [i + 1 for i, item in enumerate(bullets) if len(item) < 20]
    long_indexes = [i + 1 for i, item in enumerate(bullets) if len(item) > 280]
    normalized = [re.sub(r"\s+", " ", item.lower()) for item in bullets]
    duplicate_count = len(normalized) - len(set(normalized))
    similar_pairs = _similar_bullet_pairs(normalized)
    term_counts = Counter(term for item in bullets for term in _significant_terms(item))
    stuffed_terms = sorted(term for term, count in term_counts.items() if count >= 4)
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

    spec_gaps = _specification_gaps(product, blob)
    product_term_gaps = _product_term_gaps(product, blob)

    metrics = {
        "bullet_count": len(bullets),
        "average_length": average_length,
        "duplicate_count": duplicate_count,
        "similar_pair_count": similar_pairs,
        "short_bullet_indexes": short_indexes,
        "long_bullet_indexes": long_indexes,
        "specification_gaps": spec_gaps,
        "product_term_gaps": product_term_gaps[:12],
        "benefit_oriented_starts": benefit_starts,
        "keyword_volume_unknown": True,
    }

    if not bullets:
        findings.append(_finding(FindingSeverity.HIGH, "bullets", "NO_BULLETS", "No bullet points were provided."))
        notes.append("No bullet points were provided.")
        return _outcome("bullets", 0, metrics, notes, findings)

    score = 100.0
    if len(bullets) < 3:
        score -= 28
        findings.append(_finding(FindingSeverity.MEDIUM, "bullets", "LOW_BULLET_COVERAGE", f"Only {len(bullets)} bullet point(s) were observed."))
        notes.append("Bullet coverage is limited.")
    elif len(bullets) < 5:
        score -= 10
        findings.append(_finding(FindingSeverity.LOW, "bullets", "LOW_BULLET_COVERAGE", f"Only {len(bullets)} bullet points were observed."))
        notes.append("Fewer than five bullets were observed. Five is a scorer heuristic, not an Amazon rule.")
    else:
        notes.append("Several distinct bullets are present.")

    if short_indexes:
        score -= min(18, 6 * len(short_indexes))
        notes.append("Some bullets are unusually short.")
    if long_indexes:
        score -= min(18, 6 * len(long_indexes))
        notes.append("Some bullets are unusually long.")
    if duplicate_count:
        score -= 22
        findings.append(_finding(FindingSeverity.MEDIUM, "bullets", "BULLET_DUPLICATION", "One or more bullet points are duplicated."))
    if similar_pairs:
        score -= min(16, 8 * similar_pairs)
        findings.append(_finding(FindingSeverity.LOW, "bullets", "BULLET_REPETITION", "Two or more bullets share highly similar wording."))
    if caps_indexes:
        score -= min(12, 4 * len(caps_indexes))
        findings.append(_finding(FindingSeverity.LOW, "bullets", "BULLET_CAPS_HEAVY", "One or more bullets use heavy capitalization."))
    if punct_indexes:
        score -= min(10, 5 * len(punct_indexes))
        findings.append(_finding(FindingSeverity.LOW, "bullets", "BULLET_PUNCTUATION_HEAVY", "One or more bullets use heavy punctuation."))
    if spec_gaps:
        score -= min(20, 4 * len(spec_gaps))
        findings.append(
            _finding(
                FindingSeverity.MEDIUM,
                "bullets",
                "SPECIFICATION_COVERAGE_GAP",
                f"{len(spec_gaps)} structured specification(s) are not mentioned in the bullets.",
            )
        )
        notes.append("Specification coverage in bullets is incomplete.")
    if product_term_gaps:
        gap_ratio = len(product_term_gaps) / max(len(product_term_gaps) + 1, 1)
        if len(product_term_gaps) >= 3:
            score -= 12 if gap_ratio else 0
            findings.append(
                _finding(
                    FindingSeverity.LOW,
                    "bullets",
                    "PRODUCT_TERM_COVERAGE_GAP",
                    "Some title, category, or attribute terms are not present in the bullets.",
                )
            )
    if stuffed_terms:
        score -= 15
        findings.append(_finding(FindingSeverity.MEDIUM, "bullets", "POSSIBLE_BULLET_STUFFING", "The same significant term appears four or more times across bullets."))
    score += min(4, benefit_starts)
    notes.append("Bullet SEO readiness measures coverage of observed product terms, not keyword search volume.")

    return _outcome("bullets", _clamp(score), metrics, notes, findings)


def _similar_bullet_pairs(normalized: list[str]) -> int:
    pairs = 0
    for index, left in enumerate(normalized):
        left_terms = set(_significant_terms(left))
        if not left_terms:
            continue
        for right in normalized[index + 1 :]:
            if left == right:
                continue
            right_terms = set(_significant_terms(right))
            if not right_terms:
                continue
            overlap = len(left_terms & right_terms) / len(left_terms | right_terms)
            if overlap >= 0.75:
                pairs += 1
    return pairs


def _specification_gaps(product: Product, blob: str) -> list[str]:
    gaps: list[str] = []
    for spec in product.specifications:
        if spec.name.strip().lower() in GENERIC_SPEC_NAMES:
            continue
        tokens = [
            token
            for token in _significant_terms(spec.value)
            if token not in SKIP_SPEC_VALUE_TOKENS
        ]
        if not tokens:
            continue
        if not any(token in blob for token in tokens):
            gaps.append(spec.name)
    return gaps


def _product_term_gaps(product: Product, blob: str) -> list[str]:
    sources = [product.title, product.brand or "", product.category or ""]
    sources.extend(node.name for node in product.category_path)
    if product.attributes:
        sources.append(product.attributes.manufacturer or "")
        sources.extend(product.attributes.ingredients)
        sources.extend(product.attributes.diet_type)
        sources.extend(f"{item.name} {item.value}" for item in product.attributes.listed)
    for variation in product.variations:
        sources.extend(variation.attributes.values())
        sources.append(variation.label)
    terms: list[str] = []
    seen: set[str] = set()
    for source in sources:
        for term in _significant_terms(source):
            if term in seen:
                continue
            seen.add(term)
            terms.append(term)
    return [term for term in terms if term not in blob]


def _score_description_a_plus(product: Product) -> _SectionOutcome:
    description = (product.description or "").strip()
    char_count = len(description)
    a_plus_state = _a_plus_presence(product)
    brand_state = _brand_story_state(product)
    a_plus_text = _a_plus_text(product)
    a_plus_images = _a_plus_image_count(product)
    findings: list[Finding] = []
    notes: list[str] = []
    metrics = {
        "description_present": bool(description),
        "description_character_count": char_count,
        "a_plus_presence": a_plus_state.value,
        "brand_story_presence": brand_state.value,
        "a_plus_text_characters": len(a_plus_text),
        "a_plus_image_count": a_plus_images,
        "a_plus_quality_unknown": True,
    }

    if not description and a_plus_state in {EvidenceState.REPORTED_ABSENT, EvidenceState.UNKNOWN} and not a_plus_text:
        score = 18 if a_plus_state == EvidenceState.UNKNOWN else 12
        findings.append(_finding(FindingSeverity.HIGH, "description_a_plus", "DESCRIPTION_MISSING", "No product description was provided."))
        notes.append("Standard description is missing.")
        if a_plus_state == EvidenceState.REPORTED_ABSENT:
            findings.append(_finding(FindingSeverity.MEDIUM, "description_a_plus", "A_PLUS_NOT_PRESENT", "A+ Content was reported as not present."))
        elif a_plus_state == EvidenceState.UNKNOWN:
            findings.append(_finding(FindingSeverity.INFO, "description_a_plus", "A_PLUS_UNKNOWN", "A+ Content was not included in the provider payload, so presence on Amazon is unknown."))
        return _outcome("description_a_plus", score, metrics, notes, findings)

    score = 100.0
    substantial_a_plus = len(a_plus_text) >= 120 or a_plus_images >= 1
    if not description:
        if substantial_a_plus or a_plus_state == EvidenceState.OBSERVED:
            score = 72
            findings.append(_finding(FindingSeverity.LOW, "description_a_plus", "DESCRIPTION_MISSING", "No standard description was observed; enhanced content is present."))
            notes.append("Standard description is missing, but A+/Brand Story evidence exists.")
        else:
            score = 20
            findings.append(_finding(FindingSeverity.HIGH, "description_a_plus", "DESCRIPTION_MISSING", "No product description was provided."))
    elif char_count < 80:
        if substantial_a_plus:
            score = 78
            findings.append(_finding(FindingSeverity.INFO, "description_a_plus", "DESCRIPTION_THIN", "The standard description is short; enhanced content is present."))
            notes.append("Thin description was not heavily penalized because A+ text or media is present.")
        else:
            score = 48
            findings.append(_finding(FindingSeverity.MEDIUM, "description_a_plus", "DESCRIPTION_THIN", "The standard description is unusually short."))
            notes.append("Description is thin. Length is not treated as quality by itself.")
    elif char_count < 200:
        score = 78
        notes.append("A standard description is present.")
    else:
        score = 86
        notes.append("A substantial standard description is present. Length is not a quality rating.")

    if a_plus_state == EvidenceState.OBSERVED:
        findings.append(_finding(FindingSeverity.INFO, "description_a_plus", "A_PLUS_PRESENT", "A+ Content is present on the product payload. Presence is not quality."))
        if a_plus_text:
            score += 6
            findings.append(_finding(FindingSeverity.INFO, "description_a_plus", "A_PLUS_TEXT_AVAILABLE", "A+ text was observed in the product payload."))
        else:
            score += 3
            notes.append("A+ is flagged present; body text was not in this payload.")
        if a_plus_images:
            score += 4
            findings.append(_finding(FindingSeverity.INFO, "description_a_plus", "A_PLUS_MEDIA_PRESENT", "A+ images were observed."))
        if brand_state == EvidenceState.OBSERVED:
            score += 4
            findings.append(_finding(FindingSeverity.INFO, "description_a_plus", "BRAND_STORY_PRESENT", "Brand Story evidence was observed."))
    elif a_plus_state == EvidenceState.REPORTED_ABSENT:
        if char_count >= 80:
            score -= 2
            findings.append(_finding(FindingSeverity.INFO, "description_a_plus", "A_PLUS_NOT_PRESENT", "A+ Content was reported as not present. This is an opportunity, not a marketplace outcome."))
        else:
            score -= 6
            findings.append(_finding(FindingSeverity.LOW, "description_a_plus", "A_PLUS_NOT_PRESENT", "A+ Content was reported as not present."))
    else:
        findings.append(_finding(FindingSeverity.INFO, "description_a_plus", "A_PLUS_UNKNOWN", "A+ Content was not included in the provider payload, so presence on Amazon is unknown."))

    return _outcome("description_a_plus", _clamp(score), metrics, notes, findings)


def _score_media(product: Product) -> _SectionOutcome:
    images = [image for image in product.images if image.url.strip()]
    urls = [image.url.strip() for image in images]
    unique = set(urls)
    duplicate_count = len(urls) - len(unique)
    has_main = any(image.is_main for image in images) or bool(images)
    video_objects = bool(product.videos)
    video_count = product.videos_count
    a_plus_images = _a_plus_image_count(product)
    brand_media = _brand_story_media(product)
    dimensioned = sum(1 for image in images if image.width and image.height)
    findings: list[Finding] = []
    notes: list[str] = []
    metrics = {
        "image_count": len(urls),
        "duplicate_url_count": duplicate_count,
        "has_main_image": has_main,
        "video_object_count": len(product.videos),
        "videos_count": video_count,
        "a_plus_image_count": a_plus_images,
        "brand_story_media": brand_media,
        "images_with_dimensions": dimensioned,
        "image_quality_not_evaluated": True,
    }

    if not urls:
        score = 16
        findings.append(_finding(FindingSeverity.HIGH, "media_coverage", "MAIN_IMAGE_MISSING", "No product images were observed."))
        notes.append("No gallery images were observed. Image quality was not evaluated.")
    elif not has_main:
        score = 28
        findings.append(_finding(FindingSeverity.MEDIUM, "media_coverage", "MAIN_IMAGE_MISSING", "A main image was not identified."))
        notes.append("Gallery URLs exist but a main image was not marked.")
    elif len(urls) == 1:
        score = 42
        findings.append(_finding(FindingSeverity.MEDIUM, "media_coverage", "LIMITED_GALLERY", "Only 1 product image was observed."))
        notes.append("Image count is coverage only, not photography quality.")
    elif len(urls) <= 3:
        score = 58
        findings.append(_finding(FindingSeverity.LOW, "media_coverage", "LIMITED_GALLERY", f"Only {len(urls)} product images were observed."))
        notes.append("Gallery coverage is limited. Quality was not evaluated.")
    elif len(urls) <= 6:
        score = 72
        notes.append("Several gallery images are present. Count is not quality.")
    else:
        score = 78
        notes.append("A larger gallery was observed. Count is not treated as excellent photography.")

    if duplicate_count:
        score -= 12
        findings.append(_finding(FindingSeverity.MEDIUM, "media_coverage", "DUPLICATE_MEDIA", "One or more image URLs are duplicated."))

    if video_objects:
        score += 8
        findings.append(_finding(FindingSeverity.INFO, "media_coverage", "VIDEO_PRESENT", "Product video objects were observed."))
    elif video_count is not None and video_count > 0:
        score += 6
        findings.append(
            _finding(
                FindingSeverity.INFO,
                "media_coverage",
                "VIDEO_REPORTED_DETAILS_UNAVAILABLE",
                "The provider reported videos_count > 0, but detailed video objects were not returned.",
            )
        )
        notes.append("Video evidence is reported; detailed objects are unavailable.")
    elif video_count == 0:
        notes.append("Provider reported videos_count=0.")
    else:
        notes.append("Video presence is unknown; missing video objects were not treated as a listing defect.")

    if a_plus_images:
        score += 5
        findings.append(_finding(FindingSeverity.INFO, "media_coverage", "A_PLUS_MEDIA_PRESENT", "A+ images were observed separately from the gallery."))
    if brand_media:
        score += 4
        findings.append(_finding(FindingSeverity.INFO, "media_coverage", "BRAND_STORY_MEDIA_PRESENT", "Brand Story media was observed."))
    if urls and dimensioned == 0:
        findings.append(_finding(FindingSeverity.INFO, "media_coverage", "MEDIA_DIMENSIONS_UNKNOWN", "Explicit image width/height were not provided. Dimensions were not inferred from URLs."))

    return _outcome("media_coverage", _clamp(score), metrics, notes, findings)


def _score_content_structure(product: Product) -> _SectionOutcome:
    title = product.title.strip()
    bullets = [item.strip() for item in product.bullet_points if item.strip()]
    description = (product.description or "").strip()
    a_plus_text = _a_plus_text(product)
    combined = " ".join([title, *bullets, description, a_plus_text])
    findings: list[Finding] = []
    notes: list[str] = []
    copy_fields = [field for field in (title, description, a_plus_text) if field]
    copy_fields.extend(bullets)

    metrics: dict[str, object] = {
        "fields_with_copy": len(copy_fields),
        "cross_field_duplicate": False,
        "spec_gaps_in_copy": [],
    }

    if not copy_fields:
        findings.append(_finding(FindingSeverity.HIGH, "content_structure", "DESCRIPTION_MISSING", "No seller-facing copy was observed."))
        return _outcome("content_structure", 20, metrics, ["No seller-facing copy was available."], findings)

    score = 100.0
    if _cross_field_duplicate(title, bullets, description):
        score -= 14
        metrics["cross_field_duplicate"] = True
        findings.append(_finding(FindingSeverity.LOW, "content_structure", "STRUCTURE_DUPLICATE_COPY", "The same wording is repeated across title, bullets, or description."))

    caps_fields = 0
    punct_fields = 0
    for field in copy_fields:
        tokens = [token for token in field.split() if any(char.isalpha() for char in token)]
        caps = [token for token in tokens if token.isupper() and len(token) > 1]
        if tokens and len(caps) / len(tokens) >= 0.5:
            caps_fields += 1
        if field.count("!") + field.count("?") > 3:
            punct_fields += 1
    if caps_fields >= 2:
        score -= 8
        notes.append("Heavy capitalization appears in multiple content fields.")
    if punct_fields >= 2:
        score -= 6
        notes.append("Heavy punctuation appears in multiple content fields.")

    if bullets and sum(len(item) for item in bullets) / len(bullets) < 25:
        score -= 8
        notes.append("Bullets are highly fragmented.")

    spec_gaps = _specification_gaps(product, combined.lower())
    metrics["spec_gaps_in_copy"] = spec_gaps
    if spec_gaps:
        score -= min(18, 5 * len(spec_gaps))
        findings.append(
            _finding(
                FindingSeverity.MEDIUM,
                "content_structure",
                "STRUCTURE_SPEC_GAP",
                f"{len(spec_gaps)} structured specification(s) are not represented in seller-facing copy.",
            )
        )

    term_counts = Counter(_significant_terms(combined))
    heavy = [term for term, count in term_counts.items() if count >= 8]
    if heavy:
        score -= 10
        findings.append(_finding(FindingSeverity.LOW, "content_structure", "STRUCTURE_REPETITION", "The listing repeats the same terms across content fields."))

    notes.append("Content structure is a consistency check, not grammar or conversion analysis.")
    return _outcome("content_structure", _clamp(score), metrics, notes, findings)


def _cross_field_duplicate(title: str, bullets: list[str], description: str) -> bool:
    title_norm = re.sub(r"\s+", " ", title.lower()).strip()
    desc_norm = re.sub(r"\s+", " ", description.lower()).strip()
    if title_norm and desc_norm and (title_norm == desc_norm or title_norm in desc_norm and len(title_norm) > 40):
        return True
    for bullet in bullets:
        bullet_norm = re.sub(r"\s+", " ", bullet.lower()).strip()
        if title_norm and bullet_norm and title_norm == bullet_norm:
            return True
        if desc_norm and bullet_norm and bullet_norm == desc_norm:
            return True
    return False


def _a_plus_presence(product: Product) -> EvidenceState:
    payload = product.a_plus
    if payload is None:
        return EvidenceState.UNKNOWN
    if payload.has_a_plus_content is True:
        return EvidenceState.OBSERVED
    if payload.has_a_plus_content is False:
        return EvidenceState.REPORTED_ABSENT
    if payload.body_text or payload.images or payload.brand_story or payload.company_description:
        return EvidenceState.OBSERVED
    return EvidenceState.UNKNOWN


def _brand_story_state(product: Product) -> EvidenceState:
    payload = product.a_plus
    if payload is None:
        return EvidenceState.UNKNOWN
    if payload.has_brand_story is True:
        return EvidenceState.OBSERVED
    if payload.has_brand_story is False:
        return EvidenceState.REPORTED_ABSENT
    if payload.brand_story and (
        payload.brand_story.hero_image
        or payload.brand_story.images
        or payload.brand_story.description
        or payload.brand_story.brand_logo
    ):
        return EvidenceState.OBSERVED
    return EvidenceState.UNKNOWN


def _a_plus_text(product: Product) -> str:
    payload = product.a_plus
    if payload is None:
        return ""
    parts = [payload.body_text or "", payload.company_description or ""]
    if payload.brand_story and payload.brand_story.description:
        parts.append(payload.brand_story.description)
    return " ".join(part.strip() for part in parts if part and part.strip())


def _a_plus_image_count(product: Product) -> int:
    payload = product.a_plus
    if payload is None:
        return 0
    return len(payload.images)


def _brand_story_media(product: Product) -> bool:
    payload = product.a_plus
    if payload is None or payload.brand_story is None:
        return False
    story = payload.brand_story
    return bool(story.hero_image or story.brand_logo or story.images)


def _market_signals(product: Product) -> MarketSignals:
    return MarketSignals(
        rating=product.rating,
        review_count=product.review_count,
        price=product.price,
        availability=product.availability,
        availability_type=product.availability_type,
        is_sold_by_amazon=product.is_sold_by_amazon,
        seller=product.seller,
        bsr_ranks=list(product.bsr_ranks) or ([product.bsr] if product.bsr else []),
        recent_sales_text=product.recent_sales_text,
        rating_breakdown=product.rating_breakdown,
    )


def _coverage_info_findings(product: Product) -> list[Finding]:
    findings: list[Finding] = []
    if product.is_sold_by_amazon is True and product.seller is None:
        findings.append(
            _finding(
                FindingSeverity.INFO,
                "data_coverage",
                "AMAZON_SOLD_NO_THIRD_PARTY_SELLER",
                "This listing is sold by Amazon. A missing third-party seller object is not treated as missing seller information.",
            )
        )
    return findings


def _data_coverage(product: Product) -> DataCoverage:
    core = _group(
        "core_listing_content",
        [
            _field("title", EvidenceState.OBSERVED if not _blank(product.title) else EvidenceState.REPORTED_ABSENT),
            _field("bullets", EvidenceState.OBSERVED if any(item.strip() for item in product.bullet_points) else EvidenceState.REPORTED_ABSENT),
            _field(
                "description",
                EvidenceState.OBSERVED if not _blank(product.description) else EvidenceState.REPORTED_ABSENT,
            ),
        ],
    )
    video_state = _video_evidence_state(product)
    media = _group(
        "media",
        [
            _field("images", EvidenceState.OBSERVED if product.images else EvidenceState.REPORTED_ABSENT),
            _field(
                "video",
                video_state,
                note="videos_count without videos[] is reported presence, not missing video.",
            ),
        ],
    )
    a_plus_state = _a_plus_presence(product)
    enhanced = _group(
        "enhanced_content",
        [
            _field("a_plus", a_plus_state, note="Unknown means the provider payload omitted A+; that is not confirmed absence."),
            _field("brand_story", _brand_story_state(product)),
            _field(
                "specifications",
                EvidenceState.OBSERVED if product.specifications else EvidenceState.UNKNOWN,
                note="Omitted specifications are unknown, not confirmed empty.",
            ),
        ],
    )
    category = _group(
        "category_context",
        [
            _field(
                "category",
                EvidenceState.OBSERVED if (product.category or product.category_path) else EvidenceState.UNKNOWN,
            ),
            _field(
                "bsr_ranks",
                EvidenceState.OBSERVED if (product.bsr_ranks or product.bsr) else EvidenceState.UNKNOWN,
            ),
        ],
    )
    seller_state, seller_note = _seller_coverage(product)
    market = _group(
        "market_signals",
        [
            _field("rating", EvidenceState.OBSERVED if product.rating is not None else EvidenceState.UNKNOWN),
            _field("review_count", EvidenceState.OBSERVED if product.review_count is not None else EvidenceState.UNKNOWN),
            _field("price", EvidenceState.OBSERVED if product.price is not None else EvidenceState.UNKNOWN),
            _field(
                "availability",
                EvidenceState.OBSERVED if (product.availability or product.availability_type) else EvidenceState.UNKNOWN,
            ),
            _field("seller", seller_state, note=seller_note),
        ],
    )
    groups = [core, media, enhanced, category, market]
    total_available = sum(group.available for group in groups)
    total_expected = sum(group.expected for group in groups)
    overall = _clamp(100 * total_available / total_expected) if total_expected else 0
    return DataCoverage(
        overall_percentage=overall,
        core_listing_content=core,
        media=media,
        enhanced_content=enhanced,
        category_context=category,
        market_signals=market,
    )


def _video_evidence_state(product: Product) -> EvidenceState:
    if product.videos:
        return EvidenceState.OBSERVED
    if product.videos_count is not None and product.videos_count > 0:
        return EvidenceState.OBSERVED
    if product.videos_count == 0:
        return EvidenceState.REPORTED_ABSENT
    return EvidenceState.UNKNOWN


def _seller_coverage(product: Product) -> tuple[EvidenceState, str]:
    if product.seller is not None and product.seller.name.strip():
        return EvidenceState.OBSERVED, "Third-party seller object is present."
    if product.is_sold_by_amazon is True:
        return EvidenceState.OBSERVED, "Sold by Amazon; third-party seller may be absent by design."
    if product.is_sold_by_amazon is False and product.seller is None:
        return EvidenceState.UNKNOWN, "Not sold by Amazon and no third-party seller object was provided."
    return EvidenceState.UNKNOWN, "Seller identity is unknown."


def _field(name: str, state: EvidenceState, note: str | None = None) -> CoverageField:
    return CoverageField(
        name=name,
        evidence_state=state,
        available=state == EvidenceState.OBSERVED,
        note=note,
    )


def _group(name: str, fields: list[CoverageField]) -> CoverageGroup:
    expected = len(fields)
    available = sum(1 for item in fields if item.available)
    percentage = _clamp(100 * available / expected) if expected else 0
    notes = [item.note for item in fields if item.note]
    return CoverageGroup(
        name=name,
        available=available,
        expected=expected,
        percentage=percentage,
        status=_status(percentage),
        fields=fields,
        notes=notes,
    )


def _finding(severity: FindingSeverity, category: str, code: str, message: str) -> Finding:
    return Finding(severity=severity, category=category, code=code, message=message)


def _outcome(
    name: str,
    score: int,
    metrics: dict,
    notes: list[str],
    findings: list[Finding],
) -> _SectionOutcome:
    return _SectionOutcome(
        AnalysisSection(
            name=name,
            score=score,
            status=_status(score),
            metrics=metrics,
            findings=notes,
        ),
        findings,
    )
