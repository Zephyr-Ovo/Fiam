import { copyFile, cp, mkdir, rm } from "node:fs/promises";
import { resolve } from "node:path";

const vault = process.env.FIAM_STUDIO_VAULT_DIR || "D:\\DevTools\\lib\\studio";
const target = resolve(vault, ".obsidian", "plugins", "fiam-studio");
const streamlineSource = resolve("..", "favilla", "app", "public", "icons", "streamline");
const streamlineTarget = resolve(target, "assets", "streamline");

await mkdir(target, { recursive: true });
await copyFile("main.js", resolve(target, "main.js"));
await copyFile("manifest.json", resolve(target, "manifest.json"));
await copyFile("styles.css", resolve(target, "styles.css"));
await rm(streamlineTarget, { recursive: true, force: true });
await mkdir(resolve(target, "assets"), { recursive: true });
await cp(streamlineSource, streamlineTarget, { recursive: true });

console.log(`Installed Fiam Studio plugin to ${target}`);
