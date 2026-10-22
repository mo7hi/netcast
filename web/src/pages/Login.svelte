<script>
  import { createEventDispatcher } from 'svelte';
  import { login } from '../lib/api.js';

  const dispatch = createEventDispatcher();
  let password = '';
  let err = '';
  let busy = false;

  async function submit() {
    busy = true; err = '';
    try { await login(password); dispatch('success'); }
    catch (e) { err = e.message; }
    finally { busy = false; }
  }
</script>

<div class="login">
  <div class="card">
    <div class="logo">⚡</div>
    <h1>NetCast</h1>
    <p>Enter the admin password to manage this hotspot.</p>
    <input type="password" bind:value={password} placeholder="Admin password"
           on:keydown={(e) => e.key === 'Enter' && password && submit()} />
    {#if err}<p class="err">{err}</p>{/if}
    <button on:click={submit} disabled={!password || busy}>{busy ? '...' : 'Sign in'}</button>
  </div>
</div>

<style>
  .login { min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 1.5rem; }
  .card { width: 100%; max-width: 360px; text-align: center; padding: 2.5rem 2rem;
    background: var(--rn-bg-card); border: 1px solid var(--rn-border); border-radius: var(--rn-border-radius); }
  .logo { font-size: 2.5rem; }
  h1 { margin: 0.5rem 0; }
  p { color: var(--rn-text-secondary); font-size: 0.9rem; margin-bottom: 1rem; }
  .err { color: var(--rn-danger); }
  input { width: 100%; padding: 0.75rem 1rem; margin-bottom: 1rem; background: var(--rn-bg-input);
    border: 1px solid var(--rn-border); border-radius: var(--rn-border-radius-sm); color: var(--rn-text-primary); }
  button { width: 100%; padding: 0.85rem; border: none; border-radius: var(--rn-border-radius);
    background: var(--rn-accent); color: #000; font-weight: 700; cursor: pointer; }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
