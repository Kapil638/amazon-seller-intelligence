// Final review gate, PR #28 — proves check-legal-pages-ready.mjs's core
// detection logic against real fixture files (not mocks), including the
// exact failure mode found and fixed while building this script: an
// entrypoint guard comparing import.meta.url against a raw
// file://${process.argv[1]} string silently never matches when the repo
// path contains a space, so the check would appear to pass (exit 0)
// without ever actually running. That specific regression is covered
// separately by running the real CLI as a subprocess below.

import { copyFileSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { afterEach, describe, expect, it } from "vitest";

import { findPagesWithPendingMarkers } from "./check-legal-pages-ready.mjs";

// Not derived from import.meta.url — under Vitest's own module transform
// that is not guaranteed to be a real file:// URL. This test suite (like
// the script itself, and the npm prebuild step that invokes it) is only
// ever run with apps/web as the working directory.
const SCRIPT_PATH = path.join(process.cwd(), "scripts", "check-legal-pages-ready.mjs");

let tmpRoot;

afterEach(() => {
  if (tmpRoot) {
    rmSync(tmpRoot, { recursive: true, force: true });
    tmpRoot = undefined;
  }
});

function writeFixture(files) {
  tmpRoot = mkdtempSync(path.join(tmpdir(), "legal-pages-check-"));
  for (const [relativePath, contents] of Object.entries(files)) {
    const fullPath = path.join(tmpRoot, relativePath);
    mkdirSync(path.dirname(fullPath), { recursive: true });
    writeFileSync(fullPath, contents, "utf-8");
  }
  return tmpRoot;
}

describe("findPagesWithPendingMarkers", () => {
  it("flags a file still containing a [PENDING marker", () => {
    const root = writeFixture({
      "src/app/privacy/page.tsx": "export default function Page() { return <p>[PENDING: legal operator name]</p>; }",
      "src/app/terms/page.tsx": "export default function Page() { return <p>Ready content, no markers.</p>; }",
    });
    expect(findPagesWithPendingMarkers(root)).toEqual(["src/app/privacy/page.tsx"]);
  });

  it("flags both files when both still contain markers", () => {
    const root = writeFixture({
      "src/app/privacy/page.tsx": "[PENDING: address]",
      "src/app/terms/page.tsx": "[PENDING: governing law]",
    });
    expect(findPagesWithPendingMarkers(root)).toEqual(["src/app/privacy/page.tsx", "src/app/terms/page.tsx"]);
  });

  it("flags neither file once all markers are removed", () => {
    const root = writeFixture({
      "src/app/privacy/page.tsx": "Operated by Real Company Inc, 123 Main St. Contact: privacy@real.example.",
      "src/app/terms/page.tsx": "These are the real, final terms of service.",
    });
    expect(findPagesWithPendingMarkers(root)).toEqual([]);
  });

  it("is a real, exact substring match — not fooled by similar-looking text", () => {
    const root = writeFixture({
      "src/app/privacy/page.tsx": "This section is PENDING review internally, but has no bracketed placeholder.",
      "src/app/terms/page.tsx": "Nothing pending here at all.",
    });
    expect(findPagesWithPendingMarkers(root)).toEqual([]);
  });
});

describe("check-legal-pages-ready CLI (subprocess, proves the entrypoint guard actually runs)", () => {
  it("exits non-zero and blocks the build when the real repo's current pages still have [PENDING markers", () => {
    // Runs the actual, unmodified script against the actual, unmodified
    // repo files — this is the exact invocation `npm run build`'s own
    // `prebuild` step performs. As of this review, apps/web/src/app/
    // privacy/page.tsx and terms/page.tsx are still drafts (by design —
    // the operator has not yet supplied legal details), so this must
    // fail. Once those pages are finished, this specific assertion will
    // need to flip — that is the intended, correct behavior, not a test
    // to "fix" by loosening it.
    expect(() => execFileSync("node", [SCRIPT_PATH], { cwd: process.cwd(), stdio: "pipe" })).toThrow();
  });

  it("exits zero for a fixture tree with no pending markers", () => {
    // The script resolves its own target directory relative to its OWN
    // file location (`..` from scripts/), not the process cwd — correct
    // for the real prebuild invocation (always check the real repo's own
    // pages, regardless of where `npm run build` happens to be invoked
    // from), but it means exercising the "clean" CLI success path needs
    // a copy of the script placed inside the fixture tree itself, at the
    // same scripts/<name> relative position.
    const root = writeFixture({
      "src/app/privacy/page.tsx": "Real, finished privacy content.",
      "src/app/terms/page.tsx": "Real, finished terms content.",
    });
    const fixtureScriptDir = path.join(root, "scripts");
    mkdirSync(fixtureScriptDir, { recursive: true });
    const fixtureScriptPath = path.join(fixtureScriptDir, "check-legal-pages-ready.mjs");
    copyFileSync(SCRIPT_PATH, fixtureScriptPath);
    const output = execFileSync("node", [fixtureScriptPath], { stdio: "pipe" }).toString();
    expect(output).toContain("passed");
  });
});
