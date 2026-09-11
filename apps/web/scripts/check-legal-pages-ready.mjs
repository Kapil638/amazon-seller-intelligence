#!/usr/bin/env node
// Final review gate, PR #28 — blocks `npm run build` (and therefore
// Railway's own Nixpacks build step, which runs `npm ci && npm run
// build` per docs/AI_HANDOVER/20_PILOT_DEPLOYMENT_EWISE.md §1) from
// succeeding while either public legal page still contains an
// operator-pending placeholder. Wired as the `prebuild` npm script —
// npm's own lifecycle convention runs this automatically before `build`,
// with no extra Railway/CI configuration needed.
//
// Scans the actual page source files directly (the same source of truth
// that gets rendered) rather than a separately-maintained "ready" flag —
// a flag could drift out of sync with the real content (flipped without
// every placeholder actually being replaced, or content finished but the
// flag forgotten); scanning the source itself cannot drift, because it
// IS the source.
//
// Governing rule this enforces (see src/app/privacy/page.tsx and
// src/app/terms/page.tsx's own module docstrings): do not publish
// incomplete or placeholder legal text. Every [PENDING marker is a
// legal-entity, contact, or jurisdiction-specific fact that must be
// confirmed by the operator before publishing.

import { readFileSync, realpathSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const PENDING_MARKER = "[PENDING";

export const LEGAL_PAGE_FILES = ["src/app/privacy/page.tsx", "src/app/terms/page.tsx"];

export function findPagesWithPendingMarkers(webRoot, files = LEGAL_PAGE_FILES) {
  const offending = [];
  for (const relativePath of files) {
    const contents = readFileSync(path.join(webRoot, relativePath), "utf-8");
    if (contents.includes(PENDING_MARKER)) {
      offending.push(relativePath);
    }
  }
  return offending;
}

function main() {
  const webRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
  const offending = findPagesWithPendingMarkers(webRoot);
  if (offending.length > 0) {
    console.error(
      "Build blocked: the following public legal pages still contain [PENDING] placeholders " +
        "and must not be deployed as-is:\n" +
        offending.map((file) => `  - ${file}`).join("\n") +
        "\nSupply the real legal operator/contact/jurisdiction details (see each file's own " +
        "module docstring) and remove every [PENDING marker before building for deployment.",
    );
    process.exitCode = 1;
    return;
  }
  console.log("Legal pages readiness check passed: no [PENDING markers found.");
}

// "Is this module the one node was invoked on" is surprisingly easy to
// get wrong, and a wrong answer here fails SILENTLY (exit 0, nothing
// printed, the entire check simply never ran) — the worst possible
// failure mode for a gate whose entire job is blocking an unsafe build.
// Two real, distinct bugs were found and fixed while building this:
// 1. Comparing `import.meta.url` against a raw `file://${process.argv[1]}`
//    string breaks the moment the path contains a character
//    import.meta.url percent-encodes but argv doesn't — this repo's own
//    path ("Amazon Seller Co-Pilot") has a space, which triggers exactly
//    that.
// 2. Comparing via fileURLToPath alone still breaks under a symlinked
//    directory (e.g. macOS's /tmp -> /private/tmp): import.meta.url is
//    resolved through the symlink by the module loader, while
//    process.argv[1] preserves whatever path the caller literally typed.
// realpathSync on both sides resolves symlinks identically before
// comparing, closing both gaps at once.
function isRunAsScript() {
  if (!process.argv[1]) return false;
  try {
    return realpathSync(fileURLToPath(import.meta.url)) === realpathSync(process.argv[1]);
  } catch {
    return false;
  }
}

if (isRunAsScript()) {
  main();
}
