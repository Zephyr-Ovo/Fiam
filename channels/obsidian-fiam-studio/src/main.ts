import {
  App,
  ItemView,
  MarkdownPostProcessorContext,
  MarkdownView,
  Modal,
  Notice,
  Plugin,
  PluginSettingTab,
  Setting,
  TFile,
  WorkspaceLeaf,
  normalizePath,
} from "obsidian";
import { execFile } from "child_process";
import { promisify } from "util";

const execFileAsync = promisify(execFile);
const VIEW_TYPE_FIAM_STUDIO = "fiam-studio-view";

type StudioTab = "timeline" | "desk" | "shelf" | "quick" | "coauthor";

const SIGNATURE_RE = /<!--\s*@(\S+)\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s*-->/g;

interface FiamStudioSettings {
  deskDir: string;
  shelfDir: string;
  humanAuthor: string;
  aiAuthor: string;
  gitRemote: string;
  studioEndpoint: string;
  ingestToken: string;
  autoSignature: boolean;
  showAuthorHighlight: boolean;
}

const DEFAULT_SETTINGS: FiamStudioSettings = {
  deskDir: "desk",
  shelfDir: "shelf",
  humanAuthor: "zephyr",
  aiAuthor: "ai",
  gitRemote: "origin",
  studioEndpoint: "",
  ingestToken: "",
  autoSignature: true,
  showAuthorHighlight: true,
};

interface GitCommit {
  sha: string;
  ts: number;
  author: string;
  subject: string;
  files: string[];
}

interface TimelineIcon {
  slug: string;
  alt: string;
  size: number;
}

export default class FiamStudioPlugin extends Plugin {
  settings: FiamStudioSettings = DEFAULT_SETTINGS;
  private _contentCache = new Map<string, string>();
  private _sigGuard = false;
  private _sigTimers = new Map<string, ReturnType<typeof setTimeout>>();
  private static readonly SIG_DEBOUNCE_MS = 3000;

  async onload() {
    await this.loadSettings();
    this.registerView(
      VIEW_TYPE_FIAM_STUDIO,
      (leaf) => new FiamStudioView(leaf, this),
    );

    this.addRibbonIcon("sparkles", "Open Fiam Studio", () => {
      void this.activateView();
    });

    this.addCommand({
      id: "open-fiam-studio",
      name: "Open Studio panel",
      callback: () => void this.activateView(),
    });

    this.addCommand({
      id: "quick-note",
      name: "Quick note to desk",
      callback: () => new TextModal(this.app, "Quick note", "Write to desk", async (text) => {
        await this.quickNote(text);
      }).open(),
    });

    this.addCommand({
      id: "send-selection-to-ai-inbox",
      name: "Send selection to private AI inbox",
      editorCallback: async (editor) => {
        const selection = editor.getSelection();
        if (!selection.trim()) {
          new Notice("No selection");
          return;
        }
        try {
          await this.sendToAiInbox(selection, "obsidian", this.settings.humanAuthor);
        } catch (error) {
          new Notice(`AI inbox send failed: ${String(error).slice(0, 160)}`);
        }
      },
    });

    this.addCommand({
      id: "append-ai-coauthor-block",
      name: "Append AI co-author block to current file",
      callback: () => new TextModal(this.app, "AI co-author block", "Append as AI", async (text) => {
        await this.appendCoauthorBlock(text, this.settings.aiAuthor);
      }).open(),
    });

    this.addCommand({
      id: "git-sync",
      name: "Git pull, commit, and push Studio vault",
      callback: async () => {
        const result = await this.gitSync(`studio sync: ${new Date().toISOString()}`);
        new Notice(result || "Studio git sync complete");
        this.refreshViews();
      },
    });

    this.addCommand({
      id: "git-commit",
      name: "Commit Studio vault changes",
      callback: async () => {
        const result = await this.gitCommitAll(
          `studio commit: ${new Date().toISOString()}`,
          this.settings.humanAuthor,
        );
        new Notice(result || "No Studio changes to commit");
        this.refreshViews();
      },
    });

    this.addCommand({
      id: "toggle-author-highlight",
      name: "Toggle paragraph author highlighting",
      callback: () => {
        this.settings.showAuthorHighlight = !this.settings.showAuthorHighlight;
        void this.saveSettings();
        new Notice(`Author highlighting: ${this.settings.showAuthorHighlight ? "on" : "off"}`);
      },
    });

    this.registerEvent(
      this.app.vault.on("modify", (file) => {
        if (file instanceof TFile && file.extension === "md") {
          void this._onFileModified(file);
        }
      }),
    );

    this.registerEvent(
      this.app.workspace.on("file-open", (file) => {
        if (file instanceof TFile && file.extension === "md") {
          void this._cacheFileContent(file);
        }
      }),
    );

    this.registerMarkdownPostProcessor((el, ctx) => {
      this._postProcessAuthorHighlight(el, ctx);
    });

    this.addSettingTab(new FiamStudioSettingTab(this.app, this));
    await this.ensureStudioDirs();
  }

