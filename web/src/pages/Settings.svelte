<script>
  import { theme, config as configStore } from '../lib/stores.js';
  import { getConfig, updateConfig } from '../lib/api.js';
  import { setTheme, getThemes } from '../lib/theme.js';
  import { onMount } from 'svelte';

  let cfg = {};
  let saved = false;
  let currentTheme;
  theme.subscribe(v => currentTheme = v);
  const themes = getThemes();

  const themeLabels = {
    'default-dark': { name: 'Midnight', icon: '🌙', desc: 'Deep blue with cyan glow' },
    'panic': { name: 'Panic Mode', icon: '🚨', desc: 'Max contrast, giant text' },
    'cyberpunk': { name: 'Cyberpunk', icon: '💚', desc: 'Terminal green, hacker vibes' },
    'retro-95': { name: 'Retro 95', icon: '🖥️', desc: 'Classic Windows aesthetic' },
  };

  onMount(async () => {
    try { cfg = await getConfig(); configStore.set(cfg); } catch {}
  });

  async function save() {
    try {
      await updateConfig(cfg);
      saved = true;
      setTimeout(() => saved = false, 2000);
    } catch (e) { alert('Failed: ' + e.message); }
  }
</script>

<div class="settings">
  <h1>Settings</h1>

  <section class="section">
    <h2>🎨 Theme</h2>
    <div class="theme-grid">
      {#each themes as t}
        <button
          class="theme-card"
          class:selected={currentTheme === t}
          on:click={() => setTheme(t)}
        >
          <span class="theme-icon">{themeLabels[t]?.icon || '🎨'}</span>
          <span class="theme-name">{themeLabels[t]?.name || t}</span>
          <span class="theme-desc">{themeLabels[t]?.desc || ''}</span>
          {#if currentTheme === t}
            <span class="theme-active">Active</span>
          {/if}
        </button>
      {/each}
    </div>
  </section>

  <section class="section">
    <h2>📡 Default Hotspot</h2>
    <div class="form-row">
      <div class="form-group">
        <label for="s-ssid">Default SSID</label>
        <input id="s-ssid" type="text" bind:value={cfg.ssid} />
      </div>
      <div class="form-group">
        <label for="s-pass">Default Password</label>
        <input id="s-pass" type="text" bind:value={cfg.password} />
      </div>
    </div>
    <div class="form-row">
      <div class="form-group">
        <label for="s-band">Default Band</label>
        <select id="s-band" bind:value={cfg.band}>
          <option value="bg">2.4 GHz</option>
          <option value="a">5 GHz</option>
        </select>
      </div>
      <div class="form-group">
        <label for="s-channel">Channel</label>
        <input id="s-channel" type="number" bind:value={cfg.channel} min="0" max="196" />
        <small>0 = auto</small>
      </div>
    </div>
  </section>

  <section class="section">
    <h2>⚖️ Fair Share Mode</h2>
    <label class="toggle-row">
      <input type="checkbox" bind:checked={cfg.fair_share_enabled} />
      <span class="toggle-slider"></span>
      <span>Enable per-client bandwidth sharing</span>
    </label>
    {#if cfg.fair_share_enabled}
      <div class="form-group" style="margin-top: 0.75rem;">
        <label for="s-bw">Total Bandwidth (Kbps)</label>
        <input id="s-bw" type="number" bind:value={cfg.total_bandwidth_kbps} min="0" />
        <small>0 = auto-detect</small>
      </div>
    {/if}
  </section>

  <section class="section">
    <h2>🚪 Captive Portal</h2>
    <label class="toggle-row">
      <input type="checkbox" bind:checked={cfg.captive_portal_enabled} />
      <span class="toggle-slider"></span>
      <span>Show landing page to connecting devices</span>
    </label>
    {#if cfg.captive_portal_enabled}
      <div class="form-group" style="margin-top: 0.75rem;">
        <label for="s-portal-msg">Portal Message</label>
        <textarea id="s-portal-msg" bind:value={cfg.captive_portal_message} rows="2"></textarea>
      </div>
    {/if}
  </section>

  <div class="save-bar">
    <button class="save-btn" on:click={save}>
      {saved ? '✓ Saved!' : '💾 Save Settings'}
    </button>
  </div>
</div>

<style>
  .settings { max-width: 720px; margin: 0 auto; }
  .settings h1 { font-size: 1.75rem; font-weight: 800; margin-bottom: 2rem; }

  .section {
    margin-bottom: 2rem; padding: 1.5rem;
    background: var(--rn-bg-card); border: 1px solid var(--rn-border);
    border-radius: var(--rn-border-radius);
  }
  .section h2 { font-size: 1rem; font-weight: 700; margin-bottom: 1rem; }

  .theme-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 0.75rem; }
  .theme-card {
    display: flex; flex-direction: column; align-items: center; gap: 0.25rem;
    padding: 1.25rem 1rem; background: var(--rn-bg-input);
    border: 2px solid var(--rn-border); border-radius: var(--rn-border-radius-sm);
    cursor: pointer; transition: all var(--rn-transition); text-align: center;
    color: var(--rn-text-primary); position: relative;
  }
  .theme-card:hover { border-color: var(--rn-accent); transform: translateY(-1px); }
  .theme-card.selected { border-color: var(--rn-accent); background: rgba(var(--rn-accent-rgb), 0.06); box-shadow: 0 0 20px var(--rn-accent-glow); }
  .theme-icon { font-size: 1.75rem; }
  .theme-name { font-weight: 700; font-size: 0.9rem; }
  .theme-desc { font-size: 0.72rem; color: var(--rn-text-secondary); }
  .theme-active {
    position: absolute; top: 6px; right: 8px;
    font-size: 0.6rem; font-weight: 800; text-transform: uppercase; letter-spacing: 0.5px;
    color: var(--rn-accent); background: rgba(var(--rn-accent-rgb), 0.12);
    padding: 0.15rem 0.4rem; border-radius: 4px;
  }

  .form-row { display: flex; gap: 1rem; }
  .form-group { flex: 1; margin-bottom: 0.75rem; }
  .form-group label { display: block; font-size: 0.8rem; font-weight: 600; color: var(--rn-text-secondary); margin-bottom: 0.3rem; }
  .form-group input, .form-group select, .form-group textarea {
    width: 100%; padding: 0.6rem 0.75rem;
    background: var(--rn-bg-input); border: 1px solid var(--rn-border);
    border-radius: var(--rn-border-radius-sm); color: var(--rn-text-primary);
    font-size: 0.9rem; font-family: inherit; transition: border-color var(--rn-transition);
  }
  .form-group input:focus, .form-group select:focus, .form-group textarea:focus {
    outline: none; border-color: var(--rn-accent); box-shadow: 0 0 0 3px var(--rn-accent-glow);
  }
  .form-group small { font-size: 0.72rem; color: var(--rn-text-muted); }
  .form-group textarea { resize: vertical; }

  .toggle-row {
    display: flex; align-items: center; gap: 0.75rem; cursor: pointer;
    font-size: 0.9rem; color: var(--rn-text-primary);
  }
  .toggle-row input { display: none; }
  .toggle-slider {
    width: 42px; height: 22px; background: var(--rn-bg-input);
    border: 1px solid var(--rn-border); border-radius: 11px;
    position: relative; transition: all var(--rn-transition); flex-shrink: 0;
  }
  .toggle-slider::after {
    content: ''; position: absolute; top: 2px; left: 2px;
    width: 16px; height: 16px; background: var(--rn-text-muted);
    border-radius: 50%; transition: all var(--rn-transition);
  }
  .toggle-row input:checked + .toggle-slider { background: rgba(var(--rn-accent-rgb), 0.2); border-color: var(--rn-accent); }
  .toggle-row input:checked + .toggle-slider::after { transform: translateX(20px); background: var(--rn-accent); }

  .save-bar { position: sticky; bottom: 1rem; text-align: center; }
  .save-btn {
    padding: 0.75rem 2.5rem; background: linear-gradient(135deg, var(--rn-accent), #a855f7);
    border: none; border-radius: var(--rn-border-radius); color: #fff;
    font-size: 1rem; font-weight: 700; cursor: pointer; transition: all var(--rn-transition);
    box-shadow: 0 4px 20px var(--rn-accent-glow);
  }
  .save-btn:hover { transform: translateY(-2px); box-shadow: 0 8px 32px var(--rn-accent-glow); }

  @media (max-width: 600px) {
    .theme-grid { grid-template-columns: 1fr; }
    .form-row { flex-direction: column; }
  }
</style>
