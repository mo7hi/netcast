<script>
  import { onMount } from 'svelte';
  import { route, authState, status, clients, platform, interfaces, notice } from './lib/stores.js';
  import { fetchToken, authStatus, getStatus, getClients, getPlatform, getInterfaces, getUiTabs, connectWS } from './lib/api.js';
  import { loadSavedTheme, loadCustomSkins } from './lib/theme.js';
  import Logo from './components/Logo.svelte';
  import Icon from './components/Icon.svelte';
  import Hotspot from './pages/Hotspot.svelte';
  import Devices from './pages/Devices.svelte';
  import Plugins from './pages/Plugins.svelte';
  import Settings from './pages/Settings.svelte';
  import Login from './pages/Login.svelte';

  const NAV = [
    { id: 'hotspot', label: 'Hotspot', icon: 'signal' },
    { id: 'devices', label: 'Devices', icon: 'devices' },
    { id: 'plugins', label: 'Plugins', icon: 'plugins' },
    { id: 'settings', label: 'Settings', icon: 'settings' },
  ];

  let stopWS = () => {};
  let poll;
  let tabs = [];
  let pluginTabs;

  function syncRoute() {
    const id = location.hash.replace(/^#\/?/, '').split('?')[0];
    $route = NAV.some((n) => n.id === id) ? id : 'hotspot';
  }

  async function refresh() {
    try {
      $status = await getStatus();
      $clients = $status.active ? await getClients() : [];
    } catch { /* transient */ }
  }

  async function init() {
    try { $platform = await getPlatform(); } catch { /* older daemon */ }
    try { $interfaces = await getInterfaces(); } catch { /* shown by checks */ }
    await refresh();
    try { tabs = await getUiTabs(); } catch { /* optional */ }
    loadCustomSkins();
    clearInterval(poll);
    poll = setInterval(refresh, 5000);
    stopWS();
    stopWS = connectWS((ev) => {
      if (ev.type === 'status_update') $status = ev.data;
      else if (ev.type === 'client_connected') $clients = $clients.some((c) => c.mac === ev.data.mac) ? $clients : [...$clients, ev.data];
      else if (ev.type === 'client_disconnected') $clients = $clients.filter((c) => c.mac !== ev.data.mac);
    });
  }

  async function boot() {
    if (await fetchToken()) { $authState = 'ok'; await init(); return; }
    try { $authState = (await authStatus()).admin_password_set ? 'login' : 'disabled'; }
    catch { $authState = 'disabled'; }
  }

  function signedIn() { $authState = 'ok'; init(); }

  onMount(() => {
    loadSavedTheme();
    syncRoute();
    window.addEventListener('hashchange', syncRoute);
    boot();
    return () => { window.removeEventListener('hashchange', syncRoute); clearInterval(poll); stopWS(); };
  });

  $: title = NAV.find((n) => n.id === $route)?.label;
  $: if (title) document.title = `${title} · NetCast`;
  $: summary = $status.active ? `On · ${$status.client_count || 0} device${($status.client_count || 0) === 1 ? '' : 's'}` : 'Off';
</script>

<a class="skip-link" href="#main">Skip to content</a>

{#if $authState === 'checking'}
  <main class="center" id="main"><p class="small">Connecting…</p></main>
{:else if $authState === 'login'}
  <Login onsuccess={signedIn} />
{:else if $authState === 'disabled'}
  <main class="center" id="main">
    <div class="panel narrow">
      <div class="brand"><Logo /> NetCast</div>
      <h1 class="t">Remote control is off</h1>
      <p>To manage NetCast from this device, open it once on the computer running the hotspot, go to <strong>Settings → Remote control</strong>, and set an admin password.</p>
    </div>
  </main>
{:else}
  <div class="shell">
    <aside class="sidebar">
      <div class="brand"><Logo /> NetCast</div>
      <nav class="nav" aria-label="Main">
        {#each NAV as n}
          <a href="#/{n.id}" aria-current={$route === n.id ? 'page' : undefined}><Icon name={n.icon} /> {n.label}</a>
        {/each}
      </nav>
      <div class="sidebar-foot">
        <span class="chip" class:ok={$status.active} role="status">{summary}</span>
      </div>
    </aside>

    <div>
      <header class="topbar">
        <div class="brand"><Logo size={30} /> NetCast</div>
        <span class="chip" class:ok={$status.active} role="status">{summary}</span>
      </header>

      <main class="main" id="main" tabindex="-1">
        {#if $route === 'hotspot'}<Hotspot />
        {:else if $route === 'devices'}<Devices />
        {:else if $route === 'plugins'}<Plugins />
        {:else}<Settings />{/if}
      </main>
    </div>

    <nav class="tabbar" aria-label="Main">
      {#each NAV as n}
        <a href="#/{n.id}" aria-current={$route === n.id ? 'page' : undefined}><Icon name={n.icon} size={24} /> {n.label}</a>
      {/each}
    </nav>
  </div>
{/if}

<div class="notice-wrap" aria-live="polite">
  {#if $notice}
    <div class="notice" role={$notice.kind === 'error' ? 'alert' : 'status'}>
      <span>{$notice.text}</span>
      <button on:click={() => ($notice = null)}>Dismiss</button>
    </div>
  {/if}
</div>

<style>
  .center { min-height: 100dvh; display: grid; place-items: center; padding: 1rem; }
  .narrow { width: min(100%, 30rem); }
  .t { font-size: 1.75rem; letter-spacing: -0.03em; }
</style>
