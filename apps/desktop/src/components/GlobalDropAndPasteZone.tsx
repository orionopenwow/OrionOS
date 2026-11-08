import { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { UploadCloud, FileText, Image as ImageIcon, Video } from 'lucide-react';

interface Props {
  onIngestFile: (fileName: string, fileType: string, contentPreview?: string) => void;
  onPasteText: (text: string) => void;
  /** Feature Bible asset pipeline: images / audio / video / 3D models get action pills instead of an LLM prompt. */
  onAssetFile?: (file: File) => boolean;
  children: React.ReactNode;
}

export default function GlobalDropAndPasteZone({
  onIngestFile,
  onPasteText,
  onAssetFile,
  children,
}: Props) {
  const [isDragging, setIsDragging] = useState(false);
  const [dragType, setDragType] = useState<'image' | 'video' | 'doc' | 'general'>('general');

  useEffect(() => {
    let dragCounter = 0;

    const handleDragEnter = (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      dragCounter++;
      if (e.dataTransfer && e.dataTransfer.items && e.dataTransfer.items.length > 0) {
        setIsDragging(true);
        const item = e.dataTransfer.items[0];
        if (item.type.startsWith('image/')) setDragType('image');
        else if (item.type.startsWith('video/')) setDragType('video');
        else setDragType('doc');
      }
    };

    const handleDragOver = (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
    };

    const handleDragLeave = (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      dragCounter--;
      if (dragCounter <= 0) {
        setIsDragging(false);
        dragCounter = 0;
      }
    };

    const handleDrop = (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setIsDragging(false);
      dragCounter = 0;

      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
        const file = e.dataTransfer.files[0];
        if (onAssetFile?.(file)) return;
        const isText = file.type.startsWith('text/') || file.name.match(/\.(txt|md|py|js|tsx|ts|json|html|css)$/i);
        
        if (isText) {
          const reader = new FileReader();
          reader.onload = (event) => {
            const content = event.target?.result as string;
            onIngestFile(file.name, file.type || 'text/plain', content?.slice(0, 4000));
          };
          reader.readAsText(file);
        } else {
          onIngestFile(file.name, file.type || 'media/binary');
        }
      }
    };

    const handlePaste = (e: ClipboardEvent) => {
      // Don't intercept if user is typing in an active input or textarea
      const target = e.target as HTMLElement;
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA') return;

      const clipboardData = e.clipboardData;
      if (!clipboardData) return;

      // Check for files first (e.g. copied image)
      if (clipboardData.files && clipboardData.files.length > 0) {
        const file = clipboardData.files[0];
        if (onAssetFile?.(file)) return;
        onIngestFile(file.name, file.type || 'image/png');
        return;
      }

      // Check for text
      const pastedText = clipboardData.getData('text');
      if (pastedText && pastedText.trim()) {
        onPasteText(pastedText.trim());
      }
    };

    window.addEventListener('dragenter', handleDragEnter);
    window.addEventListener('dragover', handleDragOver);
    window.addEventListener('dragleave', handleDragLeave);
    window.addEventListener('drop', handleDrop);
    window.addEventListener('paste', handlePaste);

    return () => {
      window.removeEventListener('dragenter', handleDragEnter);
      window.removeEventListener('dragover', handleDragOver);
      window.removeEventListener('dragleave', handleDragLeave);
      window.removeEventListener('drop', handleDrop);
      window.removeEventListener('paste', handlePaste);
    };
  }, [onIngestFile, onPasteText, onAssetFile]);

  return (
    <div className="relative h-full w-full">
      {children}

      {/* Full-Screen Cosmic Drop Aperture Target */}
      <AnimatePresence>
        {isDragging && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="pointer-events-none fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-xl"
          >
            <div className="relative flex flex-col items-center justify-center p-12 text-center">
              
              {/* Pulsing Concentric Aperture Rings */}
              <div className="relative h-44 w-44 flex items-center justify-center mb-6">
                <span className="absolute inset-0 rounded-full border-2 border-[#00E5FF]/60 animate-ping opacity-60" />
                <span className="absolute inset-3 rounded-full border-2 border-[#C084FC]/60 animate-spin" style={{ animationDuration: '8s' }} />
                <div className="h-28 w-28 rounded-full bg-gradient-to-tr from-[#00E5FF]/30 to-[#C084FC]/30 border border-[#00E5FF] flex items-center justify-center shadow-[0_0_50px_rgba(0,229,255,0.6)]">
                  {dragType === 'image' && <ImageIcon size={44} className="text-[#00E5FF]" />}
                  {dragType === 'video' && <Video size={44} className="text-[#C084FC]" />}
                  {dragType === 'doc' && <FileText size={44} className="text-[#3DFFA8]" />}
                  {dragType === 'general' && <UploadCloud size={44} className="text-[#00E5FF]" />}
                </div>
              </div>

              <h2 className="font-display text-xl font-bold tracking-widest text-starlight mb-1">
                FEED MEDIA INTO ORION
              </h2>
              <p className="text-xs font-mono text-dust max-w-sm">
                Drop images, video clips, or code files onto Orion for instant visual comprehension and task analysis.
              </p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