  onunload() {
    for (const timer of this._sigTimers.values()) clearTimeout(timer);
    this._sigTimers.clear();
    this.app.workspace.detachLeavesOfType(VIEW_TYPE_FIAM_STUDIO);
  }

  async loadSettings() {
    this.settings = { ...DEFAULT_SETTINGS, ...(await this.loadData()) };
  }

  async saveSettings() {
    await this.saveData(this.settings);
  }

  async activateView(tab?: StudioTab) {
    let leaf: WorkspaceLeaf | null = this.app.workspace.getLeavesOfType(VIEW_TYPE_FIAM_STUDIO)[0] ?? null;
    if (!leaf) {
      leaf = this.app.workspace.getRightLeaf(false);
      if (!leaf) {
        new Notice("Unable to open Fiam Studio view");
        return;
      }
      await leaf.setViewState({ type: VIEW_TYPE_FIAM_STUDIO, active: true });
    }
    this.app.workspace.revealLeaf(leaf);
    const view = leaf.view;
    if (view instanceof FiamStudioView && tab) {
      view.setTab(tab);
    }
  }

  refreshViews() {
    for (const leaf of this.app.workspace.getLeavesOfType(VIEW_TYPE_FIAM_STUDIO)) {
      const view = leaf.view;
      if (view instanceof FiamStudioView) {
        void view.render();
      }
    }
  }

  async ensureStudioDirs() {
    for (const dir of [this.settings.deskDir, this.settings.shelfDir]) {
      await this.ensureFolder(dir);
    }
  }

  assetUrl(fileName: string) {
    const assetPath = normalizePath(`.obsidian/plugins/${this.manifest.id}/assets/${fileName}`);
    const adapter = this.app.vault.adapter as unknown as { getResourcePath?: (path: string) => string };
    return typeof adapter.getResourcePath === "function" ? adapter.getResourcePath(assetPath) : assetPath;
  }

  async ensureFolder(path: string) {
    const clean = normalizePath(path);
    if (!clean) return;
    if (!this.app.vault.getAbstractFileByPath(clean)) {
      await this.app.vault.createFolder(clean);
    }
  }

  dailyPath(dir: string) {
    const date = new Date().toISOString().slice(0, 10);
    return normalizePath(`${dir}/${date}.md`);
  }

  async appendMarkdown(path: string, text: string) {
    const clean = normalizePath(path);
    const parent = clean.split("/").slice(0, -1).join("/");
    if (parent) await this.ensureFolder(parent);
    const file = this.app.vault.getAbstractFileByPath(clean);
    if (file instanceof TFile) {
      const current = await this.app.vault.cachedRead(file);
      const prefix = current.trim() ? "\n\n" : "";
      await this.app.vault.modify(file, `${current}${prefix}${text}`);
    } else {
      await this.app.vault.create(clean, `${text}\n`);
    }
  }

