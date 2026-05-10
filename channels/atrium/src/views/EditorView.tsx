import { useState, useCallback, useRef, useEffect } from "react";
import {
  FileText,
  Folder,
  Plus,
  Trash2,
  ChevronRight,
  FolderPlus,
  FilePlus,
  Edit3,
  Sparkles,
  X,
  Bold,
  Italic,
  Strikethrough,
  Code,
  List,
  Quote,
  Heading1,
  Heading2,
  Heading3,
  Link,
  Minus,
} from "lucide-react";
import { useWorkspace, type WorkspaceFile, type TimelineEvent } from "../hooks/useWorkspace";
import { NoteEditor } from "../components/NoteEditor";

export function EditorView() {
  const {
    files,
    setFiles,
    activeFileId,
    setActiveFileId,
    activeFile,
    timeline,
    addTimelineEvent,
    updateFileContent,
    createFile,
    createFolder,
    deleteFile,
    moveFile,
  } = useWorkspace();

  const [newItemName, setNewItemName] = useState("");
  const [newItemType, setNewItemType] = useState<"file" | "folder" | null>(null);
  const [newItemParent, setNewItemParent] = useState<string | null>(null);

  // Debounced edit timeline entry
  const editTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const handleEditorChange = useCallback(
    (html: string) => {
      if (!activeFile) return;
      updateFileContent(activeFileId, html);

      clearTimeout(editTimerRef.current);
      editTimerRef.current = setTimeout(() => {
        addTimelineEvent(
          `编辑 ${activeFile.name}`,
          "user",
          activeFileId,
          activeFile.name
        );
      }, 3000);
    },
    [activeFileId, activeFile, updateFileContent, addTimelineEvent]
  );

  const handleCreateItem = () => {
    if (!newItemName.trim() || !newItemType) return;
    if (newItemType === "file") {
      createFile(newItemName.trim(), newItemParent);
    } else {
      createFolder(newItemName.trim(), newItemParent);
    }
    setNewItemName("");
    setNewItemType(null);
    setNewItemParent(null);
  };

  const startCreate = (type: "file" | "folder", parentId: string | null) => {
    setNewItemType(type);
    setNewItemParent(parentId);
    setNewItemName("");
  };

  return (
    <div className="flex h-full">
      {/* File tree sidebar */}
      <aside className="w-[240px] bg-paper-warm border-r border-thread flex flex-col shrink-0">
        <div className="h-10 px-4 flex items-center justify-between border-b border-thread">
          <span className="font-serif text-sm font-medium text-ink">
            工作区
          </span>
          <div className="flex gap-1">
            <button
              onClick={() => startCreate("file", null)}
              className="w-6 h-6 rounded flex items-center justify-center text-lead hover:text-ink hover:bg-thread/40 transition-colors cursor-pointer"
              title="新建文件"
            >
              <FilePlus size={14} />
            </button>
            <button
              onClick={() => startCreate("folder", null)}
              className="w-6 h-6 rounded flex items-center justify-center text-lead hover:text-ink hover:bg-thread/40 transition-colors cursor-pointer"
              title="新建文件夹"
            >
              <FolderPlus size={14} />
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-3 py-3">
          {/* New item input */}
          {newItemType && (
            <div className="flex items-center gap-1.5 mb-2 px-1">
              {newItemType === "folder" ? (
                <Folder size={14} className="text-lead shrink-0" />
              ) : (
                <FileText size={14} className="text-lead shrink-0" />
              )}
              <input
                autoFocus
                value={newItemName}
                onChange={(e) => setNewItemName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleCreateItem();
                  if (e.key === "Escape") setNewItemType(null);
                }}
                onBlur={() => {
                  if (newItemName.trim()) handleCreateItem();
                  else setNewItemType(null);
                }}
                placeholder={
                  newItemType === "folder" ? "文件夹名称" : "文件名称"
                }
                className="flex-1 text-sm bg-transparent border-b border-accent outline-none text-ink placeholder:text-ghost py-0.5"
              />
              <button
                onClick={() => setNewItemType(null)}
                className="text-ghost hover:text-ink cursor-pointer"
              >
                <X size={12} />
              </button>
            </div>
          )}

          <FileTree
            files={files}
            activeFileId={activeFileId}
            onSelect={setActiveFileId}
            onDelete={deleteFile}
            onMove={moveFile}
            onCreateInFolder={(parentId) => startCreate("file", parentId)}
          />
        </div>
      </aside>

      {/* Editor area */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Toolbar */}
        <EditorToolbar />

        {/* Editor */}
        {activeFile ? (
          <NoteEditor
            key={activeFileId}
            content={activeFile.content}
            onChange={handleEditorChange}
          />
        ) : (
          <div className="flex-1 flex items-center justify-center text-ghost">
            选择或创建一个文件开始编辑
          </div>
        )}
      </div>

      {/* Timeline sidebar */}
      <aside className="w-[260px] bg-paper-warm border-l border-thread flex flex-col shrink-0">
        <div className="h-10 px-4 flex items-center border-b border-thread">
          <span className="font-serif text-sm font-medium text-ink">
            时间线
          </span>
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-4">
          <Timeline events={timeline} />
        </div>
      </aside>
    </div>
  );
}

