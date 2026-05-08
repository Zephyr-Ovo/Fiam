import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type Dispatch, type ElementType, type SetStateAction } from 'react';
import { Edit3, Sparkles, Plus, Folder, FileText, Menu, LogOut, X, Book, Trash2, FolderPlus, FilePlus, ChevronRight, ChevronDown } from 'lucide-react';
import {
  useCreateBlockNote,
  SuggestionMenuController,
  FormattingToolbar,
  BasicTextStyleButton,
  ColorStyleButton,
  getDefaultReactSlashMenuItems,
} from '@blocknote/react';
import { filterSuggestionItems } from '@blocknote/core';
import { BlockNoteView } from '@blocknote/mantine';
import '@blocknote/core/fonts/inter.css';
import '@blocknote/mantine/style.css';
import { motion, AnimatePresence } from 'framer-motion';
import { Tree, type NodeRendererProps, type MoveHandler } from 'react-arborist';
import { useStudioState } from './useStudioState';
import type { WorkspaceFile } from './useStudioState';

const ICON_MAP: Record<string, ElementType> = {
  Plus: Plus,
  Edit3: Edit3,
  Sparkles: Sparkles,
  FileText: FileText,
  Folder: Folder,
};

const studioTheme = {
  '--color-paper': '#F7F5F0',
  '--color-card': '#FFFFFF',
  '--color-ink': '#4A443D',
  '--color-lead': '#A39B8E',
  '--color-accent': '#D99477',
  '--color-thread': '#E5E1D8',
  '--font-sans': '"Anthropic Sans", ui-sans-serif, system-ui, sans-serif',
  '--font-serif': '"Anthropic Serif", Georgia, Cambria, "Times New Roman", Times, serif',
} as CSSProperties;

const FixedToolbar = () => {
  return (
    <div className="fixed bottom-[110px] left-1/2 -translate-x-1/2 z-[40] flex items-center justify-center p-1 rounded-full bg-paper/95 backdrop-blur shadow-[0_4px_24px_rgba(102,78,68,0.15)] border border-thread pointer-events-auto">
      <div className="flex items-center space-x-1 format-toolbar-container">
        <FormattingToolbar>
          <BasicTextStyleButton basicTextStyle="bold" />
          <BasicTextStyleButton basicTextStyle="italic" />
          <BasicTextStyleButton basicTextStyle="underline" />
          <BasicTextStyleButton basicTextStyle="strike" />
          <ColorStyleButton />
        </FormattingToolbar>
      </div>
    </div>
  );
};

