import { useCallback } from "react";
import { useEditor, EditorContent } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Placeholder from "@tiptap/extension-placeholder";
import { TextStyle } from "@tiptap/extension-text-style";
import { Color } from "@tiptap/extension-color";
import { Extension } from "@tiptap/core";
import LinkExtension from "@tiptap/extension-link";

/**
 * Author annotation extension — marks paragraphs with data-author="human"|"ai".
 * New paragraphs typed by the user are automatically tagged "human".
 */
const AuthorExtension = Extension.create({
  name: "author",
  addGlobalAttributes() {
    return [
      {
        types: [
          "paragraph",
          "heading",
          "blockquote",
          "codeBlock",
          "bulletList",
        ],
        attributes: {
          author: {
            default: null,
            parseHTML: (element) => element.getAttribute("data-author"),
            renderHTML: (attributes) => {
              if (!attributes.author) return {};
              return { "data-author": attributes.author };
            },
          },
        },
      },
    ];
  },
});

interface NoteEditorProps {
  content: string;
  onChange: (html: string) => void;
}

export function NoteEditor({ content, onChange }: NoteEditorProps) {
  const handleUpdate = useCallback(
    ({ editor }: { editor: ReturnType<typeof useEditor> }) => {
      if (editor) {
        onChange(editor.getHTML());
      }
    },
    [onChange]
  );

  const editor = useEditor({
    extensions: [
      StarterKit,
      AuthorExtension,
      TextStyle,
      Color,
      LinkExtension.configure({
        openOnClick: false,
        HTMLAttributes: {
          class: "text-accent underline decoration-accent/40",
        },
      }),
      Placeholder.configure({
        placeholder: "开始写作，或向 AI 提问...",
      }),
    ],
    content,
    onUpdate: handleUpdate,
    editorProps: {
      attributes: {
        class: "prose mx-auto focus:outline-none min-h-[300px] max-w-[720px]",
      },
      handleKeyDown: (_view, event) => {
        // Auto-tag new paragraphs as human-authored on Enter
        if (event.key === "Enter" && !event.shiftKey) {
          // TipTap handles the Enter; we just mark the next paragraph
          setTimeout(() => {
            const ed = editor;
            if (!ed) return;
            const { $anchor } = ed.state.selection;
            const node = $anchor.parent;
            if (
              node &&
              node.type.name === "paragraph" &&
              !node.attrs.author
            ) {
              ed.commands.updateAttributes("paragraph", { author: "human" });
            }
          }, 0);
        }
        return false;
      },
    },
  });

  if (!editor) return null;

  return (
    <div className="flex-1 overflow-y-auto px-8 py-6">
      <EditorContent editor={editor} className="h-full pb-32" />
    </div>
  );
}
