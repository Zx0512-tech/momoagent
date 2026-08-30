import { readdir, stat } from "node:fs/promises";
import { resolve } from "node:path";

const maxChunkBytes = 500_000;
const assetsDir = resolve("dist", "assets");
const files = (await readdir(assetsDir)).filter((name) => name.endsWith(".js"));
const chunks = await Promise.all(
  files.map(async (name) => ({ name, bytes: (await stat(resolve(assetsDir, name))).size }))
);
const oversized = chunks.filter((chunk) => chunk.bytes > maxChunkBytes);

if (oversized.length > 0) {
  for (const chunk of oversized) {
    console.error(`${chunk.name}: ${chunk.bytes} bytes exceeds ${maxChunkBytes}`);
  }
  process.exit(1);
}

const largest = chunks.sort((left, right) => right.bytes - left.bytes)[0];
console.log(`Bundle budget passed: ${largest.name} is ${largest.bytes} bytes`);
