import { useState, useEffect, useCallback } from "react";

export interface WorkspaceFile {
  id: string;
  name: string;
  type: "file" | "folder";
  parentId: string | null;
  content: string;
  author?: "human" | "ai";
  updatedAt: number;
}

export interface TimelineEvent {
  id: string;
  title: string;
  time: string;
  ts: number;
  type: "user" | "ai";
  fileId?: string;
  fileName?: string;
}

const STORAGE_KEY_FILES = "atrium-files";
const STORAGE_KEY_ACTIVE = "atrium-active-file";
const STORAGE_KEY_TIMELINE = "atrium-timeline";

const now = () => Date.now();
const formatTime = (ts: number) =>
  new Date(ts).toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  });

const defaultFiles: WorkspaceFile[] = [
  {
    id: "folder-1",
    name: "研究笔记",
    type: "folder",
    parentId: null,
    content: "",
    updatedAt: now(),
  },
  {
    id: "file-1",
    name: "草稿.md",
    type: "file",
    parentId: "folder-1",
    content: "<h1>第一份草稿</h1><p>开始我的研究...</p>",
    author: "human",
    updatedAt: now(),
  },
  {
    id: "file-2",
    name: "笔记.md",
    type: "file",
    parentId: "folder-1",
    content: "<p>快速记录</p>",
    author: "human",
    updatedAt: now(),
  },
  {
    id: "file-3",
    name: "想法.md",
    type: "file",
    parentId: null,
    content: "<h2>随想</h2><p>收集想法...</p>",
    author: "human",
    updatedAt: now(),
  },
];

const defaultTimeline: TimelineEvent[] = [
  {
    id: "t-1",
    title: "打开工作区",
    time: formatTime(now() - 300000),
    ts: now() - 300000,
    type: "user",
  },
  {
    id: "t-2",
    title: "AI 建议段落",
    time: formatTime(now() - 240000),
    ts: now() - 240000,
    type: "ai",
    fileId: "file-1",
    fileName: "草稿.md",
  },
  {
    id: "t-3",
    title: "编辑 草稿.md",
    time: formatTime(now() - 120000),
    ts: now() - 120000,
    type: "user",
    fileId: "file-1",
    fileName: "草稿.md",
  },
];

function loadJSON<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

export function useWorkspace() {
  const [files, setFiles] = useState<WorkspaceFile[]>(() =>
    loadJSON(STORAGE_KEY_FILES, defaultFiles)
  );
  const [activeFileId, setActiveFileId] = useState<string>(() =>
    localStorage.getItem(STORAGE_KEY_ACTIVE) || "file-1"
  );
  const [timeline, setTimeline] = useState<TimelineEvent[]>(() =>
    loadJSON(STORAGE_KEY_TIMELINE, defaultTimeline)
  );

  const activeFile = files.find((f) => f.id === activeFileId);

  // Persist
  useEffect(() => {
    localStorage.setItem(STORAGE_KEY_FILES, JSON.stringify(files));
  }, [files]);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY_ACTIVE, activeFileId);
  }, [activeFileId]);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY_TIMELINE, JSON.stringify(timeline));
  }, [timeline]);

  const addTimelineEvent = useCallback(
    (
      title: string,
      type: "user" | "ai",
      fileId?: string,
      fileName?: string
    ) => {
      const ts = now();
      setTimeline((prev) => [
        ...prev,
        {
          id: `t-${ts}`,
          title,
          time: formatTime(ts),
          ts,
          type,
          fileId,
          fileName,
        },
      ]);
    },
    []
  );

  const updateFileContent = useCallback(
    (id: string, content: string) => {
      setFiles((prev) =>
        prev.map((f) =>
          f.id === id ? { ...f, content, updatedAt: now() } : f
        )
      );
    },
    []
  );

  const createFile = useCallback(
    (name: string, parentId: string | null) => {
      const id = `file-${now()}`;
      const newFile: WorkspaceFile = {
        id,
        name: name.endsWith(".md") ? name : `${name}.md`,
        type: "file",
        parentId,
        content: "",
        author: "human",
        updatedAt: now(),
      };
      setFiles((prev) => [...prev, newFile]);
      setActiveFileId(id);
      addTimelineEvent(`新建 ${newFile.name}`, "user", id, newFile.name);
      return id;
    },
    [addTimelineEvent]
  );

  const createFolder = useCallback(
    (name: string, parentId: string | null) => {
      const id = `folder-${now()}`;
      setFiles((prev) => [
        ...prev,
        { id, name, type: "folder", parentId, content: "", updatedAt: now() },
      ]);
      addTimelineEvent(`新建文件夹 ${name}`, "user");
      return id;
    },
    [addTimelineEvent]
  );

  const deleteFile = useCallback(
    (id: string) => {
      const file = files.find((f) => f.id === id);
      if (!file) return;

      // Collect all descendant IDs for folders
      const toDelete = new Set<string>([id]);
      if (file.type === "folder") {
        const collect = (parentId: string) => {
          files
            .filter((f) => f.parentId === parentId)
            .forEach((f) => {
              toDelete.add(f.id);
              if (f.type === "folder") collect(f.id);
            });
        };
        collect(id);
      }

      setFiles((prev) => prev.filter((f) => !toDelete.has(f.id)));
      if (toDelete.has(activeFileId)) {
        const remaining = files.filter(
          (f) => !toDelete.has(f.id) && f.type === "file"
        );
        setActiveFileId(remaining[0]?.id || "");
      }
      addTimelineEvent(`删除 ${file.name}`, "user");
    },
    [files, activeFileId, addTimelineEvent]
  );

  const moveFile = useCallback(
    (id: string, newParentId: string | null) => {
      setFiles((prev) =>
        prev.map((f) => (f.id === id ? { ...f, parentId: newParentId } : f))
      );
    },
    []
  );

  return {
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
  };
}
