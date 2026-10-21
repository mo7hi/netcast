<script>
  import { page, interfaces, loading, config } from '../lib/stores.js';
  import { getInterfaces, startHotspot, getPreflight } from '../lib/api.js';
  import { onMount } from 'svelte';

  let sourceType = 'auto';
  let ssid = 'NetCast';
  let password = '';
  let band = 'bg';
  let ifaces = { wifi: [], ethernet: [], usb: [] };
  let selectedSource = '';
  let step = 1;

  onMount(async () => {
    try {
      ifaces = await getInterfaces();
      interfaces.set(ifaces);
      if (ifaces.ethernet.length) { sourceType = 'ethernet'; selectedSource = ifaces.ethernet[0]; }
      else if (ifaces.usb.length) { sourceType = 'usb'; selectedSource = ifaces.usb[0]; }
    } catch {}
  });

  function generatePassword() {
    const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789';
    const buf = new Uint32Array(12);
    crypto.getRandomValues(buf);
    password = Array.from(buf, (n) => chars[n % chars.length]).join('');
  }

  async function handleStart() {
    loading.set(true);
    try {
      const cfg = { ssid, password, band, source_type: sourceType, source_interface: selectedSource };
      const pf = await getPreflight();
      if (!pf.passed) {
        page.set('preflight');
        return;
      }
      await startHotspot(cfg);
      page.set('dashboard');
    } catch (e) {
      alert('Failed: ' + e.message);
    } finally {
      loading.set(false);
    }
  }

  const sources = [
    { id: 'ethernet', icon: '🔌', label: 'Ethernet Cable', desc: 'Wired connection to router or modem' },
    { id: 'wifi', icon: '📶', label: 'Wi-Fi Network', desc: 'Repeat an existing Wi-Fi (speed reduced ~50%)' },
    { id: 'usb', icon: '📱', label: 'USB Tethering', desc: 'Share phone data via USB cable' },
    { id: 'pppoe', icon: '🏢', label: 'PPPoE (ISP Modem)', desc: 'Dial directly into ISP modem' },
  ];
</script>

