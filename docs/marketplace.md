# Marketplace identifiers

V1 uses Amazon marketplace **domain** identifiers, not country codes.

| Marketplace | Internal value |
|-------------|----------------|
| Amazon India | `amazon.in` |
| Amazon United States | `amazon.com` |

The Product model field `marketplace` always stores this domain form.
The API query parameter `marketplace` uses the same values.

Default listing/UI marketplace is `DEFAULT_MARKETPLACE` (US seller testing uses `amazon.com`). Canonical seller marketplace identity remains Milestone **12B.2**. Do not assume listing default equals a connected seller’s participations.
