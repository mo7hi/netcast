<script>
  import { onMount } from 'svelte';
  import { page, status, clients, logs, error } from './lib/stores.js';
  import { fetchToken, getStatus, connectWS } from './lib/api.js';
  import { loadSavedTheme } from './lib/theme.js';
  import Wizard from './pages/Wizard.svelte';
  import Preflight from './pages/Preflight.svelte';
  import Dashboard from './pages/Dashboard.svelte';
  import Settings from './pages/Settings.svelte';
  import Portal from './pages/Portal.svelte';

  let currentPage;
  page.subscribe(v => currentPage = v);

  onMount(async () => {
    loadSavedTheme();
    await fetchToken();
    try {
      const s = await getStatus();
      status.set(s);
      if (s.active) page.set('dashboard');
    } catch {}

    connectWS((event) => {
      if (event.type === 'status_update') status.set(event.data);
      else if (event.type === 'client_connected') {
        clients.update(c => [...c, event.data]);
      }
      else if (event.type === 'client_disconnected') {
        clients.update(c => c.filter(x => x.mac !== event.data.mac));
      }
      else if (event.type === 'log_message') {
        logs.update(l => [...l.slice(-99), event.data]);
      }
      else if (event.type === 'error') {
        error.set(event.data.message);
      }
    });
  });

  function navigate(p) { page.set(p); }

  const isPortal = window.location.pathname.startsWith('/portal');
</script>

{#if isPortal}
  <Portal />
{:else}
  <div class="app-shell">
    <nav class="sidebar">
      <div class="logo">
        <div class="logo-icon">⚡</div>
        <span class="logo-text">NetCast</span>
      </div>
      <div class="nav-items">
        <button class="nav-btn" class:active={currentPage === 'wizard'} on:click={() => navigate('wizard')}>
          <span class="nav-icon">🧭</span><span>Wizard</span>
        </button>
        <button class="nav-btn" class:active={currentPage === 'dashboard'} on:click={() => navigate('dashboard')}>
          <span class="nav-icon">📊</span><span>Dashboard</span>
        </button>
        <button class="nav-btn" class:active={currentPage === 'settings'} on:click={() => navigate('settings')}>
          <span class="nav-icon">⚙️</span><span>Settings</span>
        </button>
      </div>
      <div class="nav-footer">
        <span class="version">v0.1.0</span>
      </div>
    </nav>

    <main class="content">
      {#if $error}
        <div class="toast error" on:click={() => error.set('')}>
          <span class="toast-icon">✗</span> {$error}
        </div>
      {/if}

      {#if currentPage === 'wizard'}
        <Wizard />
      {:else if currentPage === 'preflight'}
        <Preflight />
      {:else if currentPage === 'dashboard'}
        <Dashboard />
      {:else if currentPage === 'settings'}
        <Settings />
      {/if}
    </main>
  </div>
{/if}

<style>
  :global(*) {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
  }

  :global(body) {
    font-family: var(--rn-font-family, 'Inter', system-ui, sans-serif);
    background: var(--rn-bg-primary, #0f0f23);
    color: var(--rn-text-primary, #e0e0e0);
    min-height: 100vh;
    overflow-x: hidden;
  }

  .app-shell {
    display: flex;
    min-height: 100vh;
  }

  .sidebar {
    width: 220px;
    background: var(--rn-bg-secondary, #1a1a3e);
    border-right: 1px solid rgba(255,255,255,0.06);
    display: flex;
    flex-direction: column;
    padding: 1.5rem 0;
    position: fixed;
    top: 0;
    left: 0;
    bottom: 0;
    z-index: 100;
  }

  .logo {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0 1.25rem 1.5rem;
    border-bottom: 1px solid rgba(255,255,255,0.06);
    margin-bottom: 1rem;
  }

  .logo-icon {
    font-size: 1.75rem;
    filter: drop-shadow(0 0 8px var(--rn-accent, #00d4ff));
  }

  .logo-text {
    font-size: 1.25rem;
    font-weight: 800;
    letter-spacing: -0.5px;
    background: linear-gradient(135deg, var(--rn-accent, #00d4ff), #a855f7);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }

  .nav-items {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    padding: 0 0.75rem;
  }

  .nav-btn {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.75rem 1rem;
    background: transparent;
    border: none;
    border-radius: var(--rn-border-radius, 12px);
    color: var(--rn-text-secondary, #8888aa);
    font-size: 0.9rem;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.2s ease;
    width: 100%;
    text-align: left;
  }

  .nav-btn:hover {
    background: rgba(255,255,255,0.05);
    color: var(--rn-text-primary, #e0e0e0);
  }

  .nav-btn.active {
    background: rgba(var(--rn-accent-rgb, 0, 212, 255), 0.12);
    color: var(--rn-accent, #00d4ff);
    font-weight: 600;
  }

  .nav-icon { font-size: 1.1rem; }

  .nav-footer {
    padding: 1rem 1.25rem;
    border-top: 1px solid rgba(255,255,255,0.06);
  }

  .version {
    font-size: 0.75rem;
    color: var(--rn-text-secondary, #8888aa);
    opacity: 0.5;
  }

  .content {
    flex: 1;
    margin-left: 220px;
    padding: 2rem 2.5rem;
    min-height: 100vh;
  }

  .toast {
    position: fixed;
    top: 1.5rem;
    right: 1.5rem;
    padding: 0.875rem 1.25rem;
    border-radius: var(--rn-border-radius, 12px);
    font-size: 0.875rem;
    font-weight: 500;
    z-index: 1000;
    cursor: pointer;
    animation: slideIn 0.3s ease;
    box-shadow: 0 8px 32px rgba(0,0,0,0.3);
  }

  .toast.error {
    background: rgba(255, 68, 68, 0.15);
    border: 1px solid rgba(255, 68, 68, 0.3);
    color: #ff6b6b;
  }

  .toast-icon { margin-right: 0.5rem; font-weight: 700; }

  @keyframes slideIn {
    from { transform: translateX(100%); opacity: 0; }
    to { transform: translateX(0); opacity: 1; }
  }

  @media (max-width: 768px) {
    .sidebar { width: 64px; }
    .sidebar span:not(.nav-icon):not(.logo-icon) { display: none; }
    .content { margin-left: 64px; padding: 1.5rem; }
    .logo { padding: 0 0.75rem 1rem; justify-content: center; }
    .nav-btn { justify-content: center; padding: 0.75rem; }
  }
</style>
