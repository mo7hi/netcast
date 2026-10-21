/** NetCast theme/skin loader */
import { theme } from './stores.js';

const THEMES = ['default-dark', 'panic', 'cyberpunk', 'retro-95'];

export function setTheme(name) {
  if (THEMES.includes(name) || name.startsWith('custom-')) {
    document.documentElement.dataset.theme = name;
    theme.set(name);
    localStorage.setItem('netcast-theme', name);
  }
}

export function loadSavedTheme() {
  const saved = localStorage.getItem('netcast-theme') || 'default-dark';
  setTheme(saved);
}

export function getThemes() {
  return THEMES;
}