/* ─── Toolbar ─── */

function EditorToolbar() {
  return (
    <div className="h-10 px-4 flex items-center gap-1 border-b border-thread bg-paper shrink-0">
      <ToolbarBtn icon={<Bold size={15} />} title="粗体" />
      <ToolbarBtn icon={<Italic size={15} />} title="斜体" />
      <ToolbarBtn icon={<Strikethrough size={15} />} title="删除线" />
      <div className="w-px h-5 bg-thread mx-1" />
      <ToolbarBtn icon={<Heading1 size={15} />} title="标题1" />
      <ToolbarBtn icon={<Heading2 size={15} />} title="标题2" />
      <ToolbarBtn icon={<Heading3 size={15} />} title="标题3" />
      <div className="w-px h-5 bg-thread mx-1" />
      <ToolbarBtn icon={<List size={15} />} title="列表" />
      <ToolbarBtn icon={<Quote size={15} />} title="引用" />
      <ToolbarBtn icon={<Code size={15} />} title="代码" />
      <ToolbarBtn icon={<Link size={15} />} title="链接" />
      <ToolbarBtn icon={<Minus size={15} />} title="分割线" />
    </div>
  );
}

function ToolbarBtn({
  icon,
  title,
  active,
}: {
  icon: React.ReactNode;
  title: string;
  active?: boolean;
}) {
  return (
    <button
      title={title}
      className={`w-7 h-7 rounded-md flex items-center justify-center transition-colors cursor-pointer ${
        active
          ? "bg-accent/15 text-accent"
          : "text-lead hover:text-ink hover:bg-thread/40"
      }`}
    >
      {icon}
    </button>
  );
}

/* ─── File Tree ─── */

function FileTree({
  files,
  activeFileId,
  onSelect,
  onDelete,
  onMove,
  onCreateInFolder,
}: {
  files: WorkspaceFile[];
  activeFileId: string;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onMove: (id: string, newParentId: string | null) => void;
  onCreateInFolder: (parentId: string) => void;
}) {
  const rootItems = files.filter((f) => !f.parentId);

  const handleDragStart = (e: React.DragEvent, id: string) => {
    e.stopPropagation();
    e.dataTransfer.setData("text/plain", id);
    e.dataTransfer.effectAllowed = "move";
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  };

  const handleDropOnRoot = (e: React.DragEvent) => {
    e.preventDefault();
    const id = e.dataTransfer.getData("text/plain");
    if (id) onMove(id, null);
  };

  return (
    <div
      className="flex flex-col min-h-[60px]"
      onDragOver={handleDragOver}
      onDrop={handleDropOnRoot}
    >
      {rootItems.map((item) => (
        <FileTreeItem
          key={item.id}
          item={item}
          files={files}
          activeFileId={activeFileId}
          depth={0}
          onSelect={onSelect}
          onDelete={onDelete}
          onMove={onMove}
          onCreateInFolder={onCreateInFolder}
          onDragStart={handleDragStart}
          onDragOver={handleDragOver}
        />
      ))}
    </div>
  );
}

