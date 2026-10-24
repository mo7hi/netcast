/** Theme loader: four built-in looks plus skins registered by plugins. */
import { writable } from 'svelte/store';
import { theme } from './stores.js';
import { getSkins, getSkinCss } from './api.js';

export const BUILTIN = [
  { id: 'system', name: 'Match my device', desc: 'Light or dark following your system, and high contrast if your system asks for it.' },
  { id: 'light', name: 'Light', desc: 'Best in bright rooms and sunlight.' },
  { id: 'dark', name: 'Dark', desc: 'Easier on the eyes at night.' },
  { id: 'contrast', name: 'High contrast', desc: 'Black and white, heavier lines, larger text.' },
];

export const themeList = writable(BUILTIN);

const KEY = 'netcast-theme';
const valid = (id, list) => list.some((t) => t.id === id) || /^custom-[a-z0-9_-]+$/.test(id);

export function setTheme(id) {
  document.documentElement.dataset.theme = id;
  theme.set(id);
  try { localStorage.setItem(KEY, id); } catch { /* private mode */ }
}

export function loadSavedTheme() {
  let saved = 'system';
  try { saved = localStorage.getItem(KEY) || 'system'; } catch { /* ignore */ }
  setTheme(valid(saved, BUILTIN) ? saved : 'system');
}

/** Fetch plugin skins from the daemon and inject their CSS. Call after sign-in. */
export async function loadCustomSkins() {
  try {
    const { skins } = await getSkins();
    const custom = skins.filter((s) => !s.builtin);
    for (const s of custom) {
      if (document.querySelector(`style[data-skin="${s.id}"]`)) continue;
      const el = document.createElement('style');
      el.dataset.skin = s.id;
      el.textContent = await getSkinCss(s.id);
      document.head.appendChild(el);
    }
    themeList.set([...BUILTIN, ...custom.map((s) => ({ id: s.id, name: s.name, desc: 'Added by a plugin.' }))]);
    loadSavedTheme();
  } catch { /* skins are optional */ }
}