  formatCaptureBlock(text: string, source: string, author: string) {
    const when = new Date();
    const hhmm = when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    const quoted = text
      .trim()
      .split(/\r?\n/)
      .map((line) => (line ? `> ${line}` : ">"))
      .join("\n");
    return [
      `## ${hhmm} · ${source} · ${author}`,
      "",
      quoted,
      "",
      `source: ${source}`,
      `author: ${author}`,
      "tags: #studio/inbox",
    ].join("\n");
  }

  formatCoauthorBlock(text: string, author: string) {
    const stamp = new Date().toISOString();
    return [
      "",
      `<!-- fiam:author=${author} ts=${stamp} -->`,
      text.trim(),
      `<!-- /fiam:author=${author} -->`,
    ].join("\n");
  }

  async sendToAiInbox(text: string, source: string, author: string) {
    if (!text.trim()) return;
    const result = await this.postStudio("/studio/share", {
      source,
      selection: text,
      agent: author,
      tags: ["obsidian"],
    });
    new Notice(`Sent to private AI inbox: ${String(result.rel_path || "ok")}`);
    this.refreshViews();
  }

  async quickNote(text: string) {
    if (!text.trim()) return;
    await this.appendMarkdown(
      this.dailyPath(this.settings.deskDir),
      this.formatCaptureBlock(text, "quicknote", this.settings.humanAuthor),
    );
    new Notice("Quick note saved");
    await this.gitCommitAll("studio quicknote", this.settings.humanAuthor);
    this.refreshViews();
  }

  async appendCoauthorBlock(text: string, author: string) {
    if (!text.trim()) return;
    const file = this.app.workspace.getActiveFile();
    if (!file) {
      new Notice("No active file");
      return;
    }
    const current = await this.app.vault.cachedRead(file);
    await this.app.vault.modify(file, `${current}${this.formatCoauthorBlock(text, author)}\n`);
    new Notice(`Appended ${author} co-author block`);
    await this.gitCommitAll(`studio coauthor: ${author} -> ${file.path}`, author);
    this.refreshViews();
  }

  filesForSection(section: "desk" | "shelf") {
    const root = this.settings[`${section}Dir` as const];
    return this.app.vault
      .getMarkdownFiles()
      .filter((file) => file.path === `${root}.md` || file.path.startsWith(`${root}/`))
      .sort((a, b) => b.stat.mtime - a.stat.mtime);
  }

