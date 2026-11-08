import { useEffect, useMemo, useRef, useState } from 'react';
import { Search } from 'lucide-react';
import type { MemoryRecord, MemoryResultsEvent, MemorySource } from '@orion/shared-types';
import { colors, sourceColors } from '../theme/colors';

interface Props {
  memories: MemoryRecord[];
  searchResults: MemoryResultsEvent | null;
  onSearch: (query: string) => void;
  connected: boolean;
}

interface SimNode {
  id: string;
  label: string;
  source: MemorySource;
  hub: boolean;
  content: string;
  ts: number;
  x: number;
  y: number;
  vx: number;
  vy: number;
  r: number;
}

const SOURCES: MemorySource[] = ['ocr', 'browser', 'voice', 'discord', 'user', 'consolidated'];
const STOP = new Set(['the', 'and', 'for', 'that', 'with', 'this', 'from', 'have', 'your', 'https', 'http', 'www', 'com']);
const MAX_NODES = 48;

function keywords(text: string): Set<string> {
  const words = text.toLowerCase().match(/[a-z][a-z0-9_]{4,}/g) ?? [];
  return new Set(words.filter((w) => !STOP.has(w)).slice(0, 40));
}

/** Lightweight force-directed memory graph on canvas (no D3 dependency). */
export default function BrainMemoryMap({ memories, searchResults, onSearch, connected }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const nodesRef = useRef<Map<string, SimNode>>(new Map());
  const linksRef = useRef<[string, string, number][]>([]);
  const hoverRef = useRef<SimNode | null>(null);
  const highlightRef = useRef<Set<string>>(new Set());
  const [hover, setHover] = useState<SimNode | null>(null);
  const [query, setQuery] = useState('');

  const recent = useMemo(() => memories.slice(0, MAX_NODES), [memories]);

  useEffect(() => {
    highlightRef.current = new Set(searchResults?.results.map((r) => r.id) ?? []);
  }, [searchResults]);

  // Sync graph topology with memory records.
  useEffect(() => {
    const nodes = nodesRef.current;
    const canvas = canvasRef.current;
    const w = canvas?.clientWidth ?? 400;
    const h = canvas?.clientHeight ?? 260;

    SOURCES.forEach((s, i) => {
      if (!nodes.has(`hub:${s}`)) {
        const a = (i / SOURCES.length) * Math.PI * 2;
        nodes.set(`hub:${s}`, {
          id: `hub:${s}`, label: s, source: s, hub: true, content: '', ts: 0,
          x: w / 2 + Math.cos(a) * w * 0.28, y: h / 2 + Math.sin(a) * h * 0.28, vx: 0, vy: 0, r: 7,
        });
      }
    });

    const keep = new Set(recent.map((m) => m.id));
    for (const id of nodes.keys()) if (!id.startsWith('hub:') && !keep.has(id)) nodes.delete(id);
    for (const m of recent) {
      if (nodes.has(m.id)) continue;
      const hub = nodes.get(`hub:${m.source}`)!;
      nodes.set(m.id, {
        id: m.id, label: m.content.split('\n')[0].slice(0, 60), source: m.source, hub: false,
        content: m.content.slice(0, 280), ts: m.timestamp,
        x: hub.x + (Math.random() - 0.5) * 40, y: hub.y + (Math.random() - 0.5) * 40, vx: 0, vy: 0, r: 3.5,
      });
    }

    const links: [string, string, number][] = recent.map((m) => [m.id, `hub:${m.source}`, 1]);
    const kw = recent.map((m) => ({ id: m.id, k: keywords(m.content) }));
    for (let i = 0; i < kw.length; i++) {
      for (let j = i + 1; j < kw.length; j++) {
        let shared = 0;
        for (const word of kw[i].k) if (kw[j].k.has(word)) shared++;
        if (shared >= 3) links.push([kw[i].id, kw[j].id, Math.min(1, shared / 8)]);
      }
    }
    linksRef.current = links;
  }, [recent]);

  // Physics + render loop.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d')!;
    let raf = 0;

    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      canvas.width = canvas.clientWidth * dpr;
      canvas.height = canvas.clientHeight * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(canvas);

    const step = (t: number) => {
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      const nodes = [...nodesRef.current.values()];
      const byId = nodesRef.current;

      for (let i = 0; i < nodes.length; i++) {
        const a = nodes[i];
        for (let j = i + 1; j < nodes.length; j++) {
          const b = nodes[j];
          const dx = a.x - b.x;
          const dy = a.y - b.y;
          const d2 = Math.max(dx * dx + dy * dy, 25);
          const f = (a.hub && b.hub ? 1800 : 260) / d2;
          a.vx += dx * f * 0.05; a.vy += dy * f * 0.05;
          b.vx -= dx * f * 0.05; b.vy -= dy * f * 0.05;
        }
      }
      for (const [s, d, strength] of linksRef.current) {
        const a = byId.get(s);
        const b = byId.get(d);
        if (!a || !b) continue;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const target = b.hub ? 46 : 70;
        const f = ((dist - target) / dist) * 0.012 * strength;
        a.vx += dx * f; a.vy += dy * f;
        b.vx -= dx * f; b.vy -= dy * f;
      }
      for (const n of nodes) {
        n.vx += (w / 2 - n.x) * 0.0015;
        n.vy += (h / 2 - n.y) * 0.0015;
        n.vx *= 0.86; n.vy *= 0.86;
        n.x = Math.min(w - 8, Math.max(8, n.x + n.vx));
        n.y = Math.min(h - 8, Math.max(8, n.y + n.vy));
      }

      ctx.clearRect(0, 0, w, h);
      for (const [s, d, strength] of linksRef.current) {
        const a = byId.get(s);
        const b = byId.get(d);
        if (!a || !b) continue;
        ctx.strokeStyle = b.hub ? `${sourceColors[a.source]}30` : `rgba(0,229,255,${0.15 + strength * 0.35})`;
        ctx.lineWidth = b.hub ? 0.6 : 0.8 + strength;
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
      }
      const highlight = highlightRef.current;
      const now = Date.now() / 1000;
      for (const n of nodes) {
        const c = sourceColors[n.source];
        const fresh = !n.hub && now - n.ts < 10;
        const lit = highlight.has(n.id) || hoverRef.current?.id === n.id;
        const r = n.r + (fresh ? 1.5 + Math.sin(t / 150) : 0) + (lit ? 2 : 0);
        ctx.shadowColor = c;
        ctx.shadowBlur = n.hub || lit ? 14 : 6;
        ctx.fillStyle = n.hub ? colors.cosmos : c;
        ctx.strokeStyle = c;
        ctx.lineWidth = n.hub ? 1.6 : 0;
        ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, Math.PI * 2); ctx.fill();
        if (n.hub) ctx.stroke();
        if (lit) {
          ctx.strokeStyle = colors.starlight; ctx.lineWidth = 1;
          ctx.beginPath(); ctx.arc(n.x, n.y, r + 4, 0, Math.PI * 2); ctx.stroke();
        }
        ctx.shadowBlur = 0;
        if (n.hub) {
          ctx.fillStyle = colors.dust;
          ctx.font = '9px Orbitron, sans-serif';
          ctx.textAlign = 'center';
          ctx.fillText(n.label.toUpperCase(), n.x, n.y - 12);
        }
      }
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => { cancelAnimationFrame(raf); ro.disconnect(); };
  }, []);

  const onMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    let best: SimNode | null = null;
    let bestD = 144;
    for (const n of nodesRef.current.values()) {
      if (n.hub) continue;
      const d = (n.x - x) ** 2 + (n.y - y) ** 2;
      if (d < bestD) { best = n; bestD = d; }
    }
    hoverRef.current = best;
    setHover(best);
  };

  return (
    <div className="glass flex h-full flex-col p-4">
      <div className="mb-2 flex items-center justify-between">
        <div className="panel-title">Brain & Memory Map</div>
        <div className="text-[11px] text-dust">{memories.length} nodes</div>
      </div>
      <form
        className="mb-2 flex items-center gap-2 rounded-lg border border-glass bg-white/[0.03] px-2.5 py-1.5"
        onSubmit={(e) => { e.preventDefault(); if (query.trim()) onSearch(query.trim()); }}
      >
        <Search size={13} className="text-dust" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          disabled={!connected}
          placeholder="Semantic memory search…"
          className="selectable w-full bg-transparent text-xs text-starlight placeholder:text-dust focus:outline-none"
        />
      </form>
      <div className="relative min-h-0 flex-1">
        <canvas
          ref={canvasRef}
          className="h-full w-full cursor-crosshair"
          onMouseMove={onMove}
          onMouseLeave={() => { hoverRef.current = null; setHover(null); }}
        />
        {memories.length === 0 && (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center text-xs text-dust">
            No memories yet
          </div>
        )}
        {hover && (
          <div className="glass-strong pointer-events-none absolute bottom-2 left-2 right-2 rounded-lg p-2 text-[11.5px]">
            <div className="mb-0.5 flex justify-between">
              <span style={{ color: sourceColors[hover.source] }}>{hover.source}</span>
              <span className="text-dust">{new Date(hover.ts * 1000).toLocaleTimeString()}</span>
            </div>
            <div className="line-clamp-3 text-starlight/80">{hover.content}</div>
          </div>
        )}
      </div>
      {searchResults && searchResults.results.length > 0 && (
        <ul className="mt-2 max-h-28 space-y-1 overflow-y-auto">
          {searchResults.results.map((r) => (
            <li key={r.id} className="selectable flex gap-2 text-[11.5px]">
              <span className="font-mono text-pulse">{(r.score ?? 0).toFixed(2)}</span>
              <span className="truncate text-dust">{r.content.replace(/\s+/g, ' ')}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
