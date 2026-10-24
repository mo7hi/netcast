<script>
  import { login } from '../lib/api.js';
  import Logo from '../components/Logo.svelte';

  export let onsuccess = () => {};
  let password = '';
  let err = '';
  let busy = false;

  async function submit() {
    busy = true; err = '';
    try { await login(password); onsuccess(); }
    catch (e) { err = e.message; }
    finally { busy = false; }
  }
</script>

<main class="center" id="main">
  <form class="panel" on:submit|preventDefault={submit}>
    <div class="brand"><Logo /> NetCast</div>
    <h1 class="title">Sign in to manage this hotspot</h1>
    <div class="field">
      <label for="pw">Admin password</label>
      <input id="pw" type="password" autocomplete="current-password" bind:value={password}
             aria-invalid={err ? 'true' : undefined} aria-describedby={err ? 'pw-err' : undefined} />
      {#if err}<p id="pw-err" role="alert" class="err">{err}</p>{/if}
    </div>
    <button class="btn primary big" disabled={!password || busy}>
      {#if busy}<span class="spinner"></span>{/if} Sign in
    </button>
  </form>
</main>

<style>
  .center { min-height: 100dvh; display: grid; place-items: center; padding: 1rem; }
  .panel { width: min(100%, 26rem); }
  .title { font-size: 1.75rem; letter-spacing: -0.03em; }
  .err { color: var(--danger); font-weight: 700; }
</style>
