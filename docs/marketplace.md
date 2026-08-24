# Marketplace identifiers

V1 uses Amazon marketplace **domain** identifiers, not country codes.

| Marketplace | Internal value |
|-------------|----------------|
| Amazon India | `amazon.in` |

Future marketplaces can be added as `amazon.com`, `amazon.co.uk`, and so on.

The Product model field `marketplace` always stores this domain form.
The API query parameter `marketplace` uses the same values.

Default: `amazon.in` (listing/UI default).

A connected Amazon seller’s marketplace participation may differ (for example Amazon.com). Canonical seller marketplace identity is Milestone **12B.2**. Do not assume `DEFAULT_MARKETPLACE` equals the authorized seller’s marketplaces.
