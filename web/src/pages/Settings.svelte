<script>
  import { onMount } from 'svelte';
  import { platform, theme, say } from '../lib/stores.js';
  import { getConfig, saveConfig, getRemote, getRemoteQR, setAdminPassword } from '../lib/api.js';
  import { themeList, setTheme } from '../lib/theme.js';
  import { randomPassword, copy } from '../lib/format.js';

  let cfg = null;
  let saving = false;
  let remote = null;
  let remoteUrl = '';
  let qrSvg = '';
  let adminPw = '';
  let pwBusy = false;

  async function load() {
    try { cfg = await getConfig(); } catch (e) { say(e.message, 'error'); }
    try {
      remote = await getRemote();
      remoteUrl = remote.urls?.[0]?.url || '';
      if (remoteUrl) qrSvg = await getRemoteQR(remoteUrl);
    } catch { /* remote info is optional */ }
  }

  async function save() {
    saving = true;
    try { cfg = await saveConfig(cfg); say('Settings saved.'); }
    catch (e) { say(e.message, 'error'); }
    finally { saving = false; }
  }

  async function showQr(url) {
    remoteUrl = url;
    try { qrSvg = await getRemoteQR(url); } catch { qrSvg = ''; }
  }

  async function savePassword() {
    pwBusy = true;
    try {
      await setAdminPassword(adminPw);
      remote = { ...remote, password_set: true };
      adminPw = '';
      say('Admin password saved. You can now sign in from other devices.');
    } catch (e) { say(e.message, 'error'); }
    finally { pwBusy = false; }
  }

  onMount(load);
</script>

<svelte:head><title>Settings · NetCast</title></svelte:head>

<div class="page">
  <header class="page-head"><h1>Settings</h1></header>

  <section class="section" aria-labelledby="look-h">
    <header>
      <h2 id="look-h">Appearance</h2>
      <p>Saved on this device only.</p>
    </header>
    <fieldset class="choices">
      <legend class="sr-only">Theme</legend>
      {#each $themeList as t}
        <label class="choice">
          <input type="radio" name="theme" value={t.id} checked={$theme === t.id} on:change={() => setTheme(t.id)} />
          <span class="name">{t.name}</span>
          <span class="desc">{t.desc}</span>
        </label>
      {/each}
    </fieldset>
  </section>

  <section class="section" aria-labelledby="remote-h">
    <header>
      <h2 id="remote-h">Remote control</h2>
      <p>Manage NetCast from your phone while it is connected to the hotspot, or from any device on your network.</p>
    </header>

    {#if remote}
      <div class="panel">
        {#if remote.urls?.length}
          <div class="split">
            <div class="stack">
              <h3>Open this address on your phone</h3>
              {#each remote.urls as u}
                <div class="stack tight">
                  <a class="addr" href={u.url}>{u.url}</a>
                  <span class="small">{u.label}</span>
                  <div class="row">
                    <button class="btn small" on:click={() => showQr(u.url)}>Show QR code</button>
                    <button class="btn small" on:click={async () => say((await copy(u.url)) ? 'Address copied.' : 'Could not copy.')}>Copy</button>
                  </div>
                </div>
              {/each}
            </div>
            {#if qrSvg}<div class="qr" role="img" aria-label="QR code for {remoteUrl}">{@html qrSvg}</div>{/if}
          </div>
        {:else}
          <p><strong>The hotspot is off, so there is no remote address yet.</strong></p>
          <p class="small">Start the hotspot, then come back. To reach NetCast from your home network instead, set <code>listen_host = "0.0.0.0"</code> in the config file.</p>
        {/if}
      </div>

      <div class="panel">
        <h3>{remote.password_set ? 'Change the admin password' : 'Set an admin password'}</h3>
        <p class="small">{remote.password_set
          ? 'Other devices sign in with this password. Changing it signs them all out.'
          : 'Without a password, only this computer can manage NetCast. Set one to sign in from your phone.'}</p>
        <form class="stack" on:submit|preventDefault={savePassword}>
          <div class="field">
            <label for="admin-pw">New password (at least 8 characters)</label>
            <div class="row nowrap">
              <input id="admin-pw" class="grow" type="password" autocomplete="new-password" bind:value={adminPw} />
              <button type="button" class="btn small" on:click={() => { adminPw = randomPassword(14); }}>Generate</button>
            </div>
          </div>
          <div class="row">
            <button class="btn primary" disabled={adminPw.length < 8 || pwBusy}>Save password</button>
          </div>
        </form>
        <p class="small">NetCast uses plain HTTP, so only share this password on a network you trust. A hotspot protected by WPA2 qualifies.</p>
      </div>
    {/if}
  </section>

  {#if cfg}
    <form class="stack gap-lg" on:submit|preventDefault={save}>
      <section class="section" aria-labelledby="defaults-h">
        <header>
          <h2 id="defaults-h">Hotspot defaults</h2>
          <p>Used the next time you start a hotspot.</p>
        </header>
        <div class="panel">
          <div class="split">
            <div class="field"><label for="d-ssid">Network name</label><input id="d-ssid" type="text" maxlength="32" bind:value={cfg.ssid} /></div>
            <div class="field"><label for="d-band">Band</label>
              <select id="d-band" bind:value={cfg.band}><option value="bg">2.4 GHz</option><option value="a">5 GHz</option></select></div>
            <div class="field"><label for="d-ch">Channel</label><input id="d-ch" type="number" min="0" max="196" bind:value={cfg.channel} />
              <p class="hint">0 picks one automatically.</p></div>
          </div>
        </div>
      </section>

      {#if $platform.capabilities?.fair_share || $platform.capabilities?.captive_portal}
        <section class="section" aria-labelledby="rules-h">
          <header><h2 id="rules-h">Sharing rules</h2></header>
          <div class="panel">
            {#if $platform.capabilities?.fair_share}
              <label class="switch"><input type="checkbox" role="switch" bind:checked={cfg.fair_share_enabled} /><span>Give every device an equal share of the speed</span></label>
              {#if cfg.fair_share_enabled}
                <div class="field"><label for="d-bw">Total speed to share (kbps)</label>
                  <input id="d-bw" type="number" min="0" bind:value={cfg.total_bandwidth_kbps} /><p class="hint">0 uses a safe default.</p></div>
              {/if}
            {/if}
            {#if $platform.capabilities?.captive_portal}
              <label class="switch"><input type="checkbox" role="switch" bind:checked={cfg.captive_portal_enabled} /><span>Show a welcome page when devices connect</span></label>
              {#if cfg.captive_portal_enabled}
                <div class="field"><label for="d-msg">Welcome message</label>
                  <textarea id="d-msg" rows="2" bind:value={cfg.captive_portal_message}></textarea></div>
              {/if}
            {/if}
          </div>
        </section>
      {/if}

      <div class="row"><button class="btn primary big" disabled={saving}>{#if saving}<span class="spinner"></span>{/if} Save settings</button></div>
    </form>
  {/if}

  <section class="section" aria-labelledby="about-h">
    <header><h2 id="about-h">About</h2></header>
    <dl class="facts">
      <div><dt>Version</dt><dd>0.2.0</dd></div>
      <div><dt>System</dt><dd>{$platform.os}</dd></div>
      <div><dt>Hotspot engine</dt><dd>{$platform.backend || '–'}</dd></div>
    </dl>
  </section>
</div>

<style>
  .nowrap { flex-wrap: nowrap; }
  .tight { gap: 0.35rem; }
  .gap-lg { gap: 2.5rem; }
  .addr { font-size: 1.25rem; font-weight: 700; overflow-wrap: anywhere; }
</style>
