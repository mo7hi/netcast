<script>
  import { onMount } from 'svelte';
  import { status, clients, say } from '../lib/stores.js';
  import { getClients, getBlocked, kickClient, blockClient } from '../lib/api.js';
  import { bytes } from '../lib/format.js';

  let blocked = [];
  let busy = '';

  async function refresh() {
    try {
      $clients = await getClients();
      blocked = (await getBlocked()).blocked || [];
    } catch { /* offline or hotspot off */ }
  }

  async function act(label, mac, fn) {
    busy = mac;
    try { await fn(); say(label); await refresh(); }
    catch (e) { say(e.message, 'error'); }
    finally { busy = ''; }
  }

  const kick = (c) => act('Device disconnected. It can join again.', c.mac, () => kickClient(c.mac));
  const block = (c) => act('Device blocked from the internet.', c.mac, () => blockClient(c.mac, true));
  const unblock = (mac) => act('Device unblocked.', mac, () => blockClient(mac, false));

  onMount(refresh);
</script>

<svelte:head><title>Devices · NetCast</title></svelte:head>

<div class="page">
  <header class="page-head">
    <h1>Devices</h1>
    <p>Everything connected to your hotspot right now. Disconnect a device to make it rejoin, or block it to cut its internet until you unblock it.</p>
  </header>

  <section class="section" aria-labelledby="live-h">
    <header><h2 id="live-h">Connected <span class="count">{$clients.length}</span></h2></header>
    {#if !$status.active}
      <div class="empty">
        <p><strong>The hotspot is off.</strong></p>
        <p class="small">Turn it on to see devices here.</p>
        <a class="btn primary" href="#/hotspot">Go to Hotspot</a>
      </div>
    {:else if $clients.length === 0}
      <div class="empty">
        <p><strong>No devices yet.</strong></p>
        <p class="small">Connect a phone to “{$status.ssid}” using the QR code on the Hotspot page.</p>
        <a class="btn" href="#/hotspot">Show the QR code</a>
      </div>
    {:else}
      <ul class="list">
        {#each $clients as c (c.mac)}
          <li>
            <div class="who grow">
              <strong>{c.hostname || 'Unknown device'}</strong>
              <span class="small">{c.ip} · {c.mac}</span>
            </div>
            <div class="traffic small">
              <span>Uploaded {bytes(c.rx_bytes)}</span>
              <span>Downloaded {bytes(c.tx_bytes)}</span>
            </div>
            <div class="row">
              <button class="btn small" disabled={busy === c.mac} on:click={() => kick(c)}>Disconnect</button>
              <button class="btn small danger" disabled={busy === c.mac} on:click={() => block(c)}>Block</button>
            </div>
          </li>
        {/each}
      </ul>
    {/if}
  </section>

  {#if blocked.length}
    <section class="section" aria-labelledby="blocked-h">
      <header><h2 id="blocked-h">Blocked <span class="count">{blocked.length}</span></h2></header>
      <ul class="list">
        {#each blocked as mac}
          <li>
            <span class="grow">{mac}</span>
            <button class="btn small" disabled={busy === mac} on:click={() => unblock(mac)}>Unblock</button>
          </li>
        {/each}
      </ul>
    </section>
  {/if}
</div>

<style>
  .count { color: var(--ink-2); font-weight: 400; margin-left: 0.35rem; }
  .who { display: flex; flex-direction: column; min-width: 12rem; }
  .traffic { display: flex; flex-direction: column; }
</style>