  async postStudio(path: string, payload: Record<string, unknown>) {
    const endpoint = this.settings.studioEndpoint.trim().replace(/\/+$/, "");
    const token = this.settings.ingestToken.trim();
    if (!endpoint || !token) {
      throw new Error("Set Studio endpoint and FIAM_INGEST_TOKEN in Fiam Studio settings");
    }
    const response = await fetch(`${endpoint}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Fiam-Token": token,
      },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(String(data.error || `HTTP ${response.status}`));
    }
    return data as Record<string, unknown>;
  }

  async vaultBasePath(): Promise<string | null> {
    const adapter = this.app.vault.adapter as unknown as { getBasePath?: () => string };
    return typeof adapter.getBasePath === "function" ? adapter.getBasePath() : null;
  }

  async git(args: string[], opts: { author?: string } = {}) {
    const cwd = await this.vaultBasePath();
    if (!cwd) throw new Error("Git operations require Obsidian desktop file-system vault");
    const env = { ...process.env };
    if (opts.author) {
      env.GIT_AUTHOR_NAME = opts.author;
      env.GIT_AUTHOR_EMAIL = `${opts.author.replace(/[^A-Za-z0-9._-]+/g, "_")}@fiam.local`;
      env.GIT_COMMITTER_NAME = opts.author;
      env.GIT_COMMITTER_EMAIL = env.GIT_AUTHOR_EMAIL;
    }
    const { stdout, stderr } = await execFileAsync("git", args, {
      cwd,
      env,
      windowsHide: true,
      timeout: 30_000,
      maxBuffer: 1024 * 1024 * 8,
    });
    return `${stdout || ""}${stderr || ""}`.trim();
  }

  async gitCommitAll(message: string, author?: string) {
    try {
      await this.git(["rev-parse", "--is-inside-work-tree"]);
      await this.git(["add", "-A"], { author });
      const diffStatus = await this.git(["diff", "--cached", "--name-only"], { author });
      if (!diffStatus.trim()) return "";
      return await this.git(["commit", "-m", message], { author });
    } catch (error) {
      new Notice(`Git commit skipped: ${String(error).slice(0, 160)}`);
      return "";
    }
  }

  async gitSync(message: string) {
    await this.git(["rev-parse", "--is-inside-work-tree"]);
    const commitOutput = await this.gitCommitAll(message, this.settings.humanAuthor);
    const outputs = [commitOutput];
    try {
      outputs.push(await this.git(["pull", "--rebase", this.settings.gitRemote, "main"]));
    } catch {
      outputs.push(await this.git(["pull", "--rebase", this.settings.gitRemote, "master"]));
    }
    outputs.push(await this.git(["push", this.settings.gitRemote]));
    return outputs.filter(Boolean).join("\n");
  }

  async gitTimeline(limit = 80): Promise<GitCommit[]> {
    const raw = await this.git([
      "log",
      `--max-count=${limit}`,
      "--name-only",
      "--pretty=format:%H%x1f%ct%x1f%an%x1f%s",
    ]);
    const commits: GitCommit[] = [];
    let current: GitCommit | null = null;
    for (const line of raw.split(/\r?\n/)) {
      if (line.includes("\x1f")) {
        if (current) commits.push(current);
        const [sha, ts, author, subject] = line.split("\x1f");
        current = {
          sha,
          ts: Number(ts) || 0,
          author: author || "",
          subject: subject || "",
          files: [],
        };
      } else if (line.trim() && current) {
        current.files.push(line.trim());
      }
    }
    if (current) commits.push(current);
    return commits;
  }

  private async _cacheFileContent(file: TFile) {
    try {
      const content = await this.app.vault.cachedRead(file);
      this._contentCache.set(file.path, content);
    } catch {
      // File may have been deleted between event and read
    }
  }

  private async _onFileModified(file: TFile) {
    if (this._sigGuard) return;
    if (!this.settings.autoSignature) return;
    if (!this._isSignableFile(file)) return;

    // Debounce: reset timer on every keystroke, only stamp after pause
    const existing = this._sigTimers.get(file.path);
    if (existing) clearTimeout(existing);

    // Snapshot the "before" on first edit (cache miss means file just opened)
    if (!this._contentCache.has(file.path)) {
      // Too late to get the pre-edit state — skip this round
      try {
        this._contentCache.set(file.path, await this.app.vault.cachedRead(file));
      } catch { /* deleted */ }
      return;
    }

    this._sigTimers.set(
      file.path,
      setTimeout(() => void this._applySignatures(file), FiamStudioPlugin.SIG_DEBOUNCE_MS),
    );
  }

  private async _applySignatures(file: TFile) {
    this._sigTimers.delete(file.path);
    if (this._sigGuard) return;

    const oldContent = this._contentCache.get(file.path);
    let newContent: string;
    try {
      newContent = await this.app.vault.cachedRead(file);
    } catch {
      return;
    }
    if (oldContent === undefined || oldContent === newContent) {
      this._contentCache.set(file.path, newContent);
      return;
    }

    const signed = stampChangedParagraphs(
      oldContent,
      newContent,
      this.settings.humanAuthor,
    );
    if (signed === newContent) {
      this._contentCache.set(file.path, newContent);
      return;
    }

    this._sigGuard = true;
    try {
      await this.app.vault.modify(file, signed);
      this._contentCache.set(file.path, signed);
    } finally {
      this._sigGuard = false;
    }
  }

  private _isSignableFile(file: TFile): boolean {
    const path = file.path;
    if (path.startsWith("track/")) return false;
    if (path.startsWith(".obsidian/")) return false;
    return (
      path.startsWith(`${this.settings.deskDir}/`) ||
      path.startsWith(`${this.settings.shelfDir}/`)
    );
  }

  private _postProcessAuthorHighlight(
    el: HTMLElement,
    _ctx: MarkdownPostProcessorContext,
  ) {
    if (!this.settings.showAuthorHighlight) return;
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_COMMENT);
    let node: Comment | null;
    while ((node = walker.nextNode() as Comment | null)) {
      const text = node.nodeValue?.trim() ?? "";
      const match = text.match(/^@(\S+)\s+\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}$/);
      if (!match) continue;
      const author = match[1];
      const parent = node.parentElement;
      if (!parent) continue;
      const isAi = ["ai", "claude", "copilot", "codex", "cc"].some(
        (n) => author.toLowerCase() === n,
      );
      parent.classList.add(
        "fiam-studio-signed",
        isAi ? "fiam-studio-signed-ai" : "fiam-studio-signed-human",
      );
      parent.dataset.fiamAuthor = author;
    }
  }
}


function splitParagraphs(text: string): string[] {
  return text.split(/\n{2,}/);
}

function stripSignatures(paragraph: string): string {
  return paragraph.replace(SIGNATURE_RE, "").trimEnd();
}

function stampChangedParagraphs(
  oldText: string,
  newText: string,
  author: string,
): string {
  const oldParas = splitParagraphs(oldText);
  const newParas = splitParagraphs(newText);
  const now = new Date();
  const stamp = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")} ${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
  const tag = `<!-- @${author} ${stamp} -->`;

  const oldStripped = oldParas.map(stripSignatures);
  const result: string[] = [];
  for (let i = 0; i < newParas.length; i++) {
    const para = newParas[i];
    const stripped = stripSignatures(para);
    if (!stripped.trim()) {
      result.push(para);
      continue;
    }
    // Frontmatter block — never stamp
    if (i === 0 && stripped.startsWith("---")) {
      result.push(para);
      continue;
    }
    const matchIdx = oldStripped.indexOf(stripped);
    if (matchIdx >= 0) {
      // Paragraph unchanged (ignoring signatures) — keep as-is
      result.push(para);
      oldStripped[matchIdx] = "\x00"; // consume match
    } else {
      // New or changed paragraph — append signature
      const existingSigs = para.match(SIGNATURE_RE) ?? [];
      const alreadyStamped = existingSigs.some((sig) => {
        const m = sig.match(/<!--\s*@(\S+)/);
        return m && m[1] === author;
      });
      if (alreadyStamped) {
        result.push(para);
      } else {
        const lines = para.split("\n");
        const lastLine = lines[lines.length - 1];
        lines[lines.length - 1] = `${lastLine} ${tag}`;
        result.push(lines.join("\n"));
      }
    }
  }
  return result.join("\n\n");
}


class FiamStudioView extends ItemView {
  private activeTab: StudioTab = "timeline";

  constructor(leaf: WorkspaceLeaf, private plugin: FiamStudioPlugin) {
    super(leaf);
  }

  getViewType() {
    return VIEW_TYPE_FIAM_STUDIO;
  }

  getDisplayText() {
    return "Fiam Studio";
  }

  getIcon() {
    return "sparkles";
  }

  async onOpen() {
    await this.render();
  }

  setTab(tab: StudioTab) {
    this.activeTab = tab;
    void this.render();
  }

  async render() {
    const root = this.containerEl.children[1];
    root.empty();
    const wrap = root.createDiv({ cls: "fiam-studio-view" });
    wrap.createEl("h3", { text: "Fiam Studio" });
    const tabs = wrap.createDiv({ cls: "fiam-studio-tabs" });
    const labels: Record<StudioTab, string> = {
      timeline: "Timeline",
      desk: "Desk",
      shelf: "Shelf",
      quick: "Quick",
      coauthor: "Co-create",
    };
    (Object.keys(labels) as StudioTab[]).forEach((tab) => {
      const btn = tabs.createEl("button", {
        text: labels[tab],
        cls: `fiam-studio-tab${this.activeTab === tab ? " is-active" : ""}`,
      });
      btn.onclick = () => this.setTab(tab);
    });

    const panel = wrap.createDiv({ cls: "fiam-studio-panel" });
    if (this.activeTab === "timeline") await this.renderTimeline(panel);
    if (this.activeTab === "desk") this.renderFileList(panel, "desk");
    if (this.activeTab === "shelf") this.renderFileList(panel, "shelf");
    if (this.activeTab === "quick") this.renderQuick(panel);
    if (this.activeTab === "coauthor") this.renderCoauthor(panel);
  }

  renderFileList(container: HTMLElement, section: "desk" | "shelf") {
    const files = this.plugin.filesForSection(section);
    if (!files.length) {
      container.createDiv({ text: `No ${section} notes yet.`, cls: "fiam-studio-empty" });
      return;
    }
    for (const file of files) {
      const row = container.createDiv({ cls: "fiam-studio-row" });
      row.createDiv({ text: file.basename, cls: "fiam-studio-row-title" });
      row.createDiv({
        text: `${file.path} · ${new Date(file.stat.mtime).toLocaleString()}`,
        cls: "fiam-studio-row-meta",
      });
      const actions = row.createDiv({ cls: "fiam-studio-actions" });
      const open = actions.createEl("button", { text: "Open" });
      open.onclick = () => {
        void this.app.workspace.getLeaf(false).openFile(file);
      };
    }
  }

  renderQuick(container: HTMLElement) {
    const input = container.createEl("textarea", {
      cls: "fiam-studio-textarea",
      attr: { placeholder: "Write a quick note to desk..." },
    });
    const actions = container.createDiv({ cls: "fiam-studio-actions" });
    const save = actions.createEl("button", { text: "Save to desk" });
    save.onclick = async () => {
      await this.plugin.quickNote(input.value);
      input.value = "";
      this.setTab("desk");
    };
  }

  renderCoauthor(container: HTMLElement) {
    const status = container.createDiv({
      cls: "fiam-studio-status",
      text: "Append an AI-authored block to the active note and commit it with the AI author identity.",
    });
    status.toggleClass("fiam-studio-status", true);
    const input = container.createEl("textarea", {
      cls: "fiam-studio-textarea",
      attr: { placeholder: "AI-authored text..." },
    });
    const actions = container.createDiv({ cls: "fiam-studio-actions" });
    const append = actions.createEl("button", { text: "Append as AI" });
    append.onclick = async () => {
      await this.plugin.appendCoauthorBlock(input.value, this.plugin.settings.aiAuthor);
      input.value = "";
    };
  }

  async renderTimeline(container: HTMLElement) {
    const actions = container.createDiv({ cls: "fiam-studio-actions" });
    const commitChanges = actions.createEl("button", { text: "Commit changes" });
    commitChanges.onclick = async () => {
      const output = await this.plugin.gitCommitAll(
        `studio commit: ${new Date().toISOString()}`,
        this.plugin.settings.humanAuthor,
      );
      new Notice(output || "No Studio changes to commit");
      await this.render();
    };
    const sync = actions.createEl("button", { text: "Git sync" });
    sync.onclick = async () => {
      const output = await this.plugin.gitSync(`studio sync: ${new Date().toISOString()}`);
      new Notice(output || "Studio git sync complete");
      await this.render();
    };
    const refresh = actions.createEl("button", { text: "Refresh" });
    refresh.onclick = () => void this.render();

    try {
      const commits = await this.plugin.gitTimeline();
      if (!commits.length) {
        container.createDiv({ text: "No commits yet.", cls: "fiam-studio-empty" });
        return;
      }
      const timeline = container.createDiv({ cls: "fiam-studio-timeline" });
      timeline.createDiv({ cls: "fiam-studio-timeline-line" });
      for (const commit of commits) {
        const isAi = this.isAiCommit(commit);
        const icon = this.timelineIcon(commit, isAi);
        const item = timeline.createDiv({ cls: "fiam-studio-timeline-item" });
        const left = item.createDiv({ cls: `fiam-studio-timeline-side left${isAi ? " is-hidden" : ""}` });
        if (!isAi) this.renderTimelineText(left, "YOU", commit);

        const node = item.createDiv({ cls: `fiam-studio-timeline-node ${isAi ? "ai" : "user"}` });
        const glyph = node.createSpan({ cls: "fiam-studio-timeline-icon", attr: { "aria-label": icon.alt } });
        const iconUrl = this.plugin.assetUrl(`streamline/${icon.slug}.svg`);
        glyph.style.setProperty("mask-image", `url(${iconUrl})`);
        glyph.style.setProperty("mask-position", "center");
        glyph.style.setProperty("mask-repeat", "no-repeat");
        glyph.style.setProperty("mask-size", `${icon.size}px ${icon.size}px`);
        glyph.style.setProperty("-webkit-mask-image", `url(${iconUrl})`);
        glyph.style.setProperty("-webkit-mask-position", "center");
        glyph.style.setProperty("-webkit-mask-repeat", "no-repeat");
        glyph.style.setProperty("-webkit-mask-size", `${icon.size}px ${icon.size}px`);

        const right = item.createDiv({ cls: `fiam-studio-timeline-side right${isAi ? "" : " is-hidden"}` });
        if (isAi) this.renderTimelineText(right, "AI", commit);
      }
    } catch (error) {
      container.createDiv({
        text: `Git timeline unavailable: ${String(error)}`,
        cls: "fiam-studio-status",
      });
    }
  }

  private isAiCommit(commit: GitCommit) {
    const author = commit.author.toLowerCase();
    return ["ai", "claude", "copilot", "codex", "cc"].some((name) => author.includes(name));
  }

  private timelineIcon(commit: GitCommit, isAi: boolean): TimelineIcon {
    const subject = commit.subject.toLowerCase();
    const files = commit.files.join(" ").toLowerCase();
    const haystack = `${subject} ${files}`;
    if (/git|sync|commit|push|pull|merge|rebase/.test(haystack)) return { slug: "git", alt: "Version control", size: 14 };
    if (/build|test|verify|check|pass|done|todo|task|plan/.test(haystack)) return { slug: "clipboard-check", alt: "Checked work", size: 13.5 };
    if (/search|grep|find|query|lookup|scan/.test(haystack)) return { slug: "search", alt: "Search", size: 13.5 };
    if (/web|browser|url|http|https|fetch|site|page/.test(haystack)) return { slug: "browser", alt: "Web", size: 14 };
    if (/attach|attachment|image|photo|picture|pdf|upload|media/.test(haystack)) return { slug: "attachment", alt: "Attachment", size: 13.5 };
    if (/shelf|read|reader|book|epub|library/.test(haystack)) return { slug: "read-book", alt: "Reading", size: 14 };
    if (/folder|dir|tree|workspace/.test(haystack)) return { slug: "folder", alt: "Folder", size: 13.5 };
    if (/coauthor|co-author|desk|write|note|draft|quick|compose|create|new/.test(haystack)) return { slug: "write-paper", alt: isAi ? "AI writing" : "Writing", size: 13.5 };
    if (/edit|update|modify|patch|revise|fix/.test(haystack)) return { slug: "edit", alt: "Edit", size: 13 };
    if (/chat|message|reply|conversation/.test(haystack)) return { slug: "chat-message", alt: "Conversation", size: 13 };
    if (/think|reason|reflect/.test(haystack)) return { slug: "brain", alt: "Reasoning", size: 14 };
    return { slug: "file-text", alt: isAi ? "AI document change" : "Document change", size: 13 };
  }

  private renderTimelineText(container: HTMLElement, actor: "YOU" | "AI", commit: GitCommit) {
    const detailLines = [commit.sha, commit.author, ...commit.files.slice(0, 8)].filter(Boolean);
    if (detailLines.length) container.setAttribute("title", detailLines.join("\n"));
    container.createDiv({ text: actor, cls: "fiam-studio-timeline-label" });
    container.createDiv({ text: commit.subject || commit.sha.slice(0, 7), cls: "fiam-studio-timeline-title" });
    container.createDiv({
      text: new Date(commit.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
      cls: "fiam-studio-timeline-time",
    });
  }
}

class TextModal extends Modal {
  constructor(
    app: App,
    private title: string,
    private action: string,
    private onSubmit: (text: string) => Promise<void>,
  ) {
    super(app);
  }

  onOpen() {
    this.contentEl.empty();
    this.contentEl.createEl("h2", { text: this.title });
    const input = this.contentEl.createEl("textarea", {
      cls: "fiam-studio-textarea",
      attr: { placeholder: this.title },
    });
    const actions = this.contentEl.createDiv({ cls: "fiam-studio-actions" });
    const submit = actions.createEl("button", { text: this.action });
    submit.onclick = async () => {
      await this.onSubmit(input.value);
      this.close();
    };
    input.focus();
  }
}

class FiamStudioSettingTab extends PluginSettingTab {
  constructor(app: App, private plugin: FiamStudioPlugin) {
    super(app, plugin);
  }

  display() {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl("h2", { text: "Fiam Studio" });

    this.textSetting("Desk directory", "Active drafts and quick notes.", "deskDir");
    this.textSetting("Shelf directory", "Reading material and archives.", "shelfDir");
    this.textSetting("Human author", "Author identity for user-originated writes.", "humanAuthor");
    this.textSetting("AI author", "Author identity for AI-originated writes.", "aiAuthor");
    this.textSetting("Git remote", "Remote used by Git sync.", "gitRemote");
    this.textSetting("Studio endpoint", "Server base URL for private AI inbox sends.", "studioEndpoint");
    this.secretSetting("FIAM_INGEST_TOKEN", "Token used for private AI inbox sends.", "ingestToken");

    new Setting(this.containerEl)
      .setName("Auto paragraph signatures")
      .setDesc("Append author/timestamp signatures to changed paragraphs on save.")
      .addToggle((toggle) => {
        toggle.setValue(this.plugin.settings.autoSignature);
        toggle.onChange(async (value) => {
          this.plugin.settings.autoSignature = value;
          await this.plugin.saveSettings();
        });
      });

    new Setting(this.containerEl)
      .setName("Show author highlighting")
      .setDesc("Highlight paragraphs by author in reading view (left border + hover label).")
      .addToggle((toggle) => {
        toggle.setValue(this.plugin.settings.showAuthorHighlight);
        toggle.onChange(async (value) => {
          this.plugin.settings.showAuthorHighlight = value;
          await this.plugin.saveSettings();
        });
      });
  }

  private textSetting(
    name: string,
    desc: string,
    key: keyof FiamStudioSettings,
  ) {
    new Setting(this.containerEl)
      .setName(name)
      .setDesc(desc)
      .addText((text) => {
        text.setValue(String(this.plugin.settings[key] || ""));
        text.onChange(async (value) => {
          this.plugin.settings[key] = value.trim() as never;
          await this.plugin.saveSettings();
        });
      });
  }

  private secretSetting(
    name: string,
    desc: string,
    key: keyof FiamStudioSettings,
  ) {
    new Setting(this.containerEl)
      .setName(name)
      .setDesc(desc)
      .addText((text) => {
        text.inputEl.type = "password";
        text.setValue(String(this.plugin.settings[key] || ""));
        text.onChange(async (value) => {
          this.plugin.settings[key] = value.trim() as never;
          await this.plugin.saveSettings();
        });
      });
  }
}
