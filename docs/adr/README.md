# Architecture Decision Records

ADRs are the frozen architecture contracts. Slice docs describe how a milestone was built. If they conflict, **stop** and reconcile explicitly; do not silently violate an ADR.

| ADR | Title | Status |
| --- | --- | --- |
| [0001](0001-advertising-intelligence-domain-boundary.md) | Advertising Intelligence domain boundary | Accepted |
| [0002](0002-amazon-data-provider-separation.md) | Rainforest / SP-API / Ads API stay separate | Accepted |
| [0003](0003-canonical-amazon-seller-data-model.md) | SP-API DTOs ≠ ASI canonical model | Accepted |
| [0004](0004-seller-data-provenance-and-source-precedence.md) | Provenance and source precedence | Accepted |
| [0005](0005-amazon-seller-identity-model.md) | Seller identity (account + marketplace + SKU) | Accepted |
| [0006](0006-amazon-connection-credential-boundary.md) | Connection metadata vs SecretProvider tokens | Accepted |

Current Amazon implementation is through **12B.1D**. Canonical identity tables named in ADR 0003/0005 start at **12B.2**.
