import { redirect } from "next/navigation";

/**
 * 12B.4D: `/seller-listings` moved to `/seller/listings` under the new
 * Seller Hub. Preserves every existing query parameter (selected
 * marketplace participation, filters, listing detail) so old bookmarks
 * and shared links never 404 and never lose their place.
 */
export default async function SellerListingsRedirectPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (Array.isArray(value)) {
      for (const v of value) query.append(key, v);
    } else if (value !== undefined) {
      query.append(key, value);
    }
  }
  const suffix = query.toString();
  redirect(suffix ? `/seller/listings?${suffix}` : "/seller/listings");
}
