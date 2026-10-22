/** NetCast theme/skin loader (built-in themes + plugin-registered skins) */
import { writable, get } from 'svelte/store';
import { theme } from './stores.js';
import { getSkins, getSkinCss } from './api.js';

const BUILTIN = ['default-dark', 'panic', 'cyberpunk', 'retro-95'];

export const themeList = writable([...BUILTIN]);

export function setTheme(name) {
  if (BUILTIN.includes(name) || /^custom-[a-z0-9_-]+$/.test(name)) {
    document.documentElement.dataset.theme = name;
    theme.set(name);
    localStorage.setItem('netcast-theme', name);
  }
}

export function loadSavedTheme() {
  setTheme(localStorage.getItem('netcast-theme') || 'default-dark');
}

export function getThemes() {
  return get(themeList);
}

/** Fetch plugin skins from the daemon and inject their CSS. Call after login. */
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
    themeList.set([...BUILTIN, ...custom.map((s) => s.id)]);
    loadSavedTheme();
  } catch { /* skins are optional */ }
}
