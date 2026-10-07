// Fails the build if the bundle references any external URL that a browser would fetch
// (CDNs, Google Fonts, …). InfoPoint must work with the network cable unplugged.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const ALLOWED = [
  "http://www.w3.org/", "https://www.w3.org/", "http://localhost", "https://reactjs.org/", "https://react.dev/",
  "https://github.com/", "https://developer.mozilla.org/",
];
const LOADERS = /(?:src|href)\s*=\s*["'](https?:\/\/[^"']+)|url\(\s*["']?(https?:\/\/[^)"']+)|@import\s+["'](https?:\/\/[^"']+)/g;

function* files(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) yield* files(p);
    else if (/\.(html|css|js)$/.test(p)) yield p;
  }
}

const problems = [];
for (const file of files("dist")) {
  const text = readFileSync(file, "utf8");
  for (const m of text.matchAll(LOADERS)) {
    const url = m[1] || m[2] || m[3];
    if (!ALLOWED.some((a) => url.startsWith(a))) problems.push(`${file}: ${url}`);
  }
}
if (problems.length) {
  console.error("External resources found in the build (not allowed offline):\n" + problems.join("\n"));
  process.exit(1);
}
console.log("offline check: no external resources referenced");
