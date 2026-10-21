<script>
  import { onMount, onDestroy } from 'svelte';
  import { status, clients, page, loading } from '../lib/stores.js';
  import { getStatus, getClients, getQR, stopHotspot } from '../lib/api.js';

  let qrSvg = '';
  let pollTimer;
  let uptime = '0:00';
  let uptimeStart = Date.now();

  onMount(async () => {
    await refresh();
    pollTimer = setInterval(refresh, 5000);
    uptimeTick();
  });

  onDestroy(() => { clearInterval(pollTimer); });

  async function refresh() {
    try {
      const [s, c] = await Promise.all([getStatus(), getClients()]);
      status.set(s);
      clients.set(c);
      if (!qrSvg && s.active) qrSvg = await getQR();
    } catch {}
  }

  function uptimeTick() {
    setInterval(() => {
      const diff = Math.floor((Date.now() - uptimeStart) / 1000);
      const h = Math.floor(diff / 3600);
      const m = Math.floor((diff % 3600) / 60);
      const s = diff % 60;
      uptime = h ? `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}` : `${m}:${String(s).padStart(2,'0')}`;
    }, 1000);
  }

  async function handleStop() {
    if (!confirm('Stop the hotspot? All connected devices will lose internet.')) return;
    loading.set(true);
    try { await stopHotspot(); page.set('wizard'); }
    catch (e) { alert(e.message); }
    finally { loading.set(false); }
  }

  function formatBytes(b) {
    if (!b) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(b) / Math.log(1024));
    return (b / Math.pow(1024, i)).toFixed(1) + ' ' + units[i];
  }
</script>

