# Marketplace identifiers

V1 uses Amazon marketplace **domain** identifiers, not country codes.

| Marketplace | Internal value |
|-------------|----------------|
| Amazon India | `amazon.in` |

Future marketplaces can be added as `amazon.com`, `amazon.co.uk`, and so on.

The Product model field `marketplace` always stores this domain form.
The API query parameter `marketplace` uses the same values.

Default: `amazon.in`.
