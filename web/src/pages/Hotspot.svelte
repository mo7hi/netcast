<script>
  import { onMount, onDestroy } from 'svelte';
  import { get } from 'svelte/store';
  import { status, platform, interfaces, draft, say, route } from '../lib/stores.js';
  import { getInterfaces, getPreflight, startHotspot, stopHotspot, setPPPoE, getConfig, getQR, getMonitor } from '../lib/api.js';
  import { randomPassword, copy, bandName, rate } from '../lib/format.js';
  import Icon from '../components/Icon.svelte';

  const SOURCES = [
    { id: 'auto', name: 'Automatic', desc: 'Use the connection this computer is using right now. The best choice for most people.' },
    { id: 'ethernet', name: 'Ethernet cable', desc: 'A router or modem plugged into this computer.', key: 'ethernet', none: 'No Ethernet port found.' },
    { id: 'wifi', name: 'Wi-Fi network', desc: 'Repeat a Wi-Fi network this computer is connected to. Works with one adapter that can do both jobs at once, or with a second adapter.', key: 'wifi', none: 'No Wi-Fi adapter found.' },
    { id: 'usb', name: 'Phone over USB', desc: 'Plug in a phone and turn on USB tethering.', key: 'usb', none: 'No USB tethering device found. Plug in your phone first.' },
    { id: 'cellular', name: 'Mobile data modem', desc: 'A SIM modem connected to this computer.', key: 'cellular', none: 'No modem found.' },
    { id: 'vpn', name: 'VPN tunnel', desc: 'Share a WireGuard, OpenVPN or Tailscale connection, so every device on your hotspot uses the VPN.', key: 'vpn', none: 'No VPN connection is up.' },
    { id: 'pppoe', name: 'ISP modem (PPPoE)', desc: 'Dial in to your internet provider with your username and password. Plug the modem into an Ethernet port.', key: 'ethernet', none: 'No Ethernet port found for the modem.' },
  ];

  const saved = get(draft);
  let sourceType = saved?.sourceType ?? 'auto';
  let sourceIface = saved?.sourceIface ?? '';
  let ssid = saved?.ssid ?? 'NetCast';
  let password = saved?.password ?? '';
  let band = saved?.band ?? 'bg';
  let pppoeUser = saved?.pppoeUser ?? '';
  let pppoePass = '';
  let showPassword = false;
  let checks = null;
  let checking = false;
  let starting = false;
  let stopping = false;
  let dialog;
  let qrSvg = '';
  let qrFor = '';
  let monitor = null;
  let lastMon = null;
  let timer;

  $: ifaces = $interfaces;
  $: caps = $platform.capabilities || {};
  $: onLinux = $platform.os === 'linux';
  $: visibleSources = SOURCES.filter((s) => !caps.sources || caps.sources.includes(s.id));
  $: draft.set($status.active ? null : { sourceType, sourceIface, ssid, password, band, pppoeUser });
  $: passwordOk = password.length >= 8 && password.length <= 63;
  $: ssidOk = ssid.trim().length >= 1 && new TextEncoder().encode(ssid).length <= 32;
  $: canStart = passwordOk && ssidOk && !starting && (sourceType !== 'pppoe' || (pppoeUser && (pppoePass || saved)));
  $: if ($status.active && $status.ssid && qrFor !== $status.ssid + $status.password) loadQr();

  function found(src, all) { return src.key ? all[src.key] || [] : []; }
  function unavailable(src, all) { return !!src.key && found(src, all).length === 0; }

  function pick(src) {
    sourceType = src.id;
    sourceIface = src.key ? found(src, ifaces)[0] || '' : '';
    checks = null;
  }

  async function loadQr() {
    qrFor = $status.ssid + $status.password;
    try { qrSvg = await getQR(); } catch { qrSvg = ''; }
  }

  async function check() {
    checking = true;
    try {
      const pf = await getPreflight({ band, source_type: sourceType, source_interface: sourceIface });
      checks = pf.checks;
    } catch (e) { say(e.message, 'error'); }
    finally { checking = false; }
  }

  async function start() {
    starting = true;
    try {
      if (sourceType === 'pppoe' && pppoePass) await setPPPoE(pppoeUser, pppoePass);
      const pf = await getPreflight({ band, source_type: sourceType, source_interface: sourceIface });
      checks = pf.checks;
      if (!pf.passed) { say('Fix the problems under "Checks", then start again.', 'error'); return; }
      await startHotspot({ ssid, password, band, source_type: sourceType, source_interface: sourceIface });
      say($platform.assistant ? 'Follow the steps below to finish.' : 'Hotspot started.');
    } catch (e) { say(e.message, 'error'); }
    finally { starting = false; }
  }

  async function stop() {
    dialog.close();
    stopping = true;
    try { await stopHotspot(); say('Hotspot stopped.'); }
    catch (e) { say(e.message, 'error'); }
    finally { stopping = false; }
  }

  async function pollMonitor() {
    if (!$status.active) { monitor = null; lastMon = null; return; }
    try {
      const s = await getMonitor();
      const now = Date.now();
      if (lastMon && now > lastMon.t) {
        const dt = (now - lastMon.t) / 1000;
        // From the AP's point of view: tx = sent to devices (their download), rx = from devices.
        monitor = {
          down: ((s.tx_bytes - lastMon.tx) * 8) / 1000 / dt,
          up: ((s.rx_bytes - lastMon.rx) * 8) / 1000 / dt,
        };
      }
      lastMon = { t: now, rx: s.rx_bytes, tx: s.tx_bytes };
    } catch { monitor = null; }
  }

  async function reveal() { showPassword = !showPassword; }

  async function copyPassword() { say((await copy($status.password || password)) ? 'Password copied.' : 'Could not copy.'); }

  onMount(async () => {
    try { $interfaces = await getInterfaces(); } catch { /* shown by checks */ }
    if (!saved) {
      try {
        const cfg = await getConfig();
        ssid = cfg.ssid || ssid;
        band = cfg.band || band;
        // Never reuse the factory password: every NetCast install shares it.
        password = cfg.password && cfg.password !== 'netcast1234' ? cfg.password : randomPassword();
      } catch { password = randomPassword(); }
    }
    timer = setInterval(pollMonitor, 2500);
  });
  onDestroy(() => clearInterval(timer));