<div class="wizard">
  <div class="hero">
    <h1>Where is the internet coming from?</h1>
    <p class="subtitle">NetCast will share it as a Wi-Fi hotspot for all your devices.</p>
  </div>

  {#if step === 1}
    <div class="source-grid">
      {#each sources as src}
        <button
          class="source-card"
          class:selected={sourceType === src.id}
          on:click={() => { sourceType = src.id; selectedSource = ifaces[src.id]?.[0] || ''; step = 2; }}
          class:disabled={src.id === 'usb' && !ifaces.usb.length}
        >
          <span class="source-icon">{src.icon}</span>
          <span class="source-label">{src.label}</span>
          <span class="source-desc">{src.desc}</span>
          {#if src.id === 'usb' && !ifaces.usb.length}
            <span class="source-unavail">No USB device detected</span>
          {/if}
        </button>
      {/each}
    </div>
  {:else}
    <div class="config-panel">
      <div class="config-header">
        <button class="back-btn" on:click={() => step = 1}>← Back</button>
        <h2>Configure your hotspot</h2>
      </div>

      <div class="form-group">
        <label for="ssid">Network Name (SSID)</label>
        <input id="ssid" type="text" bind:value={ssid} maxlength="32" placeholder="NetCast" />
      </div>

      <div class="form-group">
        <label for="pass">Password</label>
        <div class="input-row">
          <input id="pass" type="text" bind:value={password} minlength="8" maxlength="63" placeholder="Min 8 characters" />
          <button class="gen-btn" on:click={generatePassword}>🎲 Generate</button>
        </div>
      </div>

      <div class="form-group">
        <label>Band</label>
        <div class="radio-group">
          <label class="radio-card" class:selected={band === 'bg'}>
            <input type="radio" bind:group={band} value="bg" />
            <span>2.4 GHz</span>
            <small>Better range, more compatible</small>
          </label>
          <label class="radio-card" class:selected={band === 'a'}>
            <input type="radio" bind:group={band} value="a" />
            <span>5 GHz</span>
            <small>Faster speed, shorter range</small>
          </label>
        </div>
      </div>

      <button class="rescue-btn" on:click={handleStart} disabled={!password || password.length < 8 || $loading}>
        {#if $loading}
          <span class="spinner"></span> Starting...
        {:else}
          ⚡ Start Hotspot
        {/if}
      </button>
    </div>
  {/if}
</div>

<style>
  .wizard { max-width: 720px; margin: 0 auto; }
  .hero { text-align: center; margin-bottom: 2.5rem; }
  .hero h1 {
    font-size: 2rem;
    font-weight: 800;
    letter-spacing: -1px;
    background: linear-gradient(135deg, var(--rn-accent), #a855f7);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin-bottom: 0.5rem;
  }
  .subtitle { color: var(--rn-text-secondary); font-size: 1.05rem; }

  .source-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1rem;
  }

  .source-card {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.5rem;
    padding: 2rem 1.5rem;
    background: var(--rn-bg-card);
    border: 2px solid var(--rn-border);
    border-radius: var(--rn-border-radius);
    cursor: pointer;
    transition: all var(--rn-transition);
    text-align: center;
    color: var(--rn-text-primary);
  }
  .source-card:hover { background: var(--rn-bg-card-hover); border-color: var(--rn-accent); transform: translateY(-2px); box-shadow: var(--rn-shadow); }
  .source-card.selected { border-color: var(--rn-accent); background: rgba(var(--rn-accent-rgb), 0.08); box-shadow: 0 0 24px var(--rn-accent-glow); }
  .source-card.disabled { opacity: 0.4; pointer-events: none; }
  .source-icon { font-size: 2.5rem; }
  .source-label { font-weight: 700; font-size: 1.05rem; }
  .source-desc { font-size: 0.8rem; color: var(--rn-text-secondary); }
  .source-unavail { font-size: 0.75rem; color: var(--rn-danger); margin-top: 0.25rem; }

  .config-panel {
    background: var(--rn-bg-card);
    border-radius: var(--rn-border-radius);
    padding: 2rem;
    border: 1px solid var(--rn-border);
  }
  .config-header { display: flex; align-items: center; gap: 1rem; margin-bottom: 1.5rem; }
  .config-header h2 { font-size: 1.25rem; font-weight: 700; }
  .back-btn {
    background: transparent; border: 1px solid var(--rn-border); color: var(--rn-text-secondary);
    padding: 0.4rem 0.75rem; border-radius: var(--rn-border-radius-sm); cursor: pointer;
    transition: all var(--rn-transition);
  }
  .back-btn:hover { border-color: var(--rn-accent); color: var(--rn-accent); }

  .form-group { margin-bottom: 1.25rem; }
  .form-group label { display: block; font-size: 0.85rem; font-weight: 600; color: var(--rn-text-secondary); margin-bottom: 0.4rem; }
  .form-group input[type="text"] {
    width: 100%; padding: 0.75rem 1rem;
    background: var(--rn-bg-input); border: 1px solid var(--rn-border);
    border-radius: var(--rn-border-radius-sm); color: var(--rn-text-primary);
    font-size: 0.95rem; font-family: inherit; transition: border-color var(--rn-transition);
  }
  .form-group input:focus { outline: none; border-color: var(--rn-accent); box-shadow: 0 0 0 3px var(--rn-accent-glow); }

  .input-row { display: flex; gap: 0.5rem; }
  .input-row input { flex: 1; }
  .gen-btn {
    padding: 0.75rem 1rem; background: var(--rn-bg-secondary); border: 1px solid var(--rn-border);
    border-radius: var(--rn-border-radius-sm); color: var(--rn-text-primary); cursor: pointer;
    font-size: 0.85rem; white-space: nowrap; transition: all var(--rn-transition);
  }
  .gen-btn:hover { border-color: var(--rn-accent); color: var(--rn-accent); }

  .radio-group { display: flex; gap: 0.75rem; }
  .radio-card {
    flex: 1; display: flex; flex-direction: column; align-items: center; gap: 0.25rem;
    padding: 1rem; background: var(--rn-bg-input); border: 2px solid var(--rn-border);
    border-radius: var(--rn-border-radius-sm); cursor: pointer; transition: all var(--rn-transition); text-align: center;
  }
  .radio-card:hover { border-color: var(--rn-accent); }
  .radio-card.selected { border-color: var(--rn-accent); background: rgba(var(--rn-accent-rgb), 0.06); }
  .radio-card input { display: none; }
  .radio-card span { font-weight: 600; color: var(--rn-text-primary); }
  .radio-card small { font-size: 0.75rem; color: var(--rn-text-secondary); }

  .rescue-btn {
    width: 100%; padding: 1rem; margin-top: 1rem;
    background: linear-gradient(135deg, var(--rn-accent), #a855f7);
    border: none; border-radius: var(--rn-border-radius); color: #fff;
    font-size: 1.1rem; font-weight: 700; cursor: pointer;
    transition: all var(--rn-transition); box-shadow: 0 4px 20px var(--rn-accent-glow);
  }
  .rescue-btn:hover:not(:disabled) { transform: translateY(-2px); box-shadow: 0 8px 32px var(--rn-accent-glow); }
  .rescue-btn:disabled { opacity: 0.5; cursor: not-allowed; }

  .spinner {
    display: inline-block; width: 18px; height: 18px;
    border: 2px solid rgba(255,255,255,0.3); border-top-color: #fff;
    border-radius: 50%; animation: spin 0.6s linear infinite; vertical-align: middle;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  @media (max-width: 600px) {
    .source-grid { grid-template-columns: 1fr; }
    .hero h1 { font-size: 1.5rem; }
  }
</style>
