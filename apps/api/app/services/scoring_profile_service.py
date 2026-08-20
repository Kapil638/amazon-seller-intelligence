from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.analytics.scoring_profiles import (
    STANDARD_PROFILE_ID,
    STANDARD_PROFILE_NAME,
    STANDARD_V2_WEIGHTS,
    calculate_weighted_listing_score,
    is_reserved_profile_name,
    is_standard_profile_id,
    section_scores_from_analysis,
)
from app.core.exceptions import (
    PersistenceNotConfiguredError,
    ScoringProfileConflictError,
    ScoringProfileImmutableError,
    ScoringProfileNotFoundError,
    ScoringProfileValidationError,
)
from app.models.listing_analysis_v2 import ListingAnalysisV2
from app.models.scoring_profile import (
    CustomScoreResult,
    ScoringProfileCreate,
    ScoringProfileResponse,
    ScoringProfileSnapshot,
    ScoringProfileUpdate,
    ScoringWeights,
)
from app.persistence.database import current_organization_id, persistence_enabled, session_scope
from app.persistence.models import ScoringProfile
from app.persistence.repositories import ScoringProfileRepository

STANDARD_DESCRIPTION = (
    "Immutable Listing Intelligence V2 weights. This is the universal benchmark. "
    "Custom profiles change only how section scores are aggregated."
)


class ScoringProfileService:
    def list_profiles(self, *, include_archived: bool = False) -> list[ScoringProfileResponse]:
        items = [self.standard_profile()]
        if not persistence_enabled():
            return items
        with session_scope() as session:
            rows = ScoringProfileRepository(session).list_for_org(
                current_organization_id(),
                include_archived=include_archived,
            )
            items.extend(_to_response(row) for row in rows)
        return items

    def get_profile(self, profile_id: str) -> ScoringProfileResponse:
        if is_standard_profile_id(profile_id):
            return self.standard_profile()
        row = self._require_row(profile_id)
        return _to_response(row)

    def create_profile(self, payload: ScoringProfileCreate) -> ScoringProfileResponse:
        self._require_persistence()
        name = _validated_name(payload.name)
        with session_scope() as session:
            repo = ScoringProfileRepository(session)
            org_id = current_organization_id()
            if repo.find_active_by_name(org_id, name) is not None:
                raise ScoringProfileConflictError(
                    "A scoring profile with this name already exists in the organization."
                )
            if payload.is_default:
                repo.clear_defaults(org_id)
            row = ScoringProfile(
                organization_id=org_id,
                name=name,
                description=payload.description,
                title_weight=payload.weights.title,
                bullets_weight=payload.weights.bullets,
                description_a_plus_weight=payload.weights.description_a_plus,
                media_weight=payload.weights.media,
                content_structure_weight=payload.weights.content_structure,
                is_default=payload.is_default,
            )
            repo.create(row)
            session.flush()
            return _to_response(row)

    def update_profile(self, profile_id: str, payload: ScoringProfileUpdate) -> ScoringProfileResponse:
        if is_standard_profile_id(profile_id):
            raise ScoringProfileImmutableError()
        self._require_persistence()
        with session_scope() as session:
            repo = ScoringProfileRepository(session)
            org_id = current_organization_id()
            row = repo.get(org_id, _as_uuid(profile_id))
            if row is None:
                raise ScoringProfileNotFoundError(profile_id)
            if row.archived_at is not None:
                raise ScoringProfileValidationError("Archived scoring profiles cannot be edited.")
            if payload.name is not None:
                name = _validated_name(payload.name)
                clash = repo.find_active_by_name(org_id, name)
                if clash is not None and clash.id != row.id:
                    raise ScoringProfileConflictError(
                        "A scoring profile with this name already exists in the organization."
                    )
                row.name = name
            if payload.description is not None:
                row.description = payload.description
            if payload.weights is not None:
                row.title_weight = payload.weights.title
                row.bullets_weight = payload.weights.bullets
                row.description_a_plus_weight = payload.weights.description_a_plus
                row.media_weight = payload.weights.media
                row.content_structure_weight = payload.weights.content_structure
            if payload.is_default is True:
                repo.clear_defaults(org_id, except_id=row.id)
                row.is_default = True
            elif payload.is_default is False:
                row.is_default = False
            session.flush()
            return _to_response(row)

    def archive_profile(self, profile_id: str) -> ScoringProfileResponse:
        if is_standard_profile_id(profile_id):
            raise ScoringProfileImmutableError("The Standard V2 scoring profile cannot be deleted.")
        self._require_persistence()
        with session_scope() as session:
            repo = ScoringProfileRepository(session)
            row = repo.get(current_organization_id(), _as_uuid(profile_id))
            if row is None:
                raise ScoringProfileNotFoundError(profile_id)
            row.is_default = False
            if row.archived_at is None:
                row.archived_at = datetime.now(UTC)
            session.flush()
            return _to_response(row)

    def standard_profile(self) -> ScoringProfileResponse:
        return ScoringProfileResponse(
            id=STANDARD_PROFILE_ID,
            name=STANDARD_PROFILE_NAME,
            description=STANDARD_DESCRIPTION,
            weights=STANDARD_V2_WEIGHTS,
            is_system=True,
            is_default=False,
            is_archived=False,
            editable=False,
            deletable=False,
        )

    def custom_score_for_analysis(
        self,
        analysis: ListingAnalysisV2,
        scoring_profile_id: str | None,
    ) -> CustomScoreResult | None:
        profile = self.resolve_for_analysis(scoring_profile_id)
        if profile is None or profile.is_system:
            return None
        score = calculate_weighted_listing_score(
            section_scores_from_analysis(analysis),
            profile.weights,
        )
        return CustomScoreResult(
            custom_listing_quality_score=score,
            profile=ScoringProfileSnapshot(
                profile_id=profile.id,
                profile_name=profile.name,
                type="custom",
                weights=profile.weights,
            ),
        )

    def resolve_for_analysis(self, scoring_profile_id: str | None) -> ScoringProfileResponse | None:
        if is_standard_profile_id(scoring_profile_id):
            return self.standard_profile()
        if scoring_profile_id:
            profile = self.get_profile(scoring_profile_id)
            if profile.is_archived:
                raise ScoringProfileValidationError(
                    "Archived scoring profiles cannot be used for new analysis."
                )
            return profile
        if not persistence_enabled():
            return None
        with session_scope() as session:
            row = ScoringProfileRepository(session).get_default(current_organization_id())
            if row is None:
                return None
            return _to_response(row)

    def _require_row(self, profile_id: str) -> ScoringProfile:
        self._require_persistence()
        with session_scope() as session:
            row = ScoringProfileRepository(session).get(
                current_organization_id(),
                _as_uuid(profile_id),
            )
            if row is None:
                raise ScoringProfileNotFoundError(profile_id)
            session.expunge(row)
            return row

    def _require_persistence(self) -> None:
        if not persistence_enabled():
            raise PersistenceNotConfiguredError("Scoring profiles require a configured database.")