</script>

<svelte:head><title>Hotspot · NetCast</title></svelte:head>

{#if $status.active}
  <div class="page">
    <header class="page-head">
      <h1>{$status.ssid}</h1>
      <p>Hotspot is on. Sharing {$status.source_interface || 'your connection'} on {bandName($status.band)}{$status.channel ? `, channel ${$status.channel}` : ''}.</p>
      <div class="row">
        <button class="btn danger" on:click={() => dialog.showModal()} disabled={stopping}>
          {#if stopping}<span class="spinner"></span>{:else}<Icon name="power" size={20} />{/if} Stop hotspot
        </button>
        <a class="btn" href="#/devices">See devices</a>
      </div>
    </header>

    <div class="split">
      <section class="panel" aria-labelledby="connect-h">
        <h2 id="connect-h">Connect a phone</h2>
        <div class="qr" role="img" aria-label="QR code that joins {$status.ssid}">{@html qrSvg}</div>
        <p class="small">Point the camera at the code, or enter the details by hand.</p>
        <dl class="facts">
          <div><dt>Network name</dt><dd>{$status.ssid}</dd></div>
          <div>
            <dt>Password</dt>
            <dd class="pw">{showPassword ? $status.password : '••••••••••'}</dd>
          </div>
        </dl>
        <div class="row">
          <button class="btn small" on:click={reveal} aria-pressed={showPassword}>
            <Icon name={showPassword ? 'eyeoff' : 'eye'} size={18} /> {showPassword ? 'Hide' : 'Show'} password
          </button>
          <button class="btn small" on:click={copyPassword}><Icon name="copy" size={18} /> Copy</button>
        </div>
      </section>

      <div class="stack">
        <section class="panel" aria-labelledby="now-h">
          <h2 id="now-h">Right now</h2>
          <dl class="facts">
            <div><dt>Devices connected</dt><dd class="big-number">{$status.client_count || 0}</dd></div>
            {#if monitor}
              <div><dt>Download</dt><dd class="mid">{rate(Math.max(monitor.down, 0))}</dd></div>
              <div><dt>Upload</dt><dd class="mid">{rate(Math.max(monitor.up, 0))}</dd></div>
            {/if}
          </dl>
        </section>

        <section class="panel" aria-labelledby="net-h">
          <h2 id="net-h">Network details</h2>
          <dl class="facts">
            <div><dt>Internet source</dt><dd>{$status.source_interface || '–'}</dd></div>
            <div><dt>Hotspot adapter</dt><dd>{$status.ap_interface || '–'}</dd></div>
            <div><dt>Address</dt><dd>{$status.gateway_ip || '–'}</dd></div>
            <div><dt>Band</dt><dd>{bandName($status.band)}</dd></div>
          </dl>
          <p class="small">Control this hotspot from a phone connected to it: open <a href="#/settings">Settings → Remote control</a> for the address.</p>
        </section>
      </div>
    </div>
  </div>

  <dialog bind:this={dialog} aria-labelledby="stop-h">
    <h2 id="stop-h">Stop the hotspot?</h2>
    <p>Everyone connected will lose their internet.</p>
    <div class="row">
      <button class="btn danger" on:click={stop}>Stop hotspot</button>
      <button class="btn" on:click={() => dialog.close()}>Keep it running</button>
    </div>
  </dialog>
{:else}
  <form class="page" on:submit|preventDefault={start}>
    <header class="page-head">
      <h1>Share your internet</h1>
      <p>Choose where the internet comes from, name your Wi-Fi, and start. Phones, laptops and TVs can then join it.</p>
    </header>

    {#if onLinux}
      <fieldset class="choices section">
        <legend><h2>Where does the internet come from?</h2></legend>
        {#each visibleSources as src}
          {@const list = found(src, ifaces)}
          <label class="choice">
            <input type="radio" name="source" value={src.id} checked={sourceType === src.id}
                   disabled={unavailable(src, ifaces)} on:change={() => pick(src)} />
            <span class="name">{src.name}</span>
            <span class="desc">
              {src.desc}
              {#if src.key}
                {#if list.length}<br /><strong>Found:</strong> {list.join(', ')}{:else}<br />{src.none}{/if}
              {/if}
            </span>
            {#if sourceType === src.id && list.length > 1}
              <span class="extra">
                <label class="sr-only" for="iface-{src.id}">Which {src.name} to use</label>
                <select id="iface-{src.id}" bind:value={sourceIface}>
                  {#each list as name}<option value={name}>{name}</option>{/each}
                </select>
              </span>
            {/if}
            {#if sourceType === 'pppoe' && src.id === 'pppoe'}
              <span class="extra stack">
                <span class="field">
                  <label for="pppoe-user">ISP username</label>
                  <input id="pppoe-user" type="text" autocomplete="off" bind:value={pppoeUser} />
                </span>
                <span class="field">
                  <label for="pppoe-pass">ISP password</label>
                  <input id="pppoe-pass" type="password" autocomplete="off" bind:value={pppoePass}
                         placeholder={saved ? '' : 'Saved on this computer'} />
                </span>
              </span>
            {/if}
          </label>
        {/each}
      </fieldset>
    {:else if $platform.assistant}
      <section class="panel" aria-labelledby="assist-h">
        <h2 id="assist-h">Finish in System Settings</h2>
        <p>macOS does not let apps turn on Internet Sharing, so NetCast opens the right place and watches until it is on.</p>
        <ol class="steps">
          {#each $platform.steps || [] as step}<li>{step}</li>{/each}
        </ol>
      </section>
    {:else}
      <section class="panel">
        <h2>Your current internet connection</h2>
        <p>On this system NetCast shares the connection Windows is using right now.</p>
      </section>
    {/if}

    <section class="section" aria-labelledby="wifi-h">
      <header><h2 id="wifi-h">Your hotspot</h2></header>
      <div class="split">
        <div class="field">
          <label for="ssid">Network name</label>
          <input id="ssid" type="text" maxlength="32" bind:value={ssid} aria-invalid={ssidOk ? undefined : 'true'} />
        </div>
        <div class="field">
          <label for="password">Password</label>
          <div class="row nowrap">
            <input id="password" class="grow" type={showPassword ? 'text' : 'password'} value={password}
                   on:input={(e) => (password = e.currentTarget.value)} autocomplete="off"
                   aria-describedby="pw-hint" aria-invalid={passwordOk ? undefined : 'true'} />
            <button type="button" class="btn small" on:click={reveal} aria-pressed={showPassword}>
              <Icon name={showPassword ? 'eyeoff' : 'eye'} size={18} /><span class="sr-only">{showPassword ? 'Hide' : 'Show'} password</span>
            </button>
            <button type="button" class="btn small" on:click={() => (password = randomPassword())}>
              <Icon name="refresh" size={18} /> New
            </button>
          </div>
          <p id="pw-hint" class="hint">8 to 63 characters. A new random one is already filled in.</p>
        </div>
      </div>

      {#if onLinux || $platform.assistant}
        <fieldset class="choices inline">
          <legend class="legend">Wi-Fi band</legend>
          <label class="choice compact">
            <input type="radio" name="band" value="bg" bind:group={band} />
            <span class="name">2.4 GHz</span><span class="desc">Reaches farther. Works with older devices.</span>
          </label>
          <label class="choice compact">
            <input type="radio" name="band" value="a" bind:group={band} />
            <span class="name">5 GHz</span><span class="desc">Faster. Shorter range.</span>
          </label>
        </fieldset>
      {/if}
    </section>

    {#if checks}
      <section class="section" aria-labelledby="checks-h">
        <header><h2 id="checks-h">Checks</h2></header>
        <ul class="checks">
          {#each checks as c}
            <li class={c.state}>
              <span class="word">{c.state === 'pass' ? 'Passed' : c.state === 'warn' ? 'Warning' : 'Problem'}</span>
              <span><strong>{c.name}</strong><br /><span class="msg">{c.message}</span></span>
              {#if c.fix && c.state !== 'pass'}<span class="fix">{c.fix}</span>{/if}
            </li>
          {/each}
        </ul>
      </section>
    {/if}

    <div class="row">
      <button class="btn primary big" disabled={!canStart}>
        {#if starting}<span class="spinner"></span> Starting…{:else}<Icon name="power" size={22} /> {$platform.assistant ? 'Open Internet Sharing' : 'Start hotspot'}{/if}
      </button>
      <button type="button" class="btn" on:click={check} disabled={checking || starting}>
        {#if checking}<span class="spinner"></span>{/if} Check this setup
      </button>
    </div>
  </form>
{/if}

<style>
  .nowrap { flex-wrap: nowrap; }
  .pw { letter-spacing: 0.05em; }
  .mid { font-size: 1.75rem; font-weight: 700; letter-spacing: -0.02em; }
  .steps { padding-left: 1.25rem; display: flex; flex-direction: column; gap: 0.5rem; }
</style>
