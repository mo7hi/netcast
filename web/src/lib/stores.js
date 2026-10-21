/** NetCast reactive stores */
import { writable } from 'svelte/store';

export const page = writable('wizard');       // wizard | preflight | dashboard | settings | portal
export const status = writable({ active: false, ssid: '', client_count: 0 });
export const clients = writable([]);
export const preflight = writable({ passed: false, checks: [] });
export const interfaces = writable({ wifi: [], ethernet: [], usb: [] });
export const config = writable({});
export const logs = writable([]);
export const theme = writable('default-dark');
export const error = writable('');
export const loading = writable(false);
