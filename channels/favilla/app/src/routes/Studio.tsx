import { useEffect, useMemo, useRef, useState, type CSSProperties, type Dispatch, type DragEvent, type ElementType, type SetStateAction } from 'react';
import { Edit3, Sparkles, Plus, Folder, FileText, Menu, LogOut, X, Quote, MessageSquare, Code, List, Strikethrough, Book } from 'lucide-react';
import { useEditor, EditorContent } from '@tiptap/react';
import { BubbleMenu } from '@tiptap/react/menus';
import StarterKit from '@tiptap/starter-kit';
import Placeholder from '@tiptap/extension-placeholder';
import { TextStyle } from '@tiptap/extension-text-style';
import { Color } from '@tiptap/extension-color';
import { Extension } from '@tiptap/core';
import { motion, AnimatePresence } from 'framer-motion';
import { useStudioState } from './useStudioState';
import type { WorkspaceFile } from './useStudioState';
import { requestStudioEdit, type StudioEditCommand } from '../lib/api';

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

const AuthorExtension = Extension.create({
  name: 'author',
  addGlobalAttributes() {
    return [
      {
        types: ['paragraph', 'heading', 'blockquote', 'codeBlock', 'bulletList', 'taskList'],
        attributes: {
          author: {
            default: null,
            parseHTML: element => element.getAttribute('data-author'),
            renderHTML: attributes => {
              if (!attributes.author) return {}
              return { 'data-author': attributes.author }
            }
          }
        }
      }
    ]
  }
});

const Editor = ({ content, onChange, onInteract, onAiEdit }: { content: string, onChange: (html: string) => void, onInteract: () => void, onAiEdit: () => void }) => {
  const [customColors, setCustomColors] = useState<string[]>(['#4A443D', '#D99477', '#A39B8E', '#8B5A4B', '#5B7A6B']);
  const lastExternalContentRef = useRef(content);

  const addCustomColor = (color: string) => {
    if (!customColors.includes(color)) {
      setCustomColors([...customColors, color]);
    }
  };

  const editor = useEditor({
    extensions: [
      StarterKit,
      AuthorExtension,
      TextStyle,
      Color,
      Placeholder.configure({
        placeholder: 'Write something, or ask AI...',
      }),
    ],
    content,
    immediatelyRender: false,
    onUpdate: ({ editor }) => {
      lastExternalContentRef.current = editor.getHTML();
      onChange(editor.getHTML());
      onInteract();
    },
    editorProps: {
      attributes: {
        class: 'prose mx-auto focus:outline-none min-h-[300px]',
      }
    }
  });

  useEffect(() => {
    if (!editor) return;
    if (content === lastExternalContentRef.current || content === editor.getHTML()) return;
    lastExternalContentRef.current = content;
    editor.commands.setContent(content, { emitUpdate: false });
  }, [content, editor]);

  if (!editor) return null;

  return (
    <div className="flex-1 overflow-y-auto px-6 py-4 text-ink text-[1.05rem] relative">
      <BubbleMenu editor={editor} className="flex flex-col overflow-hidden rounded-xl bg-ink shadow-lg shadow-black/20 text-paper p-1 gap-1 min-w-[240px]">
        <div className="flex items-center justify-between px-1">
          <button type="button" onClick={() => editor.chain().focus().toggleStrike().run()} className={`p-2 rounded-[0.5rem] transition-colors ${editor.isActive('strike') ? 'bg-paper text-ink' : 'hover:bg-paper/20'}`} aria-label="Strike">
            <Strikethrough size={16} strokeWidth={2} />
          </button>
          <button type="button" onClick={() => editor.chain().focus().toggleCodeBlock().run()} className={`p-2 rounded-[0.5rem] transition-colors ${editor.isActive('codeBlock') ? 'bg-paper text-ink' : 'hover:bg-paper/20'}`} aria-label="Code block">
            <Code size={16} strokeWidth={2} />
          </button>
          <button type="button" onClick={() => editor.chain().focus().toggleBulletList().run()} className={`p-2 rounded-[0.5rem] transition-colors ${editor.isActive('bulletList') ? 'bg-paper text-ink' : 'hover:bg-paper/20'}`} aria-label="List">
            <List size={16} strokeWidth={2} />
          </button>
          <button type="button" onClick={() => editor.chain().focus().toggleBlockquote().run()} className={`p-2 rounded-[0.5rem] transition-colors ${editor.isActive('blockquote') ? 'bg-paper text-ink' : 'hover:bg-paper/20'}`} aria-label="Quote">
            <Quote size={16} strokeWidth={2} />
          </button>
          <div className="w-px h-6 bg-paper/20 mx-1" />
          <button type="button" onClick={onAiEdit} className="p-2 rounded-[0.5rem] transition-colors hover:bg-paper/20 text-paper" title="AI" aria-label="AI edit">
            <Sparkles size={16} strokeWidth={2} />
          </button>
          <button type="button" className="p-2 rounded-[0.5rem] transition-colors hover:bg-paper/20 text-paper" title="批注" aria-label="Annotate">
            <MessageSquare size={16} strokeWidth={2} />
          </button>
        </div>
        <div className="flex px-2 pb-2 gap-2 items-center border-t border-paper/10 pt-2 flex-wrap">
          {customColors.map(c => (
            <button
              key={c}
              type="button"
              onClick={() => editor.chain().focus().setColor(c).run()}
              className={`w-[16px] h-[16px] flex-shrink-0 rounded-full hover:scale-110 transition-transform ${editor.isActive('textStyle', { color: c }) ? 'ring-2 ring-white/50' : ''}`}
              style={{ backgroundColor: c }}
              aria-label={`Color ${c}`}
            />
          ))}
          <div className="relative w-[16px] h-[16px] flex-shrink-0 rounded-full border border-dashed border-paper/50 flex items-center justify-center overflow-hidden hover:border-paper transition-colors group cursor-pointer">
            <Plus size={10} className="text-paper/50 group-hover:text-paper" />
            <input
              title="Add custom color"
              type="color"
              className="absolute inset-[-10px] opacity-0 cursor-pointer w-10 h-10"
              onChange={(e) => {
                const col = e.target.value;
                addCustomColor(col);
                editor.chain().focus().setColor(col).run();
              }}
            />
          </div>
          <button type="button" onClick={() => editor.chain().focus().unsetColor().run()} className="text-[10px] ml-auto bg-paper/20 px-1.5 py-0.5 rounded cursor-pointer hover:bg-paper/40 transition-colors">
            清除
          </button>
        </div>
      </BubbleMenu>

      <EditorContent editor={editor} className="h-full pb-32" />
    </div>
  );
};

