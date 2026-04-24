#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const srcArg = process.argv[2];
if (!srcArg) {
  console.error("Usage: node tools/ingest-stitch.mjs <stitch-export-zip-or-folder>");
  process.exit(1);
}

const sourceInput = path.resolve(process.cwd(), srcArg);
if (!fs.existsSync(sourceInput)) {
  console.error(`Source not found: ${sourceInput}`);
  process.exit(1);
}

const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "stitch-ingest-"));
let sourceDir = sourceInput;

if (sourceInput.toLowerCase().endsWith(".zip")) {
  if (process.platform === "win32") {
    const result = spawnSync(
      "powershell",
      ["-NoProfile", "-Command", `Expand-Archive -Path "${sourceInput}" -DestinationPath "${tempDir}" -Force`],
      { stdio: "inherit" },
    );
    if (result.status !== 0) process.exit(result.status ?? 1);
  } else {
    const result = spawnSync("unzip", ["-o", sourceInput, "-d", tempDir], { stdio: "inherit" });
    if (result.status !== 0) process.exit(result.status ?? 1);
  }
  sourceDir = tempDir;
}

const frontendRoot = path.join(repoRoot, "frontend-new", "src");
const assetTarget = path.join(frontendRoot, "assets", "stitch");
const styleTarget = path.join(frontendRoot, "styles");
fs.mkdirSync(assetTarget, { recursive: true });
fs.mkdirSync(styleTarget, { recursive: true });

const copied = [];
function walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      walk(full);
    } else if (/\.(png|jpg|jpeg|webp|gif|svg)$/i.test(entry.name)) {
      const cleanName = entry.name.toLowerCase().replace(/\s+/g, "-");
      const out = path.join(assetTarget, cleanName);
      fs.copyFileSync(full, out);
      copied.push({
        source: path.relative(repoRoot, full),
        output: path.relative(repoRoot, out),
      });
    }
  }
}
walk(sourceDir);

const tokenFile = path.join(styleTarget, "tokens.css");
if (!fs.existsSync(tokenFile)) {
  fs.writeFileSync(
    tokenFile,
    `:root {\n  --bg: #06090f;\n  --panel: #111a2b;\n  --border: #24314f;\n  --text: #eaf1ff;\n  --muted: #8ea2ce;\n  --accent: #35c9ff;\n}\n`,
    "utf8",
  );
}

const manifestPath = path.join(frontendRoot, "stitch-manifest.json");
fs.writeFileSync(
  manifestPath,
  JSON.stringify(
    {
      ingestedAt: new Date().toISOString(),
      source: path.relative(repoRoot, sourceInput),
      assets: copied,
      notes: "Re-run this script whenever Google Stitch export changes.",
    },
    null,
    2,
  ),
  "utf8",
);

console.log(`Stitch ingest completed: ${copied.length} assets copied.`);
