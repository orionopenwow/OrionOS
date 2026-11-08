/**
 * The Test Center's flow logic, kept free of React so `node --test` can run it (tests/testflow.test.ts):
 * which items just passed (for the chime and the auto-advance), the group progress pills at the top, and which
 * buttons the Next card shows.
 */
export type TestStatus = 'todo' | 'running' | 'pass' | 'fail' | 'skip';

export interface FlowItem {
  id: string;
  title: string;
  kind: 'auto' | 'manual';
  status: TestStatus;
  where?: 'pc' | 'phone' | 'both';
  do?: string | null;
  ask?: string | null;
  armed?: boolean;
  by_phone?: boolean;
}

export interface FlowGroup {
  id: string;
  title: string;
  items: FlowItem[];
}

export interface Pill {
  id: string;
  title: string;
  pass: number;
  fail: number;
  total: number;
  /** every item passed or skipped */
  done: boolean;
  /** something in it is being checked or watched right now */
  busy: boolean;
}

/** id -> status for every item, to compare one view with the next. */
export function statusMap(groups: FlowGroup[]): Record<string, TestStatus> {
  const out: Record<string, TestStatus> = {};
  for (const g of groups) for (const i of g.items) out[i.id] = i.status;
  return out;
}

/** Items that were not passed before and are now (an empty `prev` means the first load: nothing "just" passed). */
export function newlyPassed(prev: Record<string, TestStatus>, groups: FlowGroup[]): FlowItem[] {
  if (!Object.keys(prev).length) return [];
  const out: FlowItem[] = [];
  for (const g of groups) for (const i of g.items) if (i.status === 'pass' && prev[i.id] !== undefined && prev[i.id] !== 'pass') out.push(i);
  return out;
}

export function groupPills(groups: FlowGroup[]): Pill[] {
  return groups.map((g) => {
    const pass = g.items.filter((i) => i.status === 'pass').length;
    const skip = g.items.filter((i) => i.status === 'skip').length;
    const fail = g.items.filter((i) => i.status === 'fail').length;
    const busy = g.items.some((i) => i.status === 'running' || (i.status === 'todo' && !!i.armed));
    return { id: g.id, title: g.title, pass, fail, total: g.items.length, done: pass + skip === g.items.length, busy };
  });
}

export interface NextButtons {
  /** "ask": Yes / No are the main buttons; "do": the "Do it for me" button; "wait": nothing to press on the PC */
  primary: 'ask' | 'do' | 'wait' | 'none';
  doLabel: string;
  /** Done and It didn't work (secondary); auto checks report their own result */
  showDoneFail: boolean;
}

export function nextButtons(item: FlowItem): NextButtons {
  if (item.ask) return { primary: 'ask', doLabel: '', showDoneFail: false };
  if (item.do) return { primary: 'do', doLabel: item.do, showDoneFail: item.kind === 'manual' };
  if (item.by_phone || item.where === 'phone') return { primary: 'wait', doLabel: '', showDoneFail: item.kind === 'manual' };
  return { primary: 'none', doLabel: '', showDoneFail: item.kind === 'manual' };
}

/** Poll faster while something is being checked or watched, so an item ticks itself off within a second or two. */
export function pollMs(running: boolean, next: FlowItem | null): number {
  if (running) return 1200;
  if (next && (next.armed || next.status === 'running')) return 1500;
  return 4000;
}
