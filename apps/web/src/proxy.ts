import { NextRequest, NextResponse } from "next/server";

import { resolvePublicRouting } from "@/lib/public-routing";

/**
 * pilot-deployment-ewise — thin wrapper around `resolvePublicRouting`'s
 * pure decision logic. See that module's own docstring for the full
 * design (one Next.js deployment, two custom domains, Cloudflare Access
 * scoped to the app subdomain only).
 */
export function proxy(request: NextRequest) {
  const host = request.headers.get("host") ?? "";
  const decision = resolvePublicRouting(host, request.nextUrl.pathname, process.env.NEXT_PUBLIC_MARKETING_HOSTS);

  if (decision.action === "next") {
    return NextResponse.next();
  }
  if (decision.action === "rewrite") {
    const url = request.nextUrl.clone();
    url.pathname = decision.pathname;
    return NextResponse.rewrite(url);
  }
  const url = request.nextUrl.clone();
  url.host = decision.host;
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|api).*)"],
};
