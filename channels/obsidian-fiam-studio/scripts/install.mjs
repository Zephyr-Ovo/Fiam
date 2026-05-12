import { copyFile, mkdir } from "node:fs/promises";
import { resolve } from "node:path";

const vault = process.env.FIAM_STUDIO_VAULT_DIR || "D:\\DevTools\\lib\\studio";
const target = resolve(vault, ".obsidian", "plugins", "fiam-studio");

await mkdir(target, { recursive: true });
await copyFile("main.js", resolve(target, "main.js"));
await copyFile("manifest.json", resolve(target, "manifest.json"));
await copyFile("styles.css", resolve(target, "styles.css"));

console.log(`Installed Fiam Studio plugin to ${target}`);