const Editor = ({ content, onChange, onInteract, onCloseTimeline }: { content: string, onChange: (html: string) => void, onInteract: () => void, onCloseTimeline?: () => void }) => {
  const editor = useCreateBlockNote();
  const lastExternalContentRef = useRef(content);
  const lastEmittedRef = useRef<string>('');

  // Load initial / externally-changed content (HTML) into BlockNote.
  useEffect(() => {
    if (!editor) return;
    if (content === lastExternalContentRef.current && content === lastEmittedRef.current) return;
    if (content === lastEmittedRef.current) return;
    lastExternalContentRef.current = content;
    let cancelled = false;
    (async () => {
      const blocks = await editor.tryParseHTMLToBlocks(content || '');
      if (cancelled) return;
      editor.replaceBlocks(editor.document, blocks);
    })();
    return () => { cancelled = true; };
  }, [content, editor]);

  // Paper / ink theme aligned with the rest of Studio (var values from studioTheme above).
  const paperTheme = useMemo(() => ({
    colors: {
      editor: { text: '#4A443D', background: '#F7F5F0' },
      menu: { text: '#4A443D', background: '#FFFFFF' },
      tooltip: { text: '#F7F5F0', background: '#4A443D' },
      hovered: { text: '#4A443D', background: '#E5E1D8' },
      selected: { text: '#FFFFFF', background: '#D99477' },
      disabled: { text: '#A39B8E', background: '#E5E1D8' },
      shadow: '#A39B8E',
      border: '#E5E1D8',
      sideMenu: '#A39B8E',
      highlights: {
        gray:   { text: '#4A443D', background: '#E5E1D8' },
        brown:  { text: '#FFFFFF', background: '#8B5A4B' },
        red:    { text: '#FFFFFF', background: '#C0392B' },
        orange: { text: '#FFFFFF', background: '#D99477' },
        yellow: { text: '#4A443D', background: '#F4E4BC' },
        green:  { text: '#FFFFFF', background: '#5B7A6B' },
        blue:   { text: '#FFFFFF', background: '#6B7A8B' },
        purple: { text: '#FFFFFF', background: '#8B6B7A' },
        pink:   { text: '#4A443D', background: '#F4D4D4' },
      },
    },
    borderRadius: 10,
    fontFamily: '"Anthropic Serif", Georgia, Cambria, "Times New Roman", Times, serif',
  } as const), []);

  const emitHTMLRef = useRef<number | null>(null);

  return (
    <div className="flex flex-col flex-1 overflow-hidden text-ink relative studio-blocknote" onClick={onCloseTimeline}>
      <BlockNoteView
        editor={editor}
        theme={paperTheme}
        slashMenu={false}
        formattingToolbar={false}
        onChange={() => {
          if (emitHTMLRef.current) window.clearTimeout(emitHTMLRef.current);
          emitHTMLRef.current = window.setTimeout(() => {
            const html = editor.blocksToHTMLLossy(editor.document) as unknown as string | Promise<string>;
            if (typeof (html as Promise<string>).then === 'function') {
              (html as Promise<string>).then(s => { lastEmittedRef.current = s; onChange(s); onInteract(); });
            } else {
              lastEmittedRef.current = html as string;
              onChange(html as string);
              onInteract();
            }
          }, 500); // 500ms debounce to prevent lag while typing
        }}
      >
        <FixedToolbar />
        {/* Slash menu — strip out heading items, keep paragraph/list/quote/code/divider/etc. */}
        <SuggestionMenuController
          triggerCharacter="/"
          getItems={async (query) => {
            const all = getDefaultReactSlashMenuItems(editor);
            const filtered = all.filter(item => {
              const g = (item.group || '').toLowerCase();
              const t = (item.title || '').toLowerCase();
              return !g.includes('heading') && !t.includes('heading');
            });
            return filterSuggestionItems(filtered, query);
          }}
        />
      </BlockNoteView>
    </div>
  );
};
type TreeNode = {
  id: string;
  name: string;
  type: 'file' | 'folder';
  children?: TreeNode[];
};

function buildTree(files: WorkspaceFile[]): TreeNode[] {
  const byParent = new Map<string | null, WorkspaceFile[]>();
  for (const f of files) {
    const key = f.parentId || null;
    if (!byParent.has(key)) byParent.set(key, []);
    byParent.get(key)!.push(f);
  }
  const make = (parent: string | null): TreeNode[] => {
    const list = byParent.get(parent) || [];
    return list.map(f => ({
      id: f.id,
      name: f.name,
      type: f.type,
      ...(f.type === 'folder' ? { children: make(f.id) } : {}),
    }));
  };
  return make(null);
}

