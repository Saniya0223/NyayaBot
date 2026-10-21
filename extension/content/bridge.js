/*
 * Runs only on the NyayaBot web app (see manifest). Lets the NyayaBot page hand
 * this extension a short-lived Fill on Portal session code.
 *
 * Accepts messages only from the NyayaBot page itself (same window, same origin).
 * Never reads anything else from the page.
 */
(function () {
  'use strict';
  const FROM_APP = 'nyayabot-app';
  const FROM_EXT = 'nyayabot-extension';
  const version = chrome.runtime.getManifest().version;

  function reply(type, detail) {
    window.postMessage({ source: FROM_EXT, type, version, ...detail }, window.location.origin);
  }

  window.addEventListener('message', (event) => {
    if (event.source !== window || event.origin !== window.location.origin) return;
    const data = event.data;
    if (!data || data.source !== FROM_APP) return;
    if (data.type === 'PING') { reply('READY'); return; }
    if (data.type === 'AUTOFILL_START') {
      const { code, portal, apiBase } = data;
      if (typeof code !== 'string' || !portal || typeof apiBase !== 'string') {
        reply('AUTOFILL_ACK', { ok: false, error: 'bad_request' });
        return;
      }
      chrome.runtime.sendMessage({ type: 'START_SESSION', code, portal, apiBase }, (response) => {
        const failed = chrome.runtime.lastError || !response || !response.ok;
        reply('AUTOFILL_ACK', failed
          ? { ok: false, error: (response && response.error) || 'extension_error' }
          : { ok: true, sidePanelOpened: Boolean(response.sidePanelOpened) });
      });
    }
  });

  // Announce presence so the NyayaBot page can show "Fill on Portal".
  reply('READY');
})();
