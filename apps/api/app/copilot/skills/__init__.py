"""12B.5A — Listings + Orders Copilot launch skills.

Five deterministic evidence services (this package) feed five narrow,
read-only Copilot tools (`app/copilot/tools/skills.py`). Every service
here wraps `AmazonListingsReadService`/`AmazonOrdersReadService` — it
never queries `AmazonSellerListing`/`AmazonSellerOrder`/`AmazonSeller
OrderItem` directly. See `contracts.py` for the shared evidence envelope
every skill returns, and `docs/AI_HANDOVER/
12B5A_LISTINGS_ORDERS_COPILOT_SKILLS.md` for the full design.
"""
