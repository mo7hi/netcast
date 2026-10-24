<script>
  import { onMount } from 'svelte';
  import { status, say } from '../lib/stores.js';
  import { getPlugins, savePlugin, getUiTabs, getToken } from '../lib/api.js';

  let plugins = [];
  let edits = {};      // plugin id -> { enabled, values }
  let busy = '';
  let loaded = false;
  let tabs = [];

  function seed(list) {
    plugins = list;
    edits = Object.fromEntries(
      list.map((p) => [p.id, { enabled: p.enabled, values: { ...p.values } }])
    );
  }

  async function load() {
    try { seed((await getPlugins()).plugins); }
    catch (e) { say(e.message, 'error'); }
    try { tabs = await getUiTabs(); } catch { /* optional */ }
    loaded = true;
  }

  const visible = (f, vals) => {
    if (!f.show_if) return true;
    const cur = vals[f.show_if.key];
    return 'not' in f.show_if ? cur !== f.show_if.not : cur === f.show_if.value;
  };

  async function save(p) {
    busy = p.id;
    try {
      const e = edits[p.id];
      const res = await savePlugin(p.id, e.enabled, e.values);
      plugins = plugins.map((x) => (x.id === p.id ? res.plugin : x));
      edits[p.id] = { enabled: res.plugin.enabled, values: { ...res.plugin.values } };
      say(res.plugin.needs_restart && $status.active
        ? `${p.title} saved. It takes effect the next time the hotspot starts.`
        : `${p.title} saved.`);
    } catch (e) { say(e.message, 'error'); }
    finally { busy = ''; }
  }

  onMount(load);
</script>

<svelte:head><title>Plugins · NetCast</title></svelte:head>

<div class="page">
  <header class="page-head">
    <h1>Plugins</h1>
    <p>Add abilities to your hotspot. Turn on only what you need; each plugin is off until you enable it.</p>
  </header>

  {#if !loaded}
    <p class="small">Loading…</p>
  {:else if plugins.length === 0}
    <div class="empty"><p><strong>No plugins are installed.</strong></p></div>
  {/if}

  {#each plugins as p (p.id)}
    {@const e = edits[p.id]}
    <section class="panel" aria-labelledby="p-{p.id}">
      <div class="row head">
        <h2 id="p-{p.id}" class="grow">{p.title}</h2>
        <label class="switch">
          <input type="checkbox" role="switch" bind:checked={e.enabled} aria-label="Turn {p.title} on" />
          <span>{e.enabled ? 'On' : 'Off'}</span>
        </label>
      </div>
      <p>{p.description}</p>

      {#if e.enabled && p.fields.length}
        <div class="stack">
          {#each p.fields as f}
            {#if visible(f, e.values)}
              {#if f.type === 'toggle'}
                <label class="switch">
                  <input type="checkbox" role="switch" bind:checked={e.values[f.key]} />
                  <span>{f.label}</span>
                </label>
                {#if f.help}<p class="hint">{f.help}</p>{/if}
              {:else if f.type === 'select'}
                <div class="field">
                  <label for="{p.id}-{f.key}">{f.label}</label>
                  <select id="{p.id}-{f.key}" bind:value={e.values[f.key]}>
                    {#each f.options as o}<option value={o.value}>{o.label}</option>{/each}
                  </select>
                  {#if f.help}<p class="hint">{f.help}</p>{/if}
                </div>
              {:else if f.type === 'password'}
                <div class="field">
                  <label for="{p.id}-{f.key}">{f.label}</label>
                  <input id="{p.id}-{f.key}" type="password" autocomplete="off" bind:value={e.values[f.key]}
                         placeholder={p.secrets_set?.[f.key] ? 'Saved. Leave blank to keep it.' : ''} />
                  {#if f.help}<p class="hint">{f.help}</p>{/if}
                </div>
              {:else if f.type === 'number'}
                <div class="field">
                  <label for="{p.id}-{f.key}">{f.label}</label>
                  <input id="{p.id}-{f.key}" type="number" bind:value={e.values[f.key]} min={f.min} max={f.max} />
                  {#if f.help}<p class="hint">{f.help}</p>{/if}
                </div>
              {:else}
                <div class="field">
                  <label for="{p.id}-{f.key}">{f.label}</label>
                  <input id="{p.id}-{f.key}" type={f.type === 'url' ? 'url' : 'text'} bind:value={e.values[f.key]} />
                  {#if f.help}<p class="hint">{f.help}</p>{/if}
                </div>
              {/if}
            {/if}
          {/each}
        </div>
      {/if}

      {#if e.enabled && p.links?.length}
        <div class="panel inset">
          <h3>Links to share</h3>
          <ul class="stack links">
            {#each p.links as l}
              <li><strong>{l.label}</strong><br /><a href={l.url}>{l.url}</a>{#if l.help}<br /><span class="small">{l.help}</span>{/if}</li>
            {/each}
          </ul>
        </div>
      {/if}

      <div class="row">
        <button class="btn primary" disabled={busy === p.id} on:click={() => save(p)}>
          {#if busy === p.id}<span class="spinner"></span>{/if} Save
        </button>
      </div>
    </section>
  {/each}

  {#each tabs as tab (tab.id)}
    <section class="panel" aria-labelledby="tab-{tab.id}">
      <h2 id="tab-{tab.id}">{tab.title}</h2>
      <!-- Opaque-origin sandbox: plugin pages can run scripts but cannot reach the NetCast API. -->
      <iframe title={tab.title} sandbox="allow-scripts"
              src="{tab.src}{tab.src.includes('?') ? '&' : '?'}token={encodeURIComponent(getToken())}"></iframe>
    </section>
  {/each}

  <section class="section" aria-labelledby="more-h">
    <header>
      <h2 id="more-h">Add more</h2>
      <p>Plugins are Python packages. Install one on this computer and restart NetCast:</p>
    </header>
    <div class="panel inset"><code>pip install netcast-plugin-name</code></div>
    <p class="small">Writing your own takes about twenty lines. The README explains how.</p>
  </section>
</div>

<style>
  .head { align-items: center; }
  iframe { width: 100%; min-height: 24rem; border: var(--border-w) solid var(--line); background: var(--surface); }
  .links { list-style: none; padding: 0; overflow-wrap: anywhere; }
</style>
