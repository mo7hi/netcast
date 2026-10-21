<script>
  import { page, preflight as preflightStore, loading } from '../lib/stores.js';
  import { getPreflight } from '../lib/api.js';
  import { onMount } from 'svelte';

  let checks = [];
  let passed = false;
  let running = true;

  onMount(async () => {
    loading.set(true);
    try {
      const result = await getPreflight();
      checks = result.checks || [];
      passed = result.passed;
      preflightStore.set(result);
    } catch (e) {
      checks = [{ name: 'Connection', status: 'fail', message: 'Cannot reach daemon: ' + e.message, fix_hint: 'Is netcastd running?' }];
    } finally {
      running = false;
      loading.set(false);
    }
  });

  function statusIcon(s) {
    if (s === 'pass') return '✓';
    if (s === 'warn') return '⚠';
    return '✗';
  }
  function statusClass(s) {
    if (s === 'pass') return 'check-pass';
    if (s === 'warn') return 'check-warn';
    return 'check-fail';
  }
</script>

<div class="preflight">
  <div class="header">
    <h1>Pre-Flight Checks</h1>
    <p class="subtitle">Verifying your system is ready to create a hotspot.</p>
  </div>

  {#if running}
    <div class="loading-panel">
      <div class="scan-anim"></div>
      <p>Scanning hardware and system configuration...</p>
    </div>
  {:else}
    <div class="results">
      {#each checks as check}
        <div class="check-row {statusClass(check.status)}">
          <span class="check-icon">{statusIcon(check.status)}</span>
          <div class="check-info">
            <span class="check-name">{check.name}</span>
            <span class="check-msg">{check.message}</span>
            {#if check.fix_hint && check.status !== 'pass'}
              <span class="check-hint">💡 {check.fix_hint}</span>
            {/if}
          </div>
        </div>
      {/each}
    </div>

    <div class="actions">
      {#if passed}
        <div class="pass-banner">
          <span class="pass-icon">✓</span>
          All checks passed — you're good to go!
        </div>
        <button class="primary-btn" on:click={() => page.set('wizard')}>Back to Wizard to start the hotspot →</button>
      {:else}
        <div class="fail-banner">
          <span class="fail-icon">✗</span>
          Some checks failed. Fix the issues above before continuing.
        </div>
        <button class="secondary-btn" on:click={() => window.location.reload()}>Re-run Checks</button>
        <button class="text-btn" on:click={() => page.set('wizard')}>← Back to Wizard</button>
      {/if}
    </div>
  {/if}
</div>

<style>
  .preflight { max-width: 640px; margin: 0 auto; }
  .header { margin-bottom: 2rem; }
  .header h1 { font-size: 1.75rem; font-weight: 800; margin-bottom: 0.4rem; }
  .subtitle { color: var(--rn-text-secondary); }

  .loading-panel {
    text-align: center; padding: 3rem 2rem;
    background: var(--rn-bg-card); border-radius: var(--rn-border-radius);
    border: 1px solid var(--rn-border);
  }
  .scan-anim {
    width: 48px; height: 48px; margin: 0 auto 1rem;
    border: 3px solid var(--rn-border); border-top-color: var(--rn-accent);
    border-radius: 50%; animation: spin 1s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .loading-panel p { color: var(--rn-text-secondary); }

  .results { display: flex; flex-direction: column; gap: 0.5rem; margin-bottom: 2rem; }
  .check-row {
    display: flex; align-items: flex-start; gap: 0.75rem;
    padding: 1rem 1.25rem; border-radius: var(--rn-border-radius-sm);
    background: var(--rn-bg-card); border: 1px solid var(--rn-border);
    transition: all var(--rn-transition);
  }
  .check-row:hover { background: var(--rn-bg-card-hover); }
  .check-icon { font-size: 1.25rem; font-weight: 700; line-height: 1.4; flex-shrink: 0; }
  .check-pass .check-icon { color: var(--rn-success); }
  .check-warn .check-icon { color: var(--rn-warning); }
  .check-fail .check-icon { color: var(--rn-danger); }
  .check-info { display: flex; flex-direction: column; gap: 0.2rem; }
  .check-name { font-weight: 600; font-size: 0.9rem; }
  .check-msg { font-size: 0.8rem; color: var(--rn-text-secondary); }
  .check-hint { font-size: 0.78rem; color: var(--rn-accent); margin-top: 0.25rem; }

  .actions { display: flex; flex-direction: column; align-items: center; gap: 0.75rem; }
  .pass-banner, .fail-banner {
    width: 100%; padding: 1rem; border-radius: var(--rn-border-radius-sm);
    text-align: center; font-weight: 600; font-size: 0.95rem;
  }
  .pass-banner { background: var(--rn-success-bg); color: var(--rn-success); border: 1px solid rgba(0,232,138,0.2); }
  .fail-banner { background: var(--rn-danger-bg); color: var(--rn-danger); border: 1px solid rgba(255,68,102,0.2); }
  .pass-icon, .fail-icon { margin-right: 0.5rem; }

  .primary-btn {
    width: 100%; padding: 0.875rem; background: linear-gradient(135deg, var(--rn-accent), #a855f7);
    border: none; border-radius: var(--rn-border-radius); color: #fff; font-size: 1rem;
    font-weight: 700; cursor: pointer; transition: all var(--rn-transition);
  }
  .primary-btn:hover { transform: translateY(-2px); box-shadow: 0 8px 32px var(--rn-accent-glow); }
  .secondary-btn {
    width: 100%; padding: 0.75rem; background: var(--rn-bg-card);
    border: 1px solid var(--rn-border); border-radius: var(--rn-border-radius);
    color: var(--rn-text-primary); font-weight: 600; cursor: pointer;
  }
  .secondary-btn:hover { border-color: var(--rn-accent); }
  .text-btn { background: none; border: none; color: var(--rn-text-secondary); cursor: pointer; font-size: 0.9rem; }
  .text-btn:hover { color: var(--rn-accent); }
</style>
