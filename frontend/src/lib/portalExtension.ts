// Talks to the NyayaBot Portal Helper extension through its content script on this
// page (extension/content/bridge.js). Messages stay inside this window and origin.

const FROM_APP = 'nyayabot-app';
const FROM_EXT = 'nyayabot-extension';

type ExtensionMessage = { source?: string; type?: string; ok?: boolean; error?: string; sidePanelOpened?: boolean };

function waitFor(type: string, timeoutMs: number): Promise<ExtensionMessage | null> {
  return new Promise((resolve) => {
    const timer = window.setTimeout(() => { window.removeEventListener('message', onMessage); resolve(null); }, timeoutMs);
    function onMessage(event: MessageEvent) {
      if (event.source !== window || event.origin !== window.location.origin) return;
      const data = event.data as ExtensionMessage;
      if (!data || data.source !== FROM_EXT || data.type !== type) return;
      window.clearTimeout(timer);
      window.removeEventListener('message', onMessage);
      resolve(data);
    }
    window.addEventListener('message', onMessage);
  });
}

/** True if the extension is installed and active on this page. */
export async function detectPortalExtension(timeoutMs = 800): Promise<boolean> {
  const reply = waitFor('READY', timeoutMs);
  window.postMessage({ source: FROM_APP, type: 'PING' }, window.location.origin);
  return (await reply) !== null;
}

/** Hands the session code to the extension, which opens the portal tab. */
export async function handOffToExtension(payload: { code: string; portal: unknown; apiBase: string }) {
  const reply = waitFor('AUTOFILL_ACK', 15000);
  window.postMessage({ source: FROM_APP, type: 'AUTOFILL_START', ...payload }, window.location.origin);
  const ack = await reply;
  if (!ack) return { ok: false as const, error: 'no_reply' };
  return ack.ok
    ? { ok: true as const, sidePanelOpened: Boolean(ack.sidePanelOpened) }
    : { ok: false as const, error: ack.error || 'extension_error' };
}