def get_scoring_profile_service() -> ScoringProfileService:
    return ScoringProfileService()


def _to_response(row: ScoringProfile) -> ScoringProfileResponse:
    return ScoringProfileResponse(
        id=str(row.id),
        name=row.name,
        description=row.description,
        weights=_weights_from_row(row),
        is_system=False,
        is_default=bool(row.is_default),
        is_archived=row.archived_at is not None,
        editable=row.archived_at is None,
        deletable=row.archived_at is None,
        created_at=row.created_at,
        updated_at=row.updated_at,
        archived_at=row.archived_at,
    )


def _weights_from_row(row: ScoringProfile) -> ScoringWeights:
    return ScoringWeights(
        title=float(row.title_weight),
        bullets=float(row.bullets_weight),
        description_a_plus=float(row.description_a_plus_weight),
        media=float(row.media_weight),
        content_structure=float(row.content_structure_weight),
    )


def _validated_name(name: str) -> str:
    cleaned = " ".join(name.split())
    if not cleaned:
        raise ScoringProfileValidationError("Profile name is required.")
    if is_reserved_profile_name(cleaned):
        raise ScoringProfileValidationError("Standard V2 is a system profile and cannot be recreated.")
    return cleaned


def _as_uuid(profile_id: str) -> UUID:
    try:
        return UUID(profile_id)
    except ValueError as exc:
        raise ScoringProfileNotFoundError(profile_id) from exc
