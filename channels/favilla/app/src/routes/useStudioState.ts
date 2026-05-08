import { useCallback, useEffect, useMemo, useState, type SetStateAction } from 'react';
import { fetchStudioState, saveStudioState } from '../lib/api';

export interface WorkspaceFile {
  id: string;
  name: string;
  type: 'file' | 'folder';
  parentId?: string | null;
}

export interface TimelineEvent {
  id: string;
  title: string;
  time: string;
  type: 'user' | 'ai';
  iconName: string;
  at?: number;
  kind?: 'manual_edit' | 'ai_edit_request' | 'ai_edit_suggested' | 'ai_edit_applied' | 'note_created' | 'file_selected';
  fileId?: string;
  fileName?: string;
  units?: number;
  summary?: string;
  location?: {
    lng?: number;
    lat?: number;
    accuracy?: number;
    placeKind?: string;
    label?: string;
    cellId?: string;
  };
  operations?: number;
}

const INITIAL_FILES: WorkspaceFile[] = [
  { id: 'folder1', name: '2026 Archive', type: 'folder' },
  { id: 'file1', name: 'Initial Sandbox', type: 'file', parentId: 'folder1' },
  { id: 'folder2', name: 'Ideas', type: 'folder' },
  { id: 'file2', name: 'Favilla Concept', type: 'file', parentId: 'folder2' },
  { id: 'file3', name: 'Stroll Protocol', type: 'file' },
  { id: 'folder3', name: 'Notes', type: 'folder' },
];

const INITIAL_TIMELINE: TimelineEvent[] = [
  { id: '1', title: 'created a notebook', time: '09:00 AM', type: 'user', iconName: 'Folder', kind: 'note_created' },
  { id: '2', title: 'wrote in the document', time: '09:12 AM', type: 'user', iconName: 'Edit3', kind: 'manual_edit' },
  { id: '3', title: 'suggested a color palette', time: '09:15 AM', type: 'ai', iconName: 'Sparkles', kind: 'ai_edit_suggested' },
];

const INITIAL_FILE_CONTENTS: Record<string, string> = {
  file1: '<h1>Initial Sandbox</h1><p>Drop rough fragments here before they become notes.</p>',
  file2: "<h1>Favilla Concept</h1><p>We want to create a calm, distraction-free environment for thinking and writing.</p><ul><li><strong>Palette:</strong> Beige (#F7F5F0), ink (#4A443D), terracotta (#D99477).</li><li><strong>Layout:</strong> Central axis timeline tree.</li></ul><blockquote><p>A place where ideas settle like dust in morning light.</p></blockquote><p></p>",
  file3: '<h1>Stroll Protocol</h1><p>Stroll turns location, camera, and small AI actions into spatial memory.</p>',
};