function FileTreeItem({
  item,
  files,
  activeFileId,
  depth,
  onSelect,
  onDelete,
  onMove,
  onCreateInFolder,
  onDragStart,
  onDragOver,
}: {
  item: WorkspaceFile;
  files: WorkspaceFile[];
  activeFileId: string;
  depth: number;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onMove: (id: string, newParentId: string | null) => void;
  onCreateInFolder: (parentId: string) => void;
  onDragStart: (e: React.DragEvent, id: string) => void;
  onDragOver: (e: React.DragEvent) => void;
}) {
  const [expanded, setExpanded] = useState(true);
  const [hovered, setHovered] = useState(false);
  const children = files.filter((f) => f.parentId === item.id);
  const isFolder = item.type === "folder";

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const draggedId = e.dataTransfer.getData("text/plain");
    if (!draggedId || draggedId === item.id) return;
    onMove(draggedId, isFolder ? item.id : item.parentId);
  };

  if (isFolder) {
    return (
      <div className="mb-0.5">
        <div
          draggable
          onDragStart={(e) => onDragStart(e, item.id)}
          onDragOver={onDragOver}
          onDrop={handleDrop}
          onMouseEnter={() => setHovered(true)}
          onMouseLeave={() => setHovered(false)}
          className="flex items-center py-1 px-1 rounded-sm hover:bg-thread/40 cursor-pointer group"
          style={{ paddingLeft: `${depth * 12 + 4}px` }}
        >
          <button
            onClick={() => setExpanded(!expanded)}
            className="w-4 h-4 flex items-center justify-center shrink-0 cursor-pointer"
          >
            <ChevronRight
              size={12}
              className={`text-lead transition-transform ${expanded ? "rotate-90" : ""}`}
            />
          </button>
          <Folder
            size={14}
            className="text-lead shrink-0 ml-0.5"
            strokeWidth={2}
          />
          <span className="ml-1.5 text-[0.85rem] font-medium text-ink/90 truncate flex-1 select-none">
            {item.name}
          </span>
          {hovered && (
            <div className="flex gap-0.5">
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onCreateInFolder(item.id);
                }}
                className="w-5 h-5 rounded flex items-center justify-center text-ghost hover:text-ink cursor-pointer"
              >
                <Plus size={11} />
              </button>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete(item.id);
                }}
                className="w-5 h-5 rounded flex items-center justify-center text-ghost hover:text-red-500 cursor-pointer"
              >
                <Trash2 size={11} />
              </button>
            </div>
          )}
        </div>

        {expanded && children.length > 0 && (
          <div className="ml-2 pl-2 border-l border-thread/50">
            {children.map((child) => (
              <FileTreeItem
                key={child.id}
                item={child}
                files={files}
                activeFileId={activeFileId}
                depth={depth + 1}
                onSelect={onSelect}
                onDelete={onDelete}
                onMove={onMove}
                onCreateInFolder={onCreateInFolder}
                onDragStart={onDragStart}
                onDragOver={onDragOver}
              />
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <div
      draggable
      onDragStart={(e) => onDragStart(e, item.id)}
      onDragOver={onDragOver}
      onDrop={handleDrop}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onClick={() => onSelect(item.id)}
      className={`flex items-center py-1 px-1 rounded-sm cursor-pointer transition-colors group ${
        activeFileId === item.id
          ? "text-accent font-medium"
          : "text-ink/70 hover:text-ink/90 hover:bg-thread/40"
      }`}
      style={{ paddingLeft: `${depth * 12 + 24}px` }}
    >
      <div
        className={`w-1.5 h-1.5 rounded-full mr-1.5 shrink-0 ${
          activeFileId === item.id ? "bg-accent" : "bg-transparent"
        }`}
      />
      <span className="text-[0.85rem] truncate flex-1 select-none">
        {item.name}
      </span>
      {hovered && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onDelete(item.id);
          }}
          className="w-5 h-5 rounded flex items-center justify-center text-ghost hover:text-red-500 cursor-pointer"
        >
          <Trash2 size={11} />
        </button>
      )}
    </div>
  );
}

/* ─── Timeline ─── */

function Timeline({ events }: { events: TimelineEvent[] }) {
  const sorted = [...events].sort((a, b) => b.ts - a.ts);

  return (
    <div className="relative">
      {/* Center axis */}
      <div className="absolute left-1/2 top-0 bottom-0 w-[2px] bg-thread/60 -translate-x-1/2" />

      <div className="flex flex-col gap-3 pt-1">
        {sorted.map((event) => {
          const isUser = event.type === "user";
          return (
            <div
              key={event.id}
              className="relative flex items-center justify-between"
            >
              {/* Left column (user) */}
              <div
                className={`w-[calc(50%-12px)] text-right ${!isUser ? "invisible" : ""}`}
              >
                <div className="text-[0.85rem] text-ink leading-tight">
                  {event.title}
                </div>
                {event.fileName && (
                  <div className="text-[0.65rem] text-ghost font-mono">
                    {event.fileName}
                  </div>
                )}
                <div className="text-[0.6rem] text-lead mt-0.5 font-mono tracking-wide">
                  {event.time}
                </div>
              </div>

              {/* Center node */}
              <div className="w-4 h-4 flex items-center justify-center shrink-0 z-10 absolute left-1/2 -translate-x-1/2 bg-paper-warm">
                {isUser ? (
                  <Edit3 size={10} strokeWidth={2} className="text-ink" />
                ) : (
                  <Sparkles
                    size={10}
                    strokeWidth={2}
                    className="text-accent-warm"
                  />
                )}
              </div>

              {/* Right column (ai) */}
              <div
                className={`w-[calc(50%-12px)] text-left ${isUser ? "invisible" : ""}`}
              >
                <div className="text-[0.85rem] text-accent-warm leading-tight">
                  {event.title}
                </div>
                {event.fileName && (
                  <div className="text-[0.65rem] text-accent-warm/60 font-mono">
                    {event.fileName}
                  </div>
                )}
                <div className="text-[0.6rem] text-accent-warm/60 mt-0.5 font-mono tracking-wide">
                  {event.time}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
