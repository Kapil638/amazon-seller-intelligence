"""Production SecretProvider backend storage: ciphertext-only table for
AES-256-GCM-encrypted Amazon secret material.

Revision ID: 0018_amazon_encrypted_secrets
Revises: 0017_inventory_heartbeat
Create Date: 2026-09-11

pilot-deployment-ewise, correction 1 — schema only, additive. See
`app/persistence/models.py`'s `AmazonEncryptedSecret` docstring and
`app/amazon/production_secrets.py` for the full design: `reference` is
the existing ASI secret reference string (non-secret, stable identity),
`ciphertext`/`nonce` are AES-256-GCM output for one `key_version`. No
column here ever holds plaintext token material, and this migration
inserts no data.

`downgrade()` drops the table outright. Encrypted secret rows have no
meaning without the application's own key material and AAD-bound
decryption logic, so — unlike a business-data table — there is nothing
a downgrade could meaningfully preserve; an operator downgrading past
this revision has already accepted that every seller would need to
reauthorize Amazon afterward.
"""

from alembic import op
import sqlalchemy as sa

revision = "0018_amazon_encrypted_secrets"
down_revision = "0017_inventory_heartbeat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "amazon_encrypted_secrets",
        sa.Column("reference", sa.String(128), primary_key=True),
        sa.Column("key_version", sa.String(32), nullable=False),
        sa.Column("nonce", sa.LargeBinary(12), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("amazon_encrypted_secrets")
