import { useState } from "react";
import { ChatView } from "./views/ChatView";
import {
  MessageSquare,
  Inbox,
  Settings,
  Sparkles,
  ExternalLink,
} from "lucide-react";

type View = "chat" | "studio" | "settings";

export default function App() {
  const [view, setView] = useState<View>("studio");

  return (
    <div className="h-screen w-screen flex flex-col overflow-hidden bg-paper">
      {/* Top bar */}
      <header className="h-12 bg-accent text-white flex items-center justify-between px-5 shrink-0">
        <div className="flex items-center gap-3">
          <span className="font-serif text-xl tracking-wide">Favilla</span>
          <span className="text-white/50 text-xs font-mono">desktop</span>
        </div>

        <nav className="flex items-center gap-1">
          <NavBtn
            icon={<Inbox size={18} />}
            label="Studio"
            active={view === "studio"}
            onClick={() => setView("studio")}
          />
          <NavBtn
            icon={<MessageSquare size={18} />}
            label="对话"
            active={view === "chat"}
            onClick={() => setView("chat")}
          />
          <NavBtn
            icon={<Settings size={18} />}
            label="设置"
            active={view === "settings"}
            onClick={() => setView("settings")}
          />
        </nav>

        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1.5 text-xs text-white/60">
            <Sparkles size={14} />
            <span>Fiam connected</span>
          </div>
        </div>
      </header>

      {/* Main content */}
      <main className="flex-1 overflow-hidden">
        {view === "chat" && <ChatView />}
        {view === "studio" && <StudioStatusView />}
        {view === "settings" && (
          <div className="flex items-center justify-center h-full text-ghost">
            设置页面待实现
          </div>
        )}
      </main>
    </div>
  );
}

function StudioStatusView() {
  return (
    <div className="h-full flex items-center justify-center bg-paper-warm px-8">
      <div className="max-w-[680px] w-full bg-surface border border-thread rounded-xl p-8 shadow-sm">
        <div className="flex items-center gap-3 text-ink">
          <Sparkles size={22} className="text-accent" />
          <div>
            <h1 className="font-serif text-2xl leading-tight">Fiam Studio lives in Obsidian</h1>
            <p className="text-sm text-lead mt-1">
              Atrium keeps browser capture and desktop capabilities. The full Studio surface is the Obsidian plugin inside the vault.
            </p>
          </div>
        </div>

        <div className="mt-6 grid gap-3 text-sm text-ink/80">
          <div className="rounded-lg border border-thread bg-paper px-4 py-3">
            Vault: <span className="font-mono">D:\DevTools\lib\studio</span>
          </div>
          <div className="rounded-lg border border-thread bg-paper px-4 py-3">
            Plugin: <span className="font-mono">.obsidian/plugins/fiam-studio</span>
          </div>
          <div className="rounded-lg border border-thread bg-paper px-4 py-3">
            Capture remains available from the Atrium browser extension via <span className="font-mono">/studio/share</span>.
          </div>
        </div>

        <div className="mt-6 flex items-center gap-2 text-xs text-lead">
          <ExternalLink size={14} />
          <span>Open the Studio vault in Obsidian to use Inbox, Desk, Shelf, Co-create, and Timeline.</span>
        </div>
      </div>
    </div>
  );
}

function NavBtn({
  icon,
  label,
  active,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm transition-colors cursor-pointer ${
        active
          ? "bg-white/20 text-white"
          : "text-white/60 hover:text-white hover:bg-white/10"
      }`}
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}
