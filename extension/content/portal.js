/*
 * Runs on the supported portal pages (see manifest), in every frame.
 * Connects lib/page.js to the extension's background worker. It acts only when
 * the background asks, and only through NyayaPage's checked functions.
 */
(function () {
  'use strict';
  const page = window.NyayaPage;
  // The background may inject this script into a tab that was already open when the
  // extension was reloaded; never register the listeners twice in the same page. A
  // copy from before the reload stays in the page but can no longer reach the
  // extension, so it does not count.
  const earlier = window.__nyayaPortalReady;
  if (!page || (typeof earlier === 'function' && earlier())) return;
  window.__nyayaPortalReady = () => {
    try { return Boolean(chrome.runtime && chrome.runtime.id); } catch (e) { return false; }
  };
  page.installGuard(document);

  chrome.runtime.sendMessage({ type: 'FRAME_HELLO', url: location.href, top: window === window.top }, () => {
    void chrome.runtime.lastError;   // the background may be asleep; it re-discovers frames later
  });

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (!message || typeof message.cmd !== 'string') return false;
    try {
      switch (message.cmd) {
        case 'ping':
          sendResponse({ ok: true, url: location.href, top: window === window.top });
          return false;
        case 'loginSignals':
          sendResponse({ ok: true, url: location.href, signals: page.loginSignals(document, location.href) });
          return false;
        case 'scanFields':
          sendResponse({ ok: true, url: location.href, fields: page.scanFields(document) });
          return false;
        case 'scanLinks':
          sendResponse({ ok: true, url: location.href, links: page.scanLinks(document) });
          return false;
        case 'clickLink':
          sendResponse(page.clickLink(document, message.index));
          return false;
        case 'scanChoices': {
          const { groups, stats } = page.scanChoiceGroups(document);
          sendResponse({ ok: true, url: location.href, groups, stats });
          return false;
        }
        case 'clickChoice':
          sendResponse(page.clickChoice(document, message.index, message.text));
          return false;
        case 'scanDialogs':
          sendResponse({ ok: true, url: location.href, dialogs: page.scanDialogs(document) });
          return false;
        case 'pageContext':
          sendResponse({ ok: true, url: location.href, context: page.pageContext(document) });
          return false;
        case 'peekOptions':
          page.peekOptions(document, message.indices).then((options) => sendResponse({ ok: true, options }));
          return true;   // async response
        case 'clickNext':
          sendResponse(page.clickNext(document, message.near));
          return false;
        case 'fill':
          page.fillActions(document, message.actions).then((results) => sendResponse({ ok: true, results }));
          return true;   // async response
        default:
          return false;
      }
    } catch (error) {
      sendResponse({ ok: false, error: 'page_error' });
      return false;
    }
  });
})();
