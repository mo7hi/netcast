<script>
  import { getToken } from '../lib/api.js';
  export let tab;
  $: src = tab.src + (tab.src.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(getToken());
</script>

<!-- Opaque-origin sandbox: plugin pages can run scripts but cannot touch the NetCast API. -->
<iframe title={tab.title} sandbox="allow-scripts" {src}></iframe>

<style>
  iframe { width: 100%; height: calc(100vh - 4rem); border: 1px solid var(--rn-border);
    border-radius: var(--rn-border-radius); background: var(--rn-bg-card); }
</style>