<div class="dashboard">
  <div class="top-bar">
    <div class="status-badge" class:active={$status.active}>
      <span class="pulse"></span>
      {$status.active ? 'ACTIVE' : 'INACTIVE'}
    </div>
    <span class="uptime">⏱ {uptime}</span>
    <button class="stop-btn" on:click={handleStop} disabled={!$status.active || $loading}>
      {$loading ? '...' : '■ Stop'}
    </button>
  </div>

  <div class="stats-grid">
    <div class="stat-card accent">
      <span class="stat-value">{$status.client_count || 0}</span>
      <span class="stat-label">Clients</span>
    </div>
    <div class="stat-card">
      <span class="stat-value">{$status.ssid || '—'}</span>
      <span class="stat-label">SSID</span>
    </div>
    <div class="stat-card">
      <span class="stat-value">{$status.band === 'a' ? '5 GHz' : '2.4 GHz'}</span>
      <span class="stat-label">Band</span>
    </div>
    <div class="stat-card">
      <span class="stat-value">Ch {$status.channel || '—'}</span>
      <span class="stat-label">Channel</span>
    </div>
  </div>

  <div class="panels">
    <div class="panel qr-panel">
      <h2>Connect via QR</h2>
      <div class="qr-container">
        {#if qrSvg}
          {@html qrSvg}
        {:else}
          <div class="qr-placeholder">QR code will appear when hotspot is active</div>
        {/if}
      </div>
      <p class="qr-hint">Scan with your phone camera to connect instantly</p>
    </div>

    <div class="panel clients-panel">
      <h2>Connected Devices <span class="badge">{$clients.length}</span></h2>
      {#if $clients.length === 0}
        <div class="empty-state">
          <span class="empty-icon">📡</span>
          <p>Waiting for devices to connect...</p>
        </div>
      {:else}
        <div class="client-list">
          {#each $clients as client}
            <div class="client-row">
              <div class="client-info">
                <span class="client-name">{client.hostname || 'Unknown Device'}</span>
                <span class="client-ip">{client.ip}</span>
              </div>
              <div class="client-stats">
                <span class="client-mac">{client.mac}</span>
                <span class="traffic">↑{formatBytes(client.tx_bytes)} ↓{formatBytes(client.rx_bytes)}</span>
              </div>
            </div>
          {/each}
        </div>
      {/if}
    </div>
  </div>

  <div class="panel info-panel">
    <h2>Network Details</h2>
    <div class="info-grid">
      <div class="info-item"><span class="info-label">Source</span><span class="info-value">{$status.source_interface || '—'}</span></div>
      <div class="info-item"><span class="info-label">AP Interface</span><span class="info-value">{$status.ap_interface || '—'}</span></div>
      <div class="info-item"><span class="info-label">Gateway IP</span><span class="info-value">{$status.gateway_ip || '—'}</span></div>
      <div class="info-item"><span class="info-label">Password</span><span class="info-value password">{$status.password || '—'}</span></div>
    </div>
  </div>
</div>

<style>
  .dashboard { max-width: 960px; margin: 0 auto; }

  .top-bar {
    display: flex; align-items: center; gap: 1rem; margin-bottom: 2rem;
    padding: 1rem 1.5rem; background: var(--rn-bg-card); border-radius: var(--rn-border-radius);
    border: 1px solid var(--rn-border);
  }
  .status-badge {
    display: flex; align-items: center; gap: 0.5rem;
    font-weight: 800; font-size: 0.85rem; letter-spacing: 1px;
    color: var(--rn-danger); text-transform: uppercase;
  }
  .status-badge.active { color: var(--rn-success); }
  .pulse {
    width: 8px; height: 8px; border-radius: 50%;
    background: currentColor; animation: pulse 2s ease infinite;
  }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
  .uptime { flex: 1; color: var(--rn-text-secondary); font-size: 0.9rem; font-variant-numeric: tabular-nums; }
  .stop-btn {
    padding: 0.5rem 1.25rem; background: var(--rn-danger-bg); border: 1px solid rgba(255,68,102,0.3);
    border-radius: var(--rn-border-radius-sm); color: var(--rn-danger); font-weight: 700;
    cursor: pointer; transition: all var(--rn-transition);
  }
  .stop-btn:hover:not(:disabled) { background: var(--rn-danger); color: #fff; }
  .stop-btn:disabled { opacity: 0.4; cursor: not-allowed; }

  .stats-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; margin-bottom: 1.5rem; }
  .stat-card {
    padding: 1.25rem; text-align: center;
    background: var(--rn-bg-card); border: 1px solid var(--rn-border);
    border-radius: var(--rn-border-radius); transition: all var(--rn-transition);
  }
  .stat-card:hover { transform: translateY(-2px); box-shadow: var(--rn-shadow); }
  .stat-card.accent { border-color: rgba(var(--rn-accent-rgb), 0.2); background: rgba(var(--rn-accent-rgb), 0.04); }
  .stat-value { display: block; font-size: 1.5rem; font-weight: 800; margin-bottom: 0.25rem; }
  .stat-card.accent .stat-value { color: var(--rn-accent); }
  .stat-label { font-size: 0.75rem; color: var(--rn-text-secondary); text-transform: uppercase; letter-spacing: 0.5px; }

  .panels { display: grid; grid-template-columns: 280px 1fr; gap: 1.5rem; margin-bottom: 1.5rem; }
  .panel {
    padding: 1.5rem; background: var(--rn-bg-card);
    border: 1px solid var(--rn-border); border-radius: var(--rn-border-radius);
  }
  .panel h2 { font-size: 1rem; font-weight: 700; margin-bottom: 1rem; display: flex; align-items: center; gap: 0.5rem; }
  .badge {
    font-size: 0.7rem; padding: 0.15rem 0.5rem; background: rgba(var(--rn-accent-rgb), 0.15);
    color: var(--rn-accent); border-radius: 999px; font-weight: 700;
  }

  .qr-container { display: flex; justify-content: center; margin-bottom: 0.75rem; }
  .qr-container :global(svg) { width: 200px; height: 200px; border-radius: 8px; }
  .qr-placeholder { width: 200px; height: 200px; display: flex; align-items: center; justify-content: center;
    background: var(--rn-bg-input); border-radius: 8px; color: var(--rn-text-muted); font-size: 0.8rem; text-align: center; padding: 1rem; }
  .qr-hint { text-align: center; font-size: 0.78rem; color: var(--rn-text-secondary); }

  .empty-state { text-align: center; padding: 2rem; color: var(--rn-text-muted); }
  .empty-icon { font-size: 2rem; display: block; margin-bottom: 0.5rem; animation: pulse 2s ease infinite; }
  .client-list { display: flex; flex-direction: column; gap: 0.5rem; max-height: 300px; overflow-y: auto; }
  .client-row {
    display: flex; justify-content: space-between; align-items: center;
    padding: 0.75rem 1rem; background: var(--rn-bg-input);
    border-radius: var(--rn-border-radius-sm); transition: all var(--rn-transition);
  }
  .client-row:hover { background: var(--rn-bg-card-hover); }
  .client-info { display: flex; flex-direction: column; }
  .client-name { font-weight: 600; font-size: 0.9rem; }
  .client-ip { font-size: 0.78rem; color: var(--rn-text-secondary); font-variant-numeric: tabular-nums; }
  .client-stats { text-align: right; display: flex; flex-direction: column; }
  .client-mac { font-size: 0.72rem; color: var(--rn-text-muted); font-family: monospace; }
  .traffic { font-size: 0.75rem; color: var(--rn-text-secondary); }

  .info-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; }
  .info-item { display: flex; flex-direction: column; gap: 0.2rem; }
  .info-label { font-size: 0.75rem; color: var(--rn-text-muted); text-transform: uppercase; letter-spacing: 0.5px; }
  .info-value { font-weight: 600; font-size: 0.9rem; }
  .password { font-family: monospace; letter-spacing: 0.5px; }

  @media (max-width: 768px) {
    .stats-grid { grid-template-columns: repeat(2, 1fr); }
    .panels { grid-template-columns: 1fr; }
  }
</style>
