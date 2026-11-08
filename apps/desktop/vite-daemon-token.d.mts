import type { Plugin } from 'vite';

export function daemonToken(): string;
export function daemonTokenForDevPage(port: number): Plugin;
