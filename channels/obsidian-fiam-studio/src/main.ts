import {
  App,
  ItemView,
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

type StudioTab = "timeline" | "inbox" | "desk" | "shelf" | "quick" | "coauthor";

interface FiamStudioSettings {
  inboxDir: string;
  deskDir: string;
  shelfDir: string;
  humanAuthor: string;
  aiAuthor: string;
  gitRemote: string;
}

const DEFAULT_SETTINGS: FiamStudioSettings = {
  inboxDir: "inbox",
  deskDir: "desk",
  shelfDir: "shelf",
  humanAuthor: "zephyr",
  aiAuthor: "ai",
  gitRemote: "origin",
};

interface GitCommit {
  sha: string;
  ts: number;
  author: string;
  subject: string;
  files: string[];
}

export default class FiamStudioPlugin extends Plugin {
  settings: FiamStudioSettings = DEFAULT_SETTINGS;

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
      id: "send-selection-to-inbox",
      name: "Send selection to inbox",
      editorCallback: async (editor) => {
        const selection = editor.getSelection();
        if (!selection.trim()) {
          new Notice("No selection");
          return;
        }
        await this.captureToInbox(selection, "obsidian-selection", this.settings.humanAuthor);
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

    this.addSettingTab(new FiamStudioSettingTab(this.app, this));
    await this.ensureStudioDirs();
  }

  onunload() {
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
    for (const dir of [this.settings.inboxDir, this.settings.deskDir, this.settings.shelfDir]) {
      await this.ensureFolder(dir);
    }
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

  async captureToInbox(text: string, source: string, author: string) {
    if (!text.trim()) return;
    await this.appendMarkdown(
      this.dailyPath(this.settings.inboxDir),
      this.formatCaptureBlock(text, source, author),
    );
    new Notice("Sent to Studio inbox");
    await this.gitCommitAll(`studio inbox: ${source}`, author);
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

  filesForSection(section: "inbox" | "desk" | "shelf") {
    const root = this.settings[`${section}Dir` as const];
    return this.app.vault
      .getMarkdownFiles()
      .filter((file) => file.path === `${root}.md` || file.path.startsWith(`${root}/`))
      .sort((a, b) => b.stat.mtime - a.stat.mtime);
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
    try {
      await this.git(["pull", "--rebase", this.settings.gitRemote, "main"]);
    } catch {
      await this.git(["pull", "--rebase", this.settings.gitRemote, "master"]);
    }
    await this.gitCommitAll(message, this.settings.humanAuthor);
    return await this.git(["push", this.settings.gitRemote]);
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
      inbox: "Inbox",
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
    if (this.activeTab === "inbox") this.renderFileList(panel, "inbox");
    if (this.activeTab === "desk") this.renderFileList(panel, "desk");
    if (this.activeTab === "shelf") this.renderFileList(panel, "shelf");
    if (this.activeTab === "quick") this.renderQuick(panel);
    if (this.activeTab === "coauthor") this.renderCoauthor(panel);
  }

  renderFileList(container: HTMLElement, section: "inbox" | "desk" | "shelf") {
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
      for (const commit of commits) {
        const row = container.createDiv({ cls: "fiam-studio-row" });
        row.createDiv({ text: commit.subject || commit.sha.slice(0, 7), cls: "fiam-studio-row-title" });
        row.createDiv({
          text: `${commit.sha.slice(0, 7)} · ${commit.author} · ${new Date(commit.ts * 1000).toLocaleString()}`,
          cls: "fiam-studio-row-meta",
        });
        if (commit.files.length) {
          row.createDiv({ text: commit.files.slice(0, 8).join(", "), cls: "fiam-studio-row-meta" });
        }
      }
    } catch (error) {
      container.createDiv({
        text: `Git timeline unavailable: ${String(error)}`,
        cls: "fiam-studio-status",
      });
    }
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

    this.textSetting("Inbox directory", "Default capture mailbox.", "inboxDir");
    this.textSetting("Desk directory", "Active drafts and quick notes.", "deskDir");
    this.textSetting("Shelf directory", "Reading material and archives.", "shelfDir");
    this.textSetting("Human author", "Author identity for user-originated writes.", "humanAuthor");
    this.textSetting("AI author", "Author identity for AI-originated writes.", "aiAuthor");
    this.textSetting("Git remote", "Remote used by Git sync.", "gitRemote");
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
}