const FileTree = ({ files, setFiles, activeFileId, onSelectFile, onDelete, onRename }: { files: WorkspaceFile[], setFiles: Dispatch<SetStateAction<WorkspaceFile[]>>, activeFileId: string, onSelectFile: (id: string) => void, onDelete: (id: string, label: string) => void, onRename?: (id: string, currentName: string, isFolder: boolean) => void }) => {
  const data = useMemo(() => buildTree(files), [files]);
  const containerRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 208, height: 400 });
  
  
  
  

  useLayoutEffect(() => {
    if (!containerRef.current) return;
    const el = containerRef.current;
    const update = () => setSize({ width: el.clientWidth, height: Math.max(120, el.clientHeight) });
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const isDescendantOf = (ancestorId: string, nodeId: string) => {
    let cur = files.find(f => f.id === nodeId);
    while (cur && cur.parentId) {
      if (cur.parentId === ancestorId) return true;
      cur = files.find(f => f.id === cur!.parentId);
    }
    return false;
  };

  const onMove: MoveHandler<TreeNode> = ({ dragIds, parentId, index }) => {
    setFiles(prev => {
      let next = [...prev];
      const newParent = parentId || null;
      for (const id of dragIds) {
        // Disallow moving a folder into its own descendant.
        if (newParent && (newParent === id || isDescendantOf(id, newParent))) continue;
        const dragged = next.find(f => f.id === id);
        if (!dragged) continue;
        next = next.filter(f => f.id !== id);
        const updated: WorkspaceFile = { ...dragged, parentId: newParent };

        // Find target insertion point: take the `index`-th sibling under newParent in current `next`,
        // then insert before it (or append if index >= siblings count).
        const siblings = next.filter(f => (f.parentId || null) === newParent);
        if (index >= siblings.length || index < 0) {
          // Append to end of array (after last sibling)
          if (siblings.length === 0) {
            next.push(updated);
          } else {
            const lastSib = siblings[siblings.length - 1];
            const lastIdx = next.findIndex(f => f.id === lastSib.id);
            next.splice(lastIdx + 1, 0, updated);
          }
        } else {
          const target = siblings[index];
          const targetIdx = next.findIndex(f => f.id === target.id);
          next.splice(targetIdx, 0, updated);
        }
      }
      return next;
    });
  };

    const Node = ({ node, style, dragHandle }: NodeRendererProps<TreeNode>) => {
    const isFolder = node.data.type === 'folder';
    const isActive = !isFolder && node.data.id === activeFileId;
    const isDropTarget = node.willReceiveDrop && isFolder;

    return (
        <div style={style} className="relative group/row overflow-hidden" onClick={() => {
            if (isFolder) node.toggle();
            else onSelectFile(node.data.id);
          }}>
          <div className="absolute inset-y-0 left-0 flex items-center justify-start pl-4 pointer-events-none">
             <Trash2 size={16} className="text-[#C0392B]" />
          </div>
          <motion.div
            drag="x"
            dragConstraints={{ left: 0, right: 60 }}
            dragElastic={0.1}
            onDragEnd={(_, { offset, velocity }) => {
              if (offset.x > 40 || velocity.x > 400) {
                 onDelete(node.data.id, node.data.name);
              }
            }}
            ref={dragHandle}
            className={`flex w-full items-center pr-2 rounded-sm cursor-pointer select-none transition-colors bg-paper relative z-10 h-full ${isDropTarget ? 'bg-accent/15 shadow-[inset_0_0_0_1.5px_rgba(217,148,119,0.55)]' : 'hover:bg-thread/50'} ${isActive ? 'text-accent font-medium' : 'text-ink/80'}`}
            style={{ touchAction: 'pan-y' }}
          >
            <div className={`flex items-center flex-1 transition-transform duration-200`}>
              {isFolder ? (
                <span className="w-4 h-4 flex items-center justify-center text-lead/70 shrink-0">
                  {node.isOpen ? <ChevronDown size={12} strokeWidth={2} /> : <ChevronRight size={12} strokeWidth={2} />}
                </span>
              ) : (
                <span className="w-4 h-4 flex items-center justify-center shrink-0">
                  <span className={`w-1.5 h-1.5 flex shrink-0 rounded-full ${isActive ? 'bg-accent' : 'bg-transparent'}`} />
                </span>
              )}
              {isFolder ? <Book size={13} className="text-lead shrink-0 mr-1.5" strokeWidth={2} /> : null}
              <span className="truncate flex-1 text-[0.95rem] pointer-events-none">{node.data.name}</span>
            </div>
            
            <div className="flex items-center gap-1 opacity-0 group-hover/row:opacity-100 transition-opacity">
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); if(onRename) onRename(node.data.id, node.data.name, isFolder); }}
                className="w-6 h-6 flex items-center justify-center rounded-md hover:bg-thread/60 text-ink/50 hover:text-ink/80 transition-colors"
                aria-label="Rename"
              >
                <Edit3 size={13} strokeWidth={2} />
              </button>
            </div>
          </motion.div>
        </div>
      );
    };

    return (
      <div className="flex flex-col min-h-[120px]">
        <div ref={containerRef} className="flex-1 min-h-[200px]">
        <Tree<TreeNode>
          data={data}
          onMove={onMove}
          openByDefault={false}
          width={size.width}
          height={size.height}
          indent={16}
          rowHeight={32}
          paddingTop={4}
          paddingBottom={4}
          disableMultiSelection
          renderCursor={({ top, left, indent }) => (
            <div
              style={{ position: 'absolute', top, left: left + indent, right: 4, height: 2, background: 'var(--color-accent)', borderRadius: 2, pointerEvents: 'none', zIndex: 5 }}
            />
          )}
        >
          {Node}
        </Tree>
      </div>
    </div>
  );
};

