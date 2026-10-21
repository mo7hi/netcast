<script>
  let authorized = false;
  let authorizing = false;

  async function authorize() {
    authorizing = true;
    try {
      const r = await fetch('/portal/authorize', { method: 'POST' });
      if (r.ok) {
        authorized = true;
        setTimeout(() => { window.location.href = 'https://www.google.com'; }, 1500);
      }
    } catch (e) {
      alert('Connection error. Please try again.');
    } finally {
      authorizing = false;
    }
  }
</script>

<div class="portal">
  <div class="portal-card">
    <div class="portal-logo">⚡</div>
    <h1>Welcome to NetCast</h1>

    {#if authorized}
      <div class="success-state">
        <div class="check-circle">✓</div>
        <p>You're connected! Redirecting...</p>
      </div>
    {:else}
      <p class="portal-msg">This network is shared via NetCast — the VLC of software routers.</p>
      <p class="portal-sub">Tap the button below to connect to the internet.</p>

      <button class="connect-btn" on:click={authorize} disabled={authorizing}>
        {#if authorizing}
          <span class="spinner"></span> Connecting...
        {:else}
          🌐 Connect to Internet
        {/if}
      </button>

      <p class="portal-footer">Powered by <strong>NetCast</strong></p>
    {/if}
  </div>
</div>

<style>
  .portal {
    min-height: 100vh;
    display: flex; align-items: center; justify-content: center;
    background: linear-gradient(135deg, #0f0f23 0%, #1a1a3e 50%, #2d1b69 100%);
    padding: 1.5rem;
  }

  .portal-card {
    max-width: 420px; width: 100%;
    background: rgba(255,255,255, 0.04);
    backdrop-filter: blur(20px);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 24px;
    padding: 3rem 2.5rem;
    text-align: center;
    box-shadow: 0 24px 80px rgba(0,0,0,0.5);
  }

  .portal-logo {
    font-size: 3.5rem; margin-bottom: 1rem;
    filter: drop-shadow(0 0 20px rgba(0, 212, 255, 0.5));
    animation: float 3s ease-in-out infinite;
  }
  @keyframes float {
    0%, 100% { transform: translateY(0); }
    50% { transform: translateY(-8px); }
  }

  h1 {
    font-size: 1.75rem; font-weight: 800; margin-bottom: 1rem;
    color: #ffffff; letter-spacing: -0.5px;
  }

  .portal-msg { color: #c0c0d8; font-size: 0.95rem; margin-bottom: 0.5rem; }
  .portal-sub { color: #7a7a9e; font-size: 0.85rem; margin-bottom: 2rem; }

  .connect-btn {
    width: 100%; padding: 1.1rem;
    background: linear-gradient(135deg, #00d4ff, #a855f7);
    border: none; border-radius: 14px; color: #fff;
    font-size: 1.15rem; font-weight: 700; cursor: pointer;
    transition: all 0.2s ease;
    box-shadow: 0 8px 32px rgba(0, 212, 255, 0.3);
  }
  .connect-btn:hover:not(:disabled) {
    transform: translateY(-3px);
    box-shadow: 0 12px 48px rgba(0, 212, 255, 0.4);
  }
  .connect-btn:active { transform: translateY(0); }
  .connect-btn:disabled { opacity: 0.7; cursor: not-allowed; }

  .spinner {
    display: inline-block; width: 18px; height: 18px;
    border: 2px solid rgba(255,255,255,0.3); border-top-color: #fff;
    border-radius: 50%; animation: spin 0.6s linear infinite; vertical-align: middle;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  .success-state { padding: 2rem 0; }
  .check-circle {
    width: 64px; height: 64px; margin: 0 auto 1rem;
    background: rgba(0, 232, 138, 0.15); border: 2px solid #00e88a;
    border-radius: 50%; display: flex; align-items: center; justify-content: center;
    font-size: 2rem; color: #00e88a; animation: scaleIn 0.4s ease;
  }
  @keyframes scaleIn { from { transform: scale(0); } to { transform: scale(1); } }
  .success-state p { color: #00e88a; font-weight: 600; }

  .portal-footer { margin-top: 2rem; font-size: 0.75rem; color: #4a4a6a; }
  .portal-footer strong { color: #7a7a9e; }
</style>
