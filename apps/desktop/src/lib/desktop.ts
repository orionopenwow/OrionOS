/** Desktop-shell (Tauri) bridges. Every function is a safe no-op in a plain browser (npm run dev:web). */
import { isTauri } from './tauri';

export interface DaemonStatus {
  stage:
    | 'locating_python'
    | 'python_missing'
    | 'creating_env'
    | 'installing'
    | 'starting'
    | 'running'
    | 'external'
    | 'restarting'
    | 'stopped'
    | 'error';
  message: string;
  detail?: string | null;
  progress?: number | null;
}

export interface AppInfo {
  version: string;
  log_dir: string;
  packaged: boolean;
}

async function invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T | undefined> {
  if (!isTauri()) return undefined;
  const { invoke } = await import('@tauri-apps/api/core');
  return invoke<T>(cmd, args);
}

export const desktop = {
  daemonStatus: () => invoke<DaemonStatus>('daemon_status'),
  restartDaemon: () => invoke<void>('restart_daemon'),
  stopDaemon: () => invoke<void>('stop_daemon'),
  openLogs: () => invoke<void>('open_logs'),
  installPython: () => invoke<void>('install_python'),
  appInfo: () => invoke<AppInfo>('app_info'),
  hideWindow: () => invoke<void>('hide_window'),
};

export async function onDesktopEvent<T>(name: string, cb: (payload: T) => void): Promise<() => void> {
  if (!isTauri()) return () => {};
  const { listen } = await import('@tauri-apps/api/event');
  return listen<T>(name, (e) => cb(e.payload));
}
