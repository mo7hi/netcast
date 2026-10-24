/** NetCast reactive stores */
import { writable } from 'svelte/store';

export const route = writable('hotspot');          // hotspot | devices | plugins | settings
export const authState = writable('checking');     // checking | ok | login | disabled
export const status = writable({ active: false, ssid: '', client_count: 0 });
export const clients = writable([]);
export const platform = writable({ os: 'linux', assistant: false, capabilities: {}, steps: [] });
export const interfaces = writable({ wifi: [], ethernet: [], usb: [], cellular: [], vpn: [] });
export const theme = writable('system');
export const notice = writable(null);              // { kind: 'info' | 'error', text }
export const draft = writable(null);               // hotspot form, kept while navigating

export function say(text, kind = 'info') {
  notice.set({ kind, text });
  if (kind === 'info') setTimeout(() => notice.update((n) => (n && n.text === text ? null : n)), 5000);
}
