import Link from "next/link";
import type { ReactNode } from "react";

/**
 * pilot-deployment-ewise — minimal shared chrome for the public
 * marketing/legal surface (`ewiseintelligence.com`), deliberately
 * distinct from `AppShell` (the private app's own navigation), since
 * these pages are served to visitors who have never authenticated and
 * are never behind Cloudflare Access.
 */
export function PublicPageShell({ children }: { children: ReactNode }) {
  return (
    <div className="mx-auto flex min-h-screen max-w-3xl flex-col px-6 py-10">
      <header className="mb-10 flex items-center justify-between">
        <Link href="/" className="text-lg font-semibold">
          EWise Amazon Seller Intelligence
        </Link>
        <nav className="flex gap-4 text-sm text-muted-foreground">
          <Link href="/privacy" className="hover:text-foreground">
            Privacy
          </Link>
          <Link href="/terms" className="hover:text-foreground">
            Terms
          </Link>
        </nav>
      </header>
      <main className="flex-1">{children}</main>
      <footer className="mt-16 border-t border-border pt-6 text-xs text-muted-foreground">
        <p>&copy; {new Date().getFullYear()} EWise. All rights reserved.</p>
      </footer>
    </div>
  );
}

export function DraftNotice() {
  return (
    <div className="mb-8 rounded-md border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-800">
      <strong>Draft — pending review.</strong> This page contains placeholder legal-entity and contact details
      marked <code>[PENDING]</code> below. It must not be treated as published/final until those are confirmed and
      replaced.
    </div>
  );
}