function eventTime(date = new Date()) {
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function wordUnits(text?: string) {
  return (text || '').match(/[A-Za-z0-9_]+|[\u4e00-\u9fff]/g)?.length || 0;
}

export function useStudioState() {
  const [files, setFiles] = useState<WorkspaceFile[]>(INITIAL_FILES);
  const [activeFileId, setActiveFileId] = useState<string>('file2');
  const [fileContents, setFileContents] = useState<Record<string, string>>(INITIAL_FILE_CONTENTS);
  const [timeline, setTimeline] = useState<TimelineEvent[]>(INITIAL_TIMELINE);
  const [hydrated, setHydrated] = useState(false);
  const [serverReady, setServerReady] = useState(false);
  const activeNoteContent = useMemo(() => fileContents[activeFileId] || '<p></p>', [activeFileId, fileContents]);

  useEffect(() => {
    let cancelled = false;
    fetchStudioState()
      .then((result) => {
        if (cancelled || !result.ok) return;
        setServerReady(true);
        if (!result.state) return;
        if (Array.isArray(result.state.files)) setFiles(result.state.files as WorkspaceFile[]);
        const nextActiveFileId = typeof result.state.activeFileId === 'string' && result.state.activeFileId ? result.state.activeFileId : 'file2';
        const storedContents = result.state.fileContents && typeof result.state.fileContents === 'object' ? result.state.fileContents : {};
        const nextContents = { ...INITIAL_FILE_CONTENTS, ...storedContents } as Record<string, string>;
        if (typeof result.state.activeNoteContent === 'string') nextContents[nextActiveFileId] = result.state.activeNoteContent;
        setFileContents(nextContents);
        setActiveFileId(nextActiveFileId);
        if (Array.isArray(result.state.timeline)) setTimeline(result.state.timeline as TimelineEvent[]);
      })
      .finally(() => {
        if (!cancelled) setHydrated(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!hydrated || !serverReady) return;
    const timer = window.setTimeout(() => {
      void saveStudioState({ files, activeFileId, activeNoteContent, fileContents, timeline });
    }, 700);
    return () => window.clearTimeout(timer);
  }, [activeFileId, activeNoteContent, fileContents, files, hydrated, serverReady, timeline]);

  const setActiveNoteContent = useCallback((next: SetStateAction<string>) => {
    setFileContents(prev => {
      const current = prev[activeFileId] || '';
      const value = typeof next === 'function' ? (next as (value: string) => string)(current) : next;
      return { ...prev, [activeFileId]: value };
    });
  }, [activeFileId]);

  const activeFile = files.find(file => file.id === activeFileId);

  const addTimelineEvent = useCallback((title: string, type: 'user' | 'ai', iconName: string = 'Plus', meta: Partial<Omit<TimelineEvent, 'id' | 'title' | 'time' | 'type' | 'iconName'>> = {}) => {
    const now = Date.now();
    setTimeline(prev => [...prev, {
      id: `studio-${now}-${Math.random().toString(16).slice(2)}`,
      title,
      time: eventTime(new Date(now)),
      type,
      iconName,
      at: now,
      fileId: activeFileId,
      fileName: activeFile?.name,
      units: wordUnits(title),
      ...meta,
    }]);
  }, [activeFile?.name, activeFileId]);

  const createNote = useCallback((name: string, content: string, parentId: string | null = 'folder3') => {
    const id = `file-${Date.now()}`;
    setFiles(prev => [...prev, { id, name, type: 'file', parentId }]);
    setFileContents(prev => ({ ...prev, [id]: content }));
    setActiveFileId(id);
    return id;
  }, []);

  const createFolder = useCallback((name: string, parentId: string | null = null) => {
    const id = `folder-${Date.now()}`;
    setFiles(prev => [...prev, { id, name, type: 'folder', parentId }]);
    return id;
  }, []);

  const deleteItem = useCallback((id: string) => {
    setFiles(prev => {
      const toRemove = new Set<string>([id]);
      // Recursively collect descendant ids so deleting a folder deletes its pages.
      let changed = true;
      while (changed) {
        changed = false;
        for (const f of prev) {
          if (f.parentId && toRemove.has(f.parentId) && !toRemove.has(f.id)) {
            toRemove.add(f.id);
            changed = true;
          }
        }
      }
      const next = prev.filter(f => !toRemove.has(f.id));
      // If active file got deleted, move to first remaining file.
      setActiveFileId(prevActive => {
        if (!toRemove.has(prevActive)) return prevActive;
        const fallback = next.find(f => f.type === 'file');
        return fallback ? fallback.id : prevActive;
      });
      // Drop content for removed files.
      setFileContents(prevContents => {
        const out: Record<string, string> = {};
        for (const [k, v] of Object.entries(prevContents)) {
          if (!toRemove.has(k)) out[k] = v;
        }
        return out;
      });
      return next;
    });
  }, []);

  return {
    activeNoteContent,
    setActiveNoteContent,
    fileContents,
    files,
    setFiles,
    activeFileId,
    setActiveFileId,
    activeFile,
    timeline,
    addTimelineEvent,
    createNote,
    createFolder,
    deleteItem,
  };
}
