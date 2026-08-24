# 15 — Known Issues and Tech Debt

Documented for Claude. Do not “fix” these by collapsing architecture.

1. **Live seller authorization** has not been fully validated end-to-end against a live seller grant. Code paths exist; Amazon-side configuration and seller consent still block a complete proof.

2. **Website OAuth Login URI** handling is incomplete. Do not claim website authorization is production-tested.

3. **Redirect URI / Login URI** on the Amazon Draft app must match real HTTPS routes exactly. localhost is not suitable for the live round-trip.

4. **Sandbox vs Draft/Production credentials** must stay split. Collapsing them will break either Test Connection or Connect Amazon.

5. **Connect Amazon defaults to SANDBOX** in local development. Live handshake requires a PRODUCTION connection row and production Sellers host.

6. **ASI default marketplace `amazon.in`** may not match the connected seller’s participation (e.g. Amazon.com). Canonical marketplace identity is 12B.2.

7. **`sellingPartnerId` may be absent** on `getMarketplaceParticipations`. Validation can still succeed from participation.

8. **No canonical seller-account / marketplace tables.**

9. **No seller business-data ingestion** (listings, orders, inventory, reports, finances).

10. **No Ads API.** Advertising math from seller-entered spend is not Ads API.

11. **Rainforest must stay.** Not a leftover to delete.

12. **Rainforest vs SP-API ASIN comparison** not done; needs 12B.2 + 12B.3.

13. **HTTP access logs** may still print callback query strings. Application logs are designed not to. Harden at server/proxy for production.

14. **Production SecretProvider** not implemented. `AMAZON_SECRET_BACKEND=production` fails closed. `DevelopmentSecretProvider` is the active backend.

15. **No end-user authentication.** Default organization only. RLS does not isolate users.

16. **Skill implementation paused.** Do not start Skills to “unblock” Amazon ingest.

17. **Copilot has no Amazon seller-data tools yet.** That is 12B.9, after stable seller data exists.