const FileTree = ({ files, setFiles, activeFileId, onSelectFile }: { files: WorkspaceFile[], setFiles: Dispatch<SetStateAction<WorkspaceFile[]>>, activeFileId: string, onSelectFile: (id: string) => void }) => {
  const handleDragStart = (e: DragEvent, id: string) => {
    e.stopPropagation();
    e.dataTransfer.setData('text/plain', id);
  };

  const handleDragOver = (e: DragEvent) => {
    e.preventDefault();
  };

  const handleDrop = (e: DragEvent, targetId: string, targetType: 'file' | 'folder', targetParentId: string | null | undefined) => {
    e.preventDefault();
    e.stopPropagation();
    const draggedId = e.dataTransfer.getData('text/plain');
    if (!draggedId || draggedId === targetId) return;

    setFiles(prev => {
      const draggedFile = prev.find(f => f.id === draggedId);
      if (!draggedFile) return prev;

      const newParentId = targetType === 'folder' ? targetId : targetParentId || null;

      if (newParentId === draggedId) return prev;
      return prev.map(f => f.id === draggedId ? { ...f, parentId: newParentId } : f);
    });
  };

  const rootItems = files.filter(f => !f.parentId);

  const renderItem = (item: WorkspaceFile) => {
    const children = files.filter(f => f.parentId === item.id);
    const isFolder = item.type === 'folder';

    if (isFolder) {
      return (
        <div key={item.id} className="mb-0">
          <div draggable onDragStart={(e) => handleDragStart(e, item.id)} onDragOver={handleDragOver} onDrop={(e) => handleDrop(e, item.id, 'folder', item.parentId)} className="flex items-center py-1 transition-colors cursor-grab active:cursor-grabbing hover:bg-thread/40 -mx-1 px-1 rounded-sm">
            <Book size={14} className="text-lead shrink-0" strokeWidth={2} />
            <span className="ml-2 font-medium text-ink/90 text-[0.95rem] flex-1 select-none pointer-events-none">{item.name}</span>
          </div>
          {children.length > 0 && (
            <div className="ml-2 pl-3 border-l-[1.5px] border-thread/50 mt-0.5 flex flex-col mb-1">
              {children.map(child => renderItem(child))}
            </div>
          )}
        </div>
      );
    }

    return (
      <div key={item.id} draggable onDragStart={(e) => handleDragStart(e, item.id)} onDragOver={handleDragOver} onDrop={(e) => handleDrop(e, item.id, 'file', item.parentId)} onClick={(e) => { e.stopPropagation(); onSelectFile(item.id); }} className={`py-1 text-[0.9rem] flex items-center cursor-pointer -mx-1 px-1 rounded-sm select-none hover:bg-thread/40 transition-colors ${activeFileId === item.id ? 'text-accent font-medium' : 'text-ink/70 hover:text-ink/90'}`}>
        <div className={`w-1.5 h-1.5 rounded-full mr-2 shrink-0 ${activeFileId === item.id ? 'bg-accent' : 'bg-transparent'}`} />
        <span className="truncate flex-1 pointer-events-none">{item.name}</span>
      </div>
    );
  };

  return (
    <div className="flex flex-col min-h-[100px] pb-10" onDragOver={handleDragOver} onDrop={(e) => { e.preventDefault(); const draggedId = e.dataTransfer.getData('text/plain'); if (!draggedId) return; setFiles(prev => prev.map(f => f.id === draggedId ? { ...f, parentId: null } : f)); }}>
      {rootItems.map(item => renderItem(item))}
      <div className="h-6 w-full opacity-0" />
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

function applyEditCommands(content: string, edits: StudioEditCommand[]) {
  let next = content;
  const missed: StudioEditCommand[] = [];
  let applied = 0;

  for (const edit of edits) {
    const target = edit.target || '';
    const text = edit.text || '';
    if (edit.op === 'append') {
      next += text;
      applied += 1;
      continue;
    }
    if (edit.op === 'prepend') {
      next = text + next;
      applied += 1;
      continue;
    }
    const index = target ? next.indexOf(target) : -1;
    if (index < 0) {
      missed.push(edit);
      continue;
    }
    if (edit.op === 'replace') {
      next = next.slice(0, index) + text + next.slice(index + target.length);
      applied += 1;
    } else if (edit.op === 'delete') {
      next = next.slice(0, index) + next.slice(index + target.length);
      applied += 1;
    } else if (edit.op === 'insert_after') {
      next = next.slice(0, index + target.length) + text + next.slice(index + target.length);
      applied += 1;
    } else if (edit.op === 'insert_before') {
      next = next.slice(0, index) + text + next.slice(index);
      applied += 1;
    }
  }

  return { content: next, applied, missed };
}

export function Studio({ onBack }: { onBack: () => void }) {
  const [view, setView] = useState<'editor' | 'files'>('editor');
  const [logExpanded, setLogExpanded] = useState(false);
  const [aiOpen, setAiOpen] = useState(false);
  const [aiInstruction, setAiInstruction] = useState('');
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState('');
  const [editDraft, setEditDraft] = useState<{ summary: string; author: string; edits: StudioEditCommand[] } | null>(null);
  const [studioLocation, setStudioLocation] = useState<StudioLocation | null>(null);
  const lastManualEventRef = useRef(0);
  const { timeline, addTimelineEvent, activeNoteContent, setActiveNoteContent, files, setFiles, activeFileId, setActiveFileId, activeFile, createNote } = useStudioState();
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

  const aiUnits = useMemo(() => editDraft?.edits.reduce((sum, edit) => sum + wordUnits(edit.text || edit.target || ''), 0) || 0, [editDraft]);

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

  const runAiEdit = async () => {
    const instruction = aiInstruction.trim();
    if (!instruction || aiLoading) return;
    setAiLoading(true);
    setAiError('');
    setEditDraft(null);
    addTimelineEvent('asked AI to edit', 'user', 'Edit3', {
      kind: 'ai_edit_request',
      units: wordUnits(instruction),
      location: studioLocation || undefined,
    });
    const result = await requestStudioEdit({
      instruction,
      content: activeNoteContent,
      fileId: activeFileId,
      fileName: activeFileName,
      runtime: 'auto',
      location: studioLocation || undefined,
    });
    setAiLoading(false);
    if (!result.ok || !result.edits?.length) {
      setAiError(result.error || 'No edit commands returned');
      return;
    }
    const draft = { summary: result.summary || 'Prepared edit script', author: result.author || 'AI', edits: result.edits };
    setEditDraft(draft);
    addTimelineEvent('prepared edit script', 'ai', 'Sparkles', {
      kind: 'ai_edit_suggested',
      operations: result.edits.length,
      units: result.edits.reduce((sum, edit) => sum + wordUnits(edit.text || edit.target || ''), 0),
      summary: draft.summary,
      location: studioLocation || undefined,
    });
  };

  const applyAiEdit = () => {
    if (!editDraft) return;
    const result = applyEditCommands(activeNoteContent, editDraft.edits);
    if (!result.applied) {
      setAiError('No command matched the current document');
      return;
    }
    setActiveNoteContent(result.content);
    addTimelineEvent(`applied ${result.applied} AI edit${result.applied > 1 ? 's' : ''}`, 'ai', 'Sparkles', {
      kind: 'ai_edit_applied',
      operations: result.applied,
      units: aiUnits,
      summary: editDraft.summary,
      location: studioLocation || undefined,
    });
    setEditDraft(null);
    setAiInstruction('');
    setAiOpen(false);
    setAiError(result.missed.length ? `${result.missed.length} command did not match` : '');
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

  return (
    <div className="min-h-[100dvh] w-full bg-[#E5E1D8] sm:bg-[#1a1a1a] flex items-center justify-center font-sans overflow-hidden" style={studioTheme}>
      <div className="w-full h-[100dvh] sm:w-[412px] sm:h-[915px] bg-paper sm:rounded-[3rem] sm:shadow-[0_0_0_12px_#2a2a2a,0_10px_40px_rgba(0,0,0,0.5)] flex flex-col relative overflow-hidden outline outline-1 outline-thread/20">
        <div className="h-16 mt-8 flex items-center justify-between px-2 shrink-0 border-b border-thread/50 bg-paper z-10 transition-colors">
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

        <Editor content={activeNoteContent} onChange={setActiveNoteContent} onInteract={handleEditorInteract} onAiEdit={() => setAiOpen(true)} />

        <AnimatePresence>
          {aiOpen && (
            <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 12 }} className="absolute left-5 right-5 bottom-36 z-30 rounded-[22px] bg-paper border border-thread shadow-[0_12px_36px_rgba(74,68,61,0.16)] overflow-hidden">
              <div className="px-4 py-3 border-b border-thread/60 flex items-center justify-between">
                <div className="flex items-center gap-2 text-ink font-serif text-[1rem]"><Sparkles size={16} className="text-accent" />Edit Script</div>
                <button type="button" onClick={() => setAiOpen(false)} className="w-8 h-8 flex items-center justify-center rounded-lg active:bg-thread/60 text-ink/60 hover:text-ink transition-colors" aria-label="Close AI edit"><X size={17} /></button>
              </div>
              <div className="px-4 py-3 space-y-3">
                <textarea value={aiInstruction} onChange={(event) => setAiInstruction(event.target.value)} className="w-full min-h-[74px] resize-none rounded-[14px] border border-thread bg-card px-3 py-2 text-[0.92rem] text-ink outline-none placeholder:text-lead" placeholder="增删添改：例如把第二段改得更温柔，再在末尾添一句。" />
                {editDraft ? (
                  <div className="rounded-[14px] bg-thread/30 px-3 py-2 text-[0.82rem] text-ink/80">
                    <div className="font-medium text-ink mb-1">{editDraft.summary}</div>
                    <div className="space-y-1 max-h-28 overflow-y-auto pr-1">
                      {editDraft.edits.map((edit, index) => <div key={`${edit.op}-${index}`} className="font-mono text-[0.72rem] text-ink/60">{edit.op} {edit.target ? `· ${edit.target.slice(0, 32)}` : ''}</div>)}
                    </div>
                  </div>
                ) : null}
                {aiError ? <div className="text-[0.78rem] text-accent">{aiError}</div> : null}
                <div className="flex items-center justify-end gap-2">
                  <button type="button" onClick={runAiEdit} disabled={aiLoading || !aiInstruction.trim()} className="h-9 px-4 rounded-full bg-thread/70 text-ink text-[0.84rem] disabled:opacity-45">{aiLoading ? '生成中' : '生成'}</button>
                  <button type="button" onClick={applyAiEdit} disabled={!editDraft} className="h-9 px-4 rounded-full bg-accent text-paper text-[0.84rem] disabled:opacity-45">应用</button>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        <motion.button type="button" onClick={createNewNote} animate={{ y: logExpanded ? -450 : 0 }} transition={{ type: 'spring', damping: 25, stiffness: 200 }} className="absolute bottom-20 right-6 w-14 h-14 bg-accent text-paper rounded-full shadow-[0_4px_14px_rgba(217,148,119,0.3)] flex items-center justify-center hover:scale-95 active:scale-90 transition-transform z-10" aria-label="New note">
          <Plus size={24} strokeWidth={2.5} />
        </motion.button>
        <motion.button type="button" onClick={() => setAiOpen(true)} animate={{ y: logExpanded ? -450 : 0 }} transition={{ type: 'spring', damping: 25, stiffness: 200 }} className="absolute bottom-36 right-7 w-12 h-12 bg-paper text-accent border border-thread rounded-full shadow-[0_4px_14px_rgba(74,68,61,0.12)] flex items-center justify-center hover:scale-95 active:scale-90 transition-transform z-10" aria-label="AI edit">
          <Sparkles size={20} strokeWidth={2.3} />
        </motion.button>

        <motion.div initial={{ y: 'calc(100% - 64px)' }} animate={{ y: logExpanded ? 0 : 'calc(100% - 64px)' }} transition={{ type: 'spring', damping: 25, stiffness: 200 }} className="absolute bottom-0 left-0 right-0 h-[60%] sm:h-[450px] bg-paper rounded-t-[24px] border-t border-thread shadow-[0_-10px_40px_rgba(0,0,0,0.05)] flex flex-col z-20">
          <button type="button" onClick={() => setLogExpanded(!logExpanded)} className="h-[64px] shrink-0 w-full flex flex-col items-center justify-center relative active:bg-thread/20 rounded-t-[24px] transition-colors" aria-label="Toggle timeline">
            <div className="w-10 h-[4px] bg-thread/60 rounded-full mb-1" />
            <div className="font-serif text-[1rem] text-ink font-medium tracking-wide">Timeline</div>
          </button>

          <div className="flex-1 overflow-y-auto px-5 pb-8 pt-0">
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

              <motion.div drag="x" dragConstraints={{ left: -100, right: 0 }} dragElastic={0.1} onDragEnd={(_, { offset, velocity }) => { if (offset.x < -50 || velocity.x < -500) { setView('editor'); } }} initial={{ x: '-100%', opacity: 0 }} animate={{ x: 0, opacity: 1 }} exit={{ x: '-100%', opacity: 0 }} transition={{ type: 'spring', damping: 30, stiffness: 300 }} className="absolute top-0 bottom-0 left-0 w-[240px] bg-paper shadow-[4px_0_24px_rgba(0,0,0,0.08)] z-30 flex flex-col border-r border-thread/60">
                <div className="h-16 mt-8 px-4 flex items-center justify-between shrink-0 border-b border-thread/50">
                  <div className="font-serif text-lg font-medium text-ink">Studio</div>
                  <button type="button" onClick={() => setView('editor')} className="w-8 h-8 flex items-center justify-center rounded-lg active:bg-thread/60 text-ink/60 hover:text-ink transition-colors" aria-label="Close files">
                    <X size={18} />
                  </button>
                </div>

                <div className="flex-1 overflow-y-auto px-4 py-6">
                  <FileTree files={files} setFiles={setFiles} activeFileId={activeFileId} onSelectFile={(id) => { setActiveFileId(id); setView('editor'); }} />
                </div>
              </motion.div>
            </>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}