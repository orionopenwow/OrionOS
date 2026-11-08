/**
 * The slim status strip (bottom right): Orion's health light, how many things wait for your OK, the camera light
 * while the camera is on, and Freeze (tap twice). The hot mic keeps its own light at the top while it listens.
 * Everything else lives in the menu.
 */
import { ShieldCheck } from 'lucide-react';
import SafetyLight from '../shell/SafetyLight';
import CameraLight from '../presence/CameraLight';
import { openOrion } from '../../lib/menu';

const pill: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 6, padding: '6px 11px', borderRadius: 999,
  background: 'rgba(26,31,39,0.85)', border: '1px solid #2F3744', color: '#DDE5F0',
  font: '500 12px/1.2 Figtree, system-ui, sans-serif', backdropFilter: 'blur(8px)', cursor: 'pointer',
};

export default function StatusStrip({ waiting, camera = true }: { waiting: number; camera?: boolean }) {
  return (
    <div
      role="region"
      aria-label="Status"
      className="fixed bottom-4 right-4 z-[58] flex items-center gap-1.5"
      onClick={(e) => e.stopPropagation()}
    >
      {camera && <CameraLight inline />}
      {waiting > 0 && (
        <button type="button" style={{ ...pill, borderColor: '#FFC66B66' }} onClick={() => openOrion('queue')}
          aria-label={`${waiting} waiting for your OK`} title="Waiting for your OK">
          <ShieldCheck size={13} color="#FFC66B" />
          {waiting > 99 ? '99+' : waiting}
        </button>
      )}
      <SafetyLight inline />
    </div>
  );
}
