import { useState, useRef, useEffect } from "react";
import {
  ChevronRight,
  Plus,
  Paperclip,
  Send,
  Image,
  FileUp,
  Globe,
} from "lucide-react";

interface Message {
  id: string;
  sender: "user" | "assistant";
  content: string;
  thinking?: string;
  timestamp: number;
}

interface ChatSession {
  id: string;
  title: string;
  preview: string;
  messages: Message[];
  updatedAt: number;
}

const defaultSessions: ChatSession[] = [
  {
    id: "s-1",
    title: "测试环境确认",
    preview: "收到。这是在测试什么？",
    updatedAt: Date.now(),
    messages: [
      {
        id: "m-1",
        sender: "user",
        content: "收到。这是在测试什么？",
        timestamp: Date.now() - 60000,
      },
      {
        id: "m-2",
        sender: "assistant",
        content: "FORCE_CC_PHONE",
        timestamp: Date.now() - 55000,
      },
      {
        id: "m-3",
        sender: "user",
        content:
          "收到 Zephyr 的 Favilla app 消息，使用 Claude Code 后端，并强制使用手机上下文。看起来她可能想测试或确认某些特定的系统行为。",
        thinking:
          "我注意到消息有 FORCE_CC_PHONE 标记，这似乎是要确保我在 Claude Code 的手机环境下运行。我将正常回复。",
        timestamp: Date.now() - 50000,
      },
      {
        id: "m-4",
        sender: "assistant",
        content:
          "我注意到消息有 FORCE_CC_PHONE 标记，这似乎是要确保我在 Claude Code 的手机环境下运行。我将正常回复。",
        timestamp: Date.now() - 45000,
      },
      {
        id: "m-5",
        sender: "user",
        content:
          "你好！看起来你想确认我在特定的运行环境下。我在这里，准备好交流了。有什么特别想聊的吗？",
        timestamp: Date.now() - 30000,
      },
    ],
  },
  {
    id: "s-2",
    title: "代码审查讨论",
    preview: "让我们看看这个实现...",
    updatedAt: Date.now() - 3600000,
    messages: [],
  },
  {
    id: "s-3",
    title: "设计方案评审",
    preview: "我认为这个方向很有潜力",
    updatedAt: Date.now() - 7200000,
    messages: [],
  },
];