type StudioLocation = {
  lng?: number;
  lat?: number;
  accuracy?: number;
  placeKind?: string;
  label?: string;
};

function wordUnits(text?: string) {
  return (text || '').match(/[A-Za-z0-9_]+|[\u4e00-\u9fff]/g)?.length || 0;
}

function plainTextFromHtml(html: string) {
  return html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
}

export function Studio({ onBack }: { onBack: () => void }) {
  const [view, setView] = useState<'editor' | 'files'>('editor');
  const [logExpanded, setLogExpanded] = useState(false);
  const [studioLocation, setStudioLocation] = useState<StudioLocation | null>(null);
  const lastManualEventRef = useRef(0);
  const { timeline, addTimelineEvent, activeNoteContent, setActiveNoteContent, files, setFiles, activeFileId, setActiveFileId, activeFile, createNote, createFolder, deleteItem } = useStudioState();
  const activeFileName = activeFile?.name || 'Untitled';

  useEffect(() => {
    if (!('geolocation' in navigator)) return;
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setStudioLocation({
          lng: position.coords.longitude,
          lat: position.coords.latitude,
          accuracy: position.coords.accuracy,
          placeKind: 'studio',
        });
      },
      () => undefined,
      { enableHighAccuracy: true, maximumAge: 60_000, timeout: 8000 },
    );
  }, []);

  const handleEditorInteract = () => {
    const now = Date.now();
    if (now - lastManualEventRef.current < 45_000) return;
    lastManualEventRef.current = now;
    addTimelineEvent('wrote in the document', 'user', 'Edit3', {
      kind: 'manual_edit',
      units: Math.max(1, Math.min(240, wordUnits(plainTextFromHtml(activeNoteContent)))) ,
      location: studioLocation || undefined,
    });
  };

  const createNewNote = () => {
    const now = new Date();
    const name = `Note ${now.toLocaleDateString([], { month: 'short', day: 'numeric' })}`;
    const fileId = createNote(name, `<h1>${name}</h1><p></p>`);
    addTimelineEvent('created a note', 'user', 'Folder', {
      kind: 'note_created',
      fileId,
      fileName: name,
      units: wordUnits(name),
      location: studioLocation || undefined,
    });
  };

  const handleCreatePage = (parentId: string | null) => {
    const now = new Date();
    const name = `Note ${now.toLocaleDateString([], { month: 'short', day: 'numeric' })}`;
    const fileId = createNote(name, `<h1>${name}</h1><p></p>`, parentId);
    addTimelineEvent('created a note', 'user', 'Folder', {
      kind: 'note_created',
      fileId,
      fileName: name,
      units: wordUnits(name),
      location: studioLocation || undefined,
    });
  };

  const handleCreateFolder = (parentId: string | null) => {
    const stamp = new Date().toLocaleDateString([], { month: 'short', day: 'numeric' });
    const name = `Notebook ${stamp}`;
    createFolder(name, parentId);
    addTimelineEvent('created a notebook', 'user', 'Folder', {
      kind: 'note_created',
      fileName: name,
      units: wordUnits(name),
      location: studioLocation || undefined,
    });
  };

    const [pendingRename, setPendingRename] = useState<{id: string, name: string, isFolder: boolean} | null>(null);
  const handleRename = (id: string, name: string, isFolder: boolean) => setPendingRename({ id, name, isFolder });
  const [pendingDelete, setPendingDelete] = useState<{ id: string, label: string } | null>(null);

  const handleDelete = (id: string, label: string) => {
    setPendingDelete({ id, label });
  };

  const confirmDelete = () => {
    if (pendingDelete) deleteItem(pendingDelete.id);
    setPendingDelete(null);
  };

  return (
    <div className="studio-root h-full w-full bg-[#E5E1D8] sm:bg-[#1a1a1a] flex items-center justify-center font-sans overflow-hidden" style={studioTheme}>
      <div className="w-full h-full sm:w-[412px] sm:h-[915px] bg-paper sm:rounded-[3rem] sm:shadow-[0_0_0_12px_#2a2a2a,0_10px_40px_rgba(0,0,0,0.5)] flex flex-col relative overflow-hidden outline outline-1 outline-thread/20">
        <div className="h-16 flex items-center justify-between px-2 shrink-0 border-b border-thread/50 bg-paper z-10 transition-colors">
          <button type="button" onClick={() => setView('files')} className="w-12 h-12 flex items-center justify-center rounded-2xl hover:bg-thread/40 active:bg-thread/60 transition-colors" aria-label="Files">
            <Menu size={24} className="text-ink" />
          </button>

          <div className="flex-1 flex justify-center">
             <div className="font-serif font-medium text-lg text-ink truncate px-2">{activeFileName}</div>
          </div>

          <button type="button" onClick={onBack} className="w-12 h-12 flex items-center justify-center rounded-2xl hover:bg-thread/40 active:bg-thread/60 transition-colors" aria-label="Back">
            <LogOut size={22} className="text-ink" />
          </button>
        </div>

        <Editor content={activeNoteContent} onChange={setActiveNoteContent} onInteract={handleEditorInteract} onCloseTimeline={() => logExpanded && setLogExpanded(false)} />

        <motion.button type="button" onClick={createNewNote} animate={{ y: logExpanded ? -360 : 0 }} transition={{ type: 'spring', damping: 25, stiffness: 200 }} className="absolute right-6 w-12 h-12 bg-accent text-paper rounded-full shadow-[0_4px_14px_rgba(217,148,119,0.3)] flex items-center justify-center hover:scale-95 active:scale-90 transition-transform z-30" style={{ bottom: '102px' }} aria-label="New page">
          <Plus size={20} strokeWidth={2.5} />
        </motion.button>

              <AnimatePresence>
        {pendingRename && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.12 }}
            className="absolute inset-0 z-50 flex items-center justify-center bg-ink/30 backdrop-blur-[2px] px-8"
            onClick={() => setPendingRename(null)}
          >
            <motion.div
              initial={{ scale: 0.92, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.92, opacity: 0 }}
              transition={{ type: 'spring', damping: 22, stiffness: 280 }}
              className="bg-paper rounded-2xl shadow-[0_12px_40px_rgba(0,0,0,0.18)] w-full max-w-[280px] overflow-hidden"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="px-5 pt-5 pb-3 text-center">
                <div className="font-serif text-[1.05rem] text-ink mb-1">Rename {pendingRename.isFolder ? 'Folder' : 'File'}</div>
                <input 
                  type="text" 
                  defaultValue={pendingRename.name} 
                  autoFocus 
                  onFocus={(e) => e.target.select()}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      const val = e.currentTarget.value.trim();
                      if (val) {
                        setFiles(prev => prev.map(f => f.id === pendingRename.id ? { ...f, name: val } : f));
                        setPendingRename(null);
                      }
                    } else if (e.key === 'Escape') {
                      setPendingRename(null);
                    }
                  }}
                  className="w-full mt-2 bg-thread/20 border border-thread rounded-lg px-3 py-2 text-ink outline-none focus:border-accent/40 font-serif text-[0.95rem] placeholder-lead/50"
                  placeholder="New Name"
                />
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
        <AnimatePresence>
          {logExpanded && (
            <motion.div
              initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
              onClick={() => setLogExpanded(false)}
              className="absolute inset-0 z-[15] bg-transparent"
            />
          )}
        </AnimatePresence>
        {/* Timeline drawer. Compact handle (title + small buffer); body is paper-colored. */}
        <motion.div initial={{ y: 'calc(100% - 90px)' }} animate={{ y: logExpanded ? 0 : 'calc(100% - 90px)' }} transition={{ type: 'spring', damping: 25, stiffness: 200 }} className="absolute left-0 right-0 bottom-0 h-[calc(50%+30px)] sm:h-[410px] bg-paper rounded-t-[24px] border-t border-thread shadow-[0_-10px_40px_rgba(0,0,0,0.05)] flex flex-col z-20">
          <button type="button" onClick={() => setLogExpanded(!logExpanded)} className="h-[90px] shrink-0 w-full flex flex-col items-center pt-2 pb-2 relative active:bg-thread/20 rounded-t-[24px] transition-colors" aria-label="Toggle timeline">
            <div className="w-10 h-[4px] bg-thread/60 rounded-full mb-1" />
            <div className="font-serif text-[1rem] text-ink font-medium tracking-wide">Timeline</div>
          </button>

          <div className="flex-1 overflow-y-auto px-5 pb-[64px] pt-0">
            <div className="flex flex-col relative w-full pt-1">
              <div className="absolute left-1/2 top-4 bottom-4 w-[2px] bg-thread/60 -translate-x-1/2 z-0"></div>

              {timeline.map((item) => {
                const isUser = item.type === 'user';
                const IconComp = ICON_MAP[item.iconName] || Edit3;
                return (
                  <div key={item.id} className="relative z-10 w-full flex items-center justify-between mb-3">
                    <div className={`w-[calc(50%-1.1rem)] text-right ${!isUser ? 'invisible' : ''}`}>
                      <div className="text-[0.925rem] text-ink">{item.title}</div>
                      <div className="text-[0.65rem] text-lead mt-0 font-mono tracking-wide">{item.time}</div>
                    </div>

                    <div className="w-5 h-5 flex items-center justify-center shrink-0 z-10 absolute left-1/2 -translate-x-1/2 bg-paper py-0.5 my-0.5">
                      <IconComp size={14} strokeWidth={2} className={isUser ? "text-ink" : "text-accent"} />
                    </div>

                    <div className={`w-[calc(50%-1.1rem)] text-left ${isUser ? 'invisible' : ''}`}>
                      <div className="text-[0.925rem] text-accent">{item.title}</div>
                      <div className="text-[0.65rem] text-accent/60 mt-0 font-mono tracking-wide">{item.time}</div>
                    </div>
                  </div>
                );
              })}
              <div className="h-2" />
            </div>
          </div>
        </motion.div>

        <AnimatePresence>
          {view === 'files' && (
            <>
              <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="absolute inset-0 bg-black/10 z-20" onClick={() => setView('editor')} />

              <motion.div drag="x" dragConstraints={{ left: -100, right: 0 }} dragElastic={0.1} onDragEnd={(_, { offset, velocity }) => { if (offset.x < -50 || velocity.x < -500) { setView('editor'); } }} initial={{ x: '-100%', opacity: 0 }} animate={{ x: 0, opacity: 1 }} exit={{ x: '-100%', opacity: 0 }} transition={{ type: 'spring', damping: 30, stiffness: 300 }} className="select-none absolute top-0 bottom-0 left-0 w-[240px] bg-paper shadow-[4px_0_24px_rgba(0,0,0,0.08)] z-30 flex flex-col border-r border-thread/60">
                <div className="h-16 px-4 flex items-center justify-between shrink-0 border-b border-thread/50">
                  <div className="font-serif text-lg font-medium text-ink">Studio</div>
                  <button type="button" onClick={() => setView('editor')} className="w-8 h-8 flex items-center justify-center rounded-lg active:bg-thread/60 text-ink/60 hover:text-ink transition-colors" aria-label="Close files">
                    <X size={18} />
                  </button>
                </div>

                <div className="px-4 pt-3 pb-2 flex items-center justify-between shrink-0 border-b border-thread/30">
                  <span className="text-[0.7rem] uppercase tracking-[0.12em] text-lead/80 font-medium">Files</span>
                  <div className="flex items-center gap-3">
                    <button type="button" onClick={() => handleCreateFolder(null)} className="w-7 h-7 flex items-center justify-center rounded-md hover:bg-thread/40 active:bg-thread/60 text-ink/65 hover:text-ink transition-colors" aria-label="New notebook" title="New notebook">
                      <FolderPlus size={15} strokeWidth={2} />
                    </button>
                    <button type="button" onClick={() => handleCreatePage(null)} className="w-7 h-7 flex items-center justify-center rounded-md hover:bg-thread/40 active:bg-thread/60 text-ink/65 hover:text-ink transition-colors" aria-label="New page" title="New page">
                      <FilePlus size={15} strokeWidth={2} />
                    </button>
                  </div>
                </div>

                <div className="flex-1 overflow-y-auto px-4 pb-6 pt-2">
                  <FileTree files={files} setFiles={setFiles} activeFileId={activeFileId} onSelectFile={(id) => { setActiveFileId(id); setView('editor'); }} onDelete={handleDelete} onRename={handleRename} />
                </div>
              </motion.div>
            </>
          )}
        </AnimatePresence>

        {/* Delete confirmation modal — long-press on a tree row triggers this. */}
        <AnimatePresence>
          {pendingDelete && (
            <motion.div
              key="delete-modal"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.12 }}
              className="absolute inset-0 z-50 flex items-center justify-center bg-ink/30 backdrop-blur-[2px] px-8"
              onClick={() => setPendingDelete(null)}
            >
              <motion.div
                initial={{ scale: 0.92, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                exit={{ scale: 0.92, opacity: 0 }}
                transition={{ type: 'spring', damping: 22, stiffness: 280 }}
                className="bg-paper rounded-2xl shadow-[0_12px_40px_rgba(0,0,0,0.18)] w-full max-w-[280px] overflow-hidden"
                onClick={(e) => e.stopPropagation()}
              >
                <div className="px-5 pt-5 pb-3 text-center">
                  <div className="font-serif text-[1.05rem] text-ink mb-1">删除</div>
                  <div className="text-[0.85rem] text-lead truncate">「{pendingDelete.label}」</div>
                </div>
                <div className="grid grid-cols-2 border-t border-thread/60">
                  <button type="button" onClick={() => setPendingDelete(null)} className="py-3 text-[0.95rem] text-ink/70 active:bg-thread/40 transition-colors border-r border-thread/60">
                    取消
                  </button>
                  <button type="button" onClick={confirmDelete} className="py-3 text-[0.95rem] text-[#C0392B] font-medium active:bg-[#C0392B]/10 transition-colors">
                    删除
                  </button>
                </div>
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}


