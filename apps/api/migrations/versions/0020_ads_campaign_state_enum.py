"""Widen amazon_ads_campaigns.state to the officially documented
SponsoredProductsCampaign state enum.

Revision ID: 0020_ads_campaign_state_enum
Revises: 0019_amazon_ads_foundation
Create Date: 2026-09-15

Governing contract: docs/AI_HANDOVER/23_AMAZON_ADS_API_OFFICIAL_RESEARCH_AND_INGESTION_BLUEPRINT.md
Section 10.2 — the `SponsoredProductsCampaign` schema (from Amazon's own
Sponsored Products Version 3 OpenAPI spec) documents `state` as one of
seven values: ENABLED, PAUSED, ARCHIVED, PROPOSED, ENABLING,
USER_DELETED, OTHER. `0019_amazon_ads_foundation` shipped with a
three-value constraint (ENABLED/PAUSED/ARCHIVED) shared across all five
Sponsored Products entity tables — narrower than campaigns' own
documented contract.

This migration widens ONLY `amazon_ads_campaigns.state`. The blueprint's
own extracted schemas for the sibling entities (ad groups §10.3, product
ads §10.4, keywords §11.5, product targets §11.6) require a `state`
field but do not enumerate its permitted values the way the campaign
schema does — generalizing the campaign enum onto them would be exactly
the kind of unverified assumption this project has been burned by
before (see PR #32's campaign-media-type fix). Their constraints are
left unchanged (ENABLED/PAUSED/ARCHIVED) pending an equivalent
documented enum or independent live verification.

Additive and safe: existing rows already satisfy the old (narrower)
constraint, so they trivially satisfy the new (wider) one. No column
type change, no data migration, no other table touched.
"""

from alembic import op
import sqlalchemy as sa

revision = "0020_ads_campaign_state_enum"
down_revision = "0019_amazon_ads_foundation"
branch_labels = None
depends_on = None

_OLD_CAMPAIGN_STATE_CHECK = "state IN ('ENABLED', 'PAUSED', 'ARCHIVED')"
_NEW_CAMPAIGN_STATE_CHECK = (
    "state IN ('ENABLED', 'PAUSED', 'ARCHIVED', 'PROPOSED', 'ENABLING', 'USER_DELETED', 'OTHER')"
)
_CONSTRAINT_NAME = "ck_amazon_ads_campaigns_state"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT_NAME, "amazon_ads_campaigns", type_="check")
    op.create_check_constraint(_CONSTRAINT_NAME, "amazon_ads_campaigns", _NEW_CAMPAIGN_STATE_CHECK)


def downgrade() -> None:
    conn = op.get_bind()
    unrepresentable = conn.execute(
        sa.text(
            "SELECT count(*) FROM amazon_ads_campaigns "
            "WHERE state NOT IN ('ENABLED', 'PAUSED', 'ARCHIVED')"
        )
    ).scalar_one()
    if unrepresentable:
        raise RuntimeError(
            "Refusing to downgrade 0020: "
            f"{unrepresentable} amazon_ads_campaigns row(s) hold a state value "
            "(PROPOSED/ENABLING/USER_DELETED/OTHER) the pre-0020 three-value "
            "constraint cannot represent. Remove or migrate this data out-of-band "
            "before downgrading."
        )
    op.drop_constraint(_CONSTRAINT_NAME, "amazon_ads_campaigns", type_="check")
    op.create_check_constraint(_CONSTRAINT_NAME, "amazon_ads_campaigns", _OLD_CAMPAIGN_STATE_CHECK)