export function ChatView() {
  const [sessions, setSessions] = useState<ChatSession[]>(defaultSessions);
  const [activeSessionId, setActiveSessionId] = useState("s-1");
  const [input, setInput] = useState("");
  const [attachMenuOpen, setAttachMenuOpen] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const activeSession = sessions.find((s) => s.id === activeSessionId);
  const messages = activeSession?.messages || [];

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);

  const handleSend = () => {
    const text = input.trim();
    if (!text || !activeSession) return;

    const newMsg: Message = {
      id: `m-${Date.now()}`,
      sender: "user",
      content: text,
      timestamp: Date.now(),
    };

    setSessions((prev) =>
      prev.map((s) =>
        s.id === activeSessionId
          ? {
              ...s,
              messages: [...s.messages, newMsg],
              preview: text.slice(0, 40),
              updatedAt: Date.now(),
            }
          : s
      )
    );

    setInput("");
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
    setAttachMenuOpen(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleTextareaInput = () => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  };

  const handleNewChat = () => {
    const id = `s-${Date.now()}`;
    const newSession: ChatSession = {
      id,
      title: "新对话",
      preview: "",
      messages: [],
      updatedAt: Date.now(),
    };
    setSessions((prev) => [newSession, ...prev]);
    setActiveSessionId(id);
  };

  return (
    <div className="flex h-full">
      {/* Sidebar */}
      <aside className="w-[280px] bg-paper-warm border-r border-thread flex flex-col shrink-0">
        <div className="p-6 border-b border-thread">
          <button
            onClick={handleNewChat}
            className="w-full py-3 px-4 bg-accent text-white rounded-xl text-sm font-medium cursor-pointer hover:opacity-90 transition-opacity"
          >
            + 新对话
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-2">
          {sessions.map((session) => (
            <button
              key={session.id}
              onClick={() => setActiveSessionId(session.id)}
              className={`w-full text-left p-3 rounded-lg cursor-pointer transition-colors mb-1 ${
                session.id === activeSessionId
                  ? "bg-accent/15"
                  : "hover:bg-accent/10"
              }`}
            >
              <div className="text-sm text-ink truncate">{session.title}</div>
              <div className="text-xs text-lead truncate mt-1">
                {session.preview}
              </div>
            </button>
          ))}
        </div>
      </aside>

      {/* Chat area */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-8 flex flex-col gap-6">
          {messages.map((msg) => (
            <MessageGroup key={msg.id} message={msg} />
          ))}
          <div ref={messagesEndRef} />
        </div>

        {/* Input */}
        <div className="border-t border-thread p-6 bg-paper shrink-0">
          <div className="max-w-[800px] mx-auto relative">
            {/* Attachment menu */}
            {attachMenuOpen && (
              <div className="absolute bottom-full left-0 mb-2 bg-surface border border-thread rounded-xl shadow-lg min-w-[180px] z-10">
                <AttachItem
                  icon={<Image size={20} />}
                  label="插入图片"
                  onClick={() => setAttachMenuOpen(false)}
                />
                <AttachItem
                  icon={<FileUp size={20} />}
                  label="上传文件"
                  onClick={() => setAttachMenuOpen(false)}
                />
                <AttachItem
                  icon={<Globe size={20} />}
                  label="嵌入网页"
                  onClick={() => setAttachMenuOpen(false)}
                />
              </div>
            )}

            <div className="flex items-end gap-4 bg-surface border border-thread rounded-2xl p-4 focus-within:border-accent transition-colors">
              <div className="flex gap-2 pb-0.5">
                <button
                  onClick={() => setAttachMenuOpen(!attachMenuOpen)}
                  className="w-8 h-8 rounded-full flex items-center justify-center text-lead hover:bg-paper-warm hover:text-ink transition-colors cursor-pointer"
                >
                  <Plus size={20} />
                </button>
                <button className="w-8 h-8 rounded-full flex items-center justify-center text-lead hover:bg-paper-warm hover:text-ink transition-colors cursor-pointer">
                  <Paperclip size={20} />
                </button>
              </div>

              <textarea
                ref={textareaRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onInput={handleTextareaInput}
                onKeyDown={handleKeyDown}
                placeholder="输入消息..."
                rows={1}
                className="flex-1 border-none outline-none resize-none text-[15px] leading-relaxed text-ink bg-transparent max-h-[200px] min-h-[24px] placeholder:text-ghost"
              />

              <button
                onClick={handleSend}
                disabled={!input.trim()}
                className="w-9 h-9 rounded-full bg-accent text-white flex items-center justify-center shrink-0 cursor-pointer hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed transition-opacity"
              >
                <Send size={18} />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function MessageGroup({ message }: { message: Message }) {
  const [thinkingOpen, setThinkingOpen] = useState(false);
  const isUser = message.sender === "user";

  return (
    <div
      className={`flex flex-col gap-2 ${isUser ? "items-end" : "items-start"}`}
    >
      <div className="font-serif text-base italic text-lead">
        {isUser ? "Zephyr" : "Favilla"}
      </div>

      <div
        className={`py-3.5 px-[18px] rounded-2xl max-w-[65%] text-[15px] leading-relaxed ${
          isUser
            ? "bg-[#E8E3DD] text-ink rounded-br-[6px]"
            : "bg-surface text-ink border border-thread rounded-bl-[6px]"
        }`}
      >
        {message.content}
      </div>

      {message.thinking && (
        <div className="max-w-[65%]">
          <button
            onClick={() => setThinkingOpen(!thinkingOpen)}
            className="flex items-center gap-2 py-2 text-ghost text-[13px] cursor-pointer hover:text-lead transition-colors"
          >
            <ChevronRight
              size={14}
              className={`transition-transform ${thinkingOpen ? "rotate-90" : ""}`}
            />
            <span>{thinkingOpen ? "隐藏思考过程" : "显示思考过程"}</span>
          </button>

          {thinkingOpen && (
            <div className="bg-thinking border border-thread rounded-xl p-4 mt-2 text-sm leading-relaxed text-lead">
              {message.thinking}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function AttachItem({
  icon,
  label,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className="w-full flex items-center gap-3 px-4 py-3 text-sm text-ink hover:bg-paper-warm transition-colors cursor-pointer first:rounded-t-xl last:rounded-b-xl"
    >
      <span className="text-lead">{icon}</span>
      <span>{label}</span>
    </button>
  );
}
