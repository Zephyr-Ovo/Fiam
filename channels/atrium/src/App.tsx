import { useState } from "react";
import { ChatView } from "./views/ChatView";
import { EditorView } from "./views/EditorView";
import {
  MessageSquare,
  FileText,
  Settings,
  Sparkles,
} from "lucide-react";

type View = "chat" | "editor" | "settings";

export default function App() {
  const [view, setView] = useState<View>("editor");

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
            icon={<FileText size={18} />}
            label="笔记"
            active={view === "editor"}
            onClick={() => setView("editor")}
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
        {view === "editor" && <EditorView />}
        {view === "settings" && (
          <div className="flex items-center justify-center h-full text-ghost">
            设置页面待实现
          </div>
        )}
      </main>
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
