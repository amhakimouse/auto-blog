import { useState, useEffect } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useQuery, useMutation } from '@tanstack/react-query';
import { useEditor, EditorContent } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Placeholder from '@tiptap/extension-placeholder';
import LinkExtension from '@tiptap/extension-link';
import Image from '@tiptap/extension-image';
import { articles } from '@/lib/api';
import { Card, Button, Input, Loading } from '@/components/ui';
import { ArrowLeft, Save, Code } from 'lucide-react';

export function ArticleEditPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [title, setTitle] = useState('');
  const [showHtml, setShowHtml] = useState(false);
  const [htmlContent, setHtmlContent] = useState('');

  const { data: article, isLoading } = useQuery({
    queryKey: ['article', id],
    queryFn: () => articles.get(id!),
    enabled: !!id,
  });

  const editor = useEditor({
    extensions: [
      StarterKit,
      Placeholder.configure({
        placeholder: 'Write your article content here...',
      }),
      LinkExtension.configure({
        openOnClick: false,
      }),
      Image,
    ],
    content: '',
    editorProps: {
      attributes: {
        class: 'prose prose-lg dark:prose-invert max-w-none min-h-[400px] focus:outline-none',
      },
    },
  });

  useEffect(() => {
    if (article) {
      setTitle(article.title);
      editor?.commands.setContent(article.html_content);
    }
  }, [article, editor]);

  useEffect(() => {
    if (editor) {
      setHtmlContent(editor.getHTML());
    }
  }, [editor]);

  const saveMutation = useMutation({
    mutationFn: () => articles.update(id!, { title, html_content: editor?.getHTML() || '' }),
    onSuccess: () => {
      navigate(`/articles/${id}`);
    },
  });

  if (isLoading) {
    return <Loading text="Loading article..." />;
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <Link to={`/articles/${id}`}>
          <Button variant="ghost" size="sm" leftIcon={<ArrowLeft className="w-4 h-4" />}>
            Back
          </Button>
        </Link>
        <div className="flex-1">
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Edit Article</h1>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            leftIcon={<Code className="w-4 h-4" />}
            onClick={() => setShowHtml(!showHtml)}
          >
            {showHtml ? 'Editor' : 'HTML'}
          </Button>
          <Button
            leftIcon={<Save className="w-4 h-4" />}
            onClick={() => saveMutation.mutate()}
            isLoading={saveMutation.isPending}
          >
            Save
          </Button>
        </div>
      </div>

      {/* Title */}
      <Card>
        <Input
          label="Title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Article title"
          className="text-xl font-semibold"
        />
      </Card>

      {/* Editor */}
      <Card>
        {showHtml ? (
          <div>
            <label className="label">HTML Content</label>
            <textarea
              value={htmlContent}
              onChange={(e) => {
                setHtmlContent(e.target.value);
                editor?.commands.setContent(e.target.value);
              }}
              className="input font-mono text-sm min-h-[500px]"
              rows={20}
            />
          </div>
        ) : (
          <div className="border border-gray-200 dark:border-dark-border rounded-lg overflow-hidden">
            {/* Toolbar */}
            <div className="flex items-center gap-1 p-2 border-b border-gray-200 dark:border-dark-border bg-gray-50 dark:bg-dark-card">
              <button
                onClick={() => editor?.chain().focus().toggleBold().run()}
                className={`p-2 rounded hover:bg-gray-200 dark:hover:bg-gray-700 ${
                  editor?.isActive('bold') ? 'bg-gray-200 dark:bg-gray-700' : ''
                }`}
              >
                <strong className="font-bold">B</strong>
              </button>
              <button
                onClick={() => editor?.chain().focus().toggleItalic().run()}
                className={`p-2 rounded hover:bg-gray-200 dark:hover:bg-gray-700 ${
                  editor?.isActive('italic') ? 'bg-gray-200 dark:bg-gray-700' : ''
                }`}
              >
                <em>I</em>
              </button>
              <button
                onClick={() => editor?.chain().focus().toggleStrike().run()}
                className={`p-2 rounded hover:bg-gray-200 dark:hover:bg-gray-700 ${
                  editor?.isActive('strike') ? 'bg-gray-200 dark:bg-gray-700' : ''
                }`}
              >
                <s>S</s>
              </button>
              <div className="w-px h-6 bg-gray-300 dark:bg-gray-600 mx-1" />
              <button
                onClick={() => editor?.chain().focus().toggleHeading({ level: 1 }).run()}
                className={`p-2 rounded hover:bg-gray-200 dark:hover:bg-gray-700 ${
                  editor?.isActive('heading', { level: 1 }) ? 'bg-gray-200 dark:bg-gray-700' : ''
                }`}
              >
                H1
              </button>
              <button
                onClick={() => editor?.chain().focus().toggleHeading({ level: 2 }).run()}
                className={`p-2 rounded hover:bg-gray-200 dark:hover:bg-gray-700 ${
                  editor?.isActive('heading', { level: 2 }) ? 'bg-gray-200 dark:bg-gray-700' : ''
                }`}
              >
                H2
              </button>
              <button
                onClick={() => editor?.chain().focus().toggleHeading({ level: 3 }).run()}
                className={`p-2 rounded hover:bg-gray-200 dark:hover:bg-gray-700 ${
                  editor?.isActive('heading', { level: 3 }) ? 'bg-gray-200 dark:bg-gray-700' : ''
                }`}
              >
                H3
              </button>
              <div className="w-px h-6 bg-gray-300 dark:bg-gray-600 mx-1" />
              <button
                onClick={() => editor?.chain().focus().toggleBulletList().run()}
                className={`p-2 rounded hover:bg-gray-200 dark:hover:bg-gray-700 ${
                  editor?.isActive('bulletList') ? 'bg-gray-200 dark:bg-gray-700' : ''
                }`}
              >
                • List
              </button>
              <button
                onClick={() => editor?.chain().focus().toggleOrderedList().run()}
                className={`p-2 rounded hover:bg-gray-200 dark:hover:bg-gray-700 ${
                  editor?.isActive('orderedList') ? 'bg-gray-200 dark:bg-gray-700' : ''
                }`}
              >
                1. List
              </button>
              <div className="w-px h-6 bg-gray-300 dark:bg-gray-600 mx-1" />
              <button
                onClick={() => {
                  const url = window.prompt('Enter URL');
                  if (url) {
                    editor?.chain().focus().setLink({ href: url }).run();
                  }
                }}
                className={`p-2 rounded hover:bg-gray-200 dark:hover:bg-gray-700 ${
                  editor?.isActive('link') ? 'bg-gray-200 dark:bg-gray-700' : ''
                }`}
              >
                Link
              </button>
              <button
                onClick={() => {
                  const url = window.prompt('Enter image URL');
                  if (url) {
                    editor?.chain().focus().setImage({ src: url }).run();
                  }
                }}
                className="p-2 rounded hover:bg-gray-200 dark:hover:bg-gray-700"
              >
                Image
              </button>
              <button
                onClick={() => editor?.chain().focus().toggleCodeBlock().run()}
                className={`p-2 rounded hover:bg-gray-200 dark:hover:bg-gray-700 ${
                  editor?.isActive('codeBlock') ? 'bg-gray-200 dark:bg-gray-700' : ''
                }`}
              >
                Code
              </button>
            </div>
            <EditorContent editor={editor} className="p-4" />
          </div>
        )}
      </Card>
    </div>
  );
}