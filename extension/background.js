/*
 * NyayaBot Portal Helper: background controller (service worker).
 *
 * Holds the Fill on Portal session, talks to the NyayaBot backend with the
 * short-lived session code, and asks the portal page (content/portal.js) to read
 * or fill. Every decision about which value goes where comes from the backend's
 * checked plan; nothing is filled without the user pressing Fill.
 *
 * State lives in chrome.storage.session (cleared when the browser closes), so it
 * survives this worker being suspended. The session code never goes to the panel.
 */
'use strict';

const ALLOWED_API_BASES = ['http://localhost:8000/api/v1', 'http://127.0.0.1:8000/api/v1'];
const STATE_KEY = 'nyaya_state';
const MAX_NAV_STEPS = 5;
const DEPENDENT_ROUNDS = 2;
const STAGE_STEP = { opening: 1, login: 2, finding: 3, not_found: 3, choice: 3, your_turn: 3, approval: 4, filling: 5, review: 6, done: 7 };

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// ── State ───────────────────────────────────────────────────────────────────

const EMPTY = { stage: 'idle', message: '', mood: 'wait', steps: [], frames: [0], visited: [] };

async function loadState() {
  const stored = await chrome.storage.session.get(STATE_KEY);
  return { ...EMPTY, ...(stored[STATE_KEY] || {}) };
}

async function saveState(state) {
  await chrome.storage.session.set({ [STATE_KEY]: state });
  chrome.runtime.sendMessage({ type: 'STATE', state: publicState(state) }).catch(() => {});
}

function publicState(state) {
  const { code, apiBase, ...rest } = state;   // the panel never needs the code
  return { ...rest, step: STAGE_STEP[state.stage] || 0, totalSteps: 7 };
}

async function update(changes) {
  const state = { ...(await loadState()), ...changes };
  await saveState(state);
  return state;
}

async function addStep(text) {
  const state = await loadState();
  state.steps = [...(state.steps || []), text].slice(-30);
  await saveState(state);
}

// ── Backend calls (Bearer session code) ─────────────────────────────────────

class SessionEnded extends Error {}

async function api(method, path, body) {
  const state = await loadState();
  if (!state.code || !state.apiBase) throw new SessionEnded('no session');
  const response = await fetch(state.apiBase + path, {
    method,
    headers: { Authorization: `Bearer ${state.code}`, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 401) throw new SessionEnded('expired');
  if (!response.ok) {
    let detail = '';
    try { const data = await response.json(); detail = (data.detail && (data.detail.message || data.detail)) || ''; } catch (e) { /* */ }
    throw new Error(typeof detail === 'string' && detail ? detail : `NyayaBot returned ${response.status}`);
  }
  return response.json();
}

// ── Talking to the portal page ──────────────────────────────────────────────

function hostAllowed(url, hosts) {
  try {
    const host = new URL(url).hostname.toLowerCase();
    return (hosts || []).some((allowed) => host === allowed || host.endsWith('.' + allowed));
  } catch (e) { return false; }
}

const injectedTabs = new Set();

/**
 * After the extension is reloaded, tabs that were already open keep no working
 * content script until they reload. Put the scripts back into such a tab once,
 * instead of asking the user to refresh a portal page they may be half-way through.
 */
async function ensureContentScripts(tabId) {
  if (injectedTabs.has(tabId) || !chrome.scripting) return;
  injectedTabs.add(tabId);
  try {
    await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, files: ['lib/page.js', 'content/portal.js'] });
  } catch (error) { /* not a portal page, or the tab is gone */ }
}

/** Message one frame; retries while a new page is still loading its content script. */
async function callFrame(tabId, frameId, message, waitMs = 10000) {
  const deadline = Date.now() + waitMs;
  for (let tries = 0; ; tries += 1) {
    try {
      const response = await chrome.tabs.sendMessage(tabId, message, { frameId });
      if (response) return response;
    } catch (error) {
      if (frameId === 0 && tries === 1) await ensureContentScripts(tabId);
    }
    if (Date.now() > deadline) return null;
    await sleep(500);
  }
}

/** Fields from every portal frame, each tagged "<frameNo>:<index>". */
async function scanAll(state) {
  const frameIds = Array.from(new Set([0, ...(state.frames || [])]));
  const frameMap = [];
  const fields = [];
  let topUrl = '';
  for (const frameId of frameIds) {
    const response = await callFrame(state.tabId, frameId, { cmd: 'scanFields' }, frameId === 0 ? 10000 : 1500);
    if (!response || !response.ok) continue;
    if (frameId === 0) topUrl = response.url;
    if (!hostAllowed(response.url, state.portal.hosts)) continue;
    const frameNo = frameMap.length;
    frameMap.push({ frameNo, frameId });
    for (const f of response.fields) {
      const { index, ...rest } = f;
      fields.push({ field_id: `${frameNo}:${index}`, ...rest });
    }
  }
  await update({ frames: frameMap.map((f) => f.frameId) });
  return { fields: fields.slice(0, 250), frameMap, topUrl };
}

// ── Flow ────────────────────────────────────────────────────────────────────

async function startSession({ code, apiBase }) {
  if (!ALLOWED_API_BASES.includes(String(apiBase).replace(/\/+$/, ''))) return { ok: false, error: 'unknown_backend' };
  await chrome.storage.session.set({ [STATE_KEY]: { ...EMPTY, code, apiBase: apiBase.replace(/\/+$/, '') } });
  let info;
  try {
    info = await api('GET', '/autofill/session');
  } catch (error) {
    await chrome.storage.session.remove(STATE_KEY);
    return { ok: false, error: error instanceof SessionEnded ? 'session_expired' : 'backend_unreachable' };
  }
  // The portal comes from the server's answer, not from the page that sent the code.
  const portal = info.portal;
  const tab = await chrome.tabs.create({ url: portal.url, active: true });
  let sidePanelOpened = false;
  try { await chrome.sidePanel.open({ tabId: tab.id }); sidePanelOpened = true; } catch (e) { /* needs a click on the icon */ }
  await update({
    stage: 'login', mood: 'wave', tabId: tab.id, portal, caseTitle: info.case_title, values: info.values,
    message: `I've opened ${portal.label} for you. Please log in with your own details first. I'll wait right here!`,
    steps: [`Opened ${portal.label}.`], frames: [0], visited: [], plan: null, results: null,
  });
  return { ok: true, sidePanelOpened };
}

/**
 * Does this page still need the user to sign in?
 *  - trust "none": the user said they are on the form; believe them.
 *  - trust "login": the user said they logged in; only a visible password or OTP box
 *    (a sign-in form still on screen) overrides that, never weaker hints.
 *  - trust "auto": a page reached by NyayaBot's own click; use the full check.
 */
function stillNeedsLogin(signals, trust) {
  if (trust === 'none') return false;
  if (trust === 'login') return Boolean(signals.password || signals.otp);
  return Boolean(signals.likely_login_wall);
}

// ── Telling the user where they are and what to do next ─────────────────────

async function pageContextOf(state) {
  const reply = await callFrame(state.tabId, 0, { cmd: 'pageContext' }, 2000);
  return reply && reply.ok ? reply.context || {} : {};
}

/** "You're on “Case Details” (step 1 of 6). " or "" when the page gives no name. */
function placeText(ctx) {
  const name = ctx && (ctx.step_label || ctx.title);
  if (!name) return '';
  return ctx.steps ? `You're on “${name}” (step ${ctx.step} of ${ctx.steps}). ` : `You're on “${name}”. `;
}

const quoteList = (labels, max = 4) => {
  const unique = Array.from(new Set(labels.filter(Boolean)));
  const shown = unique.slice(0, max).map((l) => `“${l}”`).join(', ');
  return unique.length > max ? `${shown} and ${unique.length - max} more` : shown;
};

/** Boxes the user fills: everything the plan left for them. */
const leftForYou = (plan) => ['conflict', 'missing', 'manual', 'unmatched']
  .flatMap((k) => (plan && plan[k]) || []).map((n) => n.field_label);

function approvalMessage(plan, ctx) {
  const place = placeText(ctx);
  if (!plan.actions.length) {
    return `${place}I found the form, but nothing on this page matches your case details. `
      + (leftForYou(plan).length ? `The boxes are listed below with what to enter. ` : '') + 'You can fill it yourself.';
  }
  const ready = plan.actions.filter((a) => !a.needs_approval);
  const check = plan.actions.filter((a) => a.needs_approval);
  const parts = [`${place}I can fill ${plan.actions.length} box(es) from your case.`];
  if (ready.length) parts.push(`${ready.length} ${ready.length === 1 ? 'is' : 'are'} ticked and ready.`);
  if (check.length) parts.push(`${check.length} marked “please check” ${check.length === 1 ? 'is' : 'are'} unticked: tick ${check.length === 1 ? 'it' : 'them'} if right (${quoteList(check.map((a) => a.field_label), 3)}).`);
  const left = leftForYou(plan);
  if (left.length) parts.push(`${left.length} ${left.length === 1 ? 'is' : 'are'} for you (listed below).`);
  parts.push('Then press “Fill selected fields”.');
  return parts.join(' ');
}

function reviewMessage({ filled, failed, skipped, left, ctx }) {
  const parts = [];
  parts.push(filled ? `Done: I filled ${filled} box(es)${ctx && (ctx.step_label || ctx.title) ? ` on “${ctx.step_label || ctx.title}”` : ''}.`
    : "I couldn't fill any box on this page.");
  if (failed) parts.push(`${failed} didn't stick (marked ✗ below): please enter ${failed === 1 ? 'it' : 'them'} yourself.`);
  if (left.length) parts.push(`Still for you: ${quoteList(left)}.`);
  if (skipped) parts.push(`${skipped} you left unticked ${skipped === 1 ? 'is' : 'are'} included there.`);
  parts.push(ctx && ctx.next
    ? `When this page is complete, press “${ctx.next}” on the portal, then “Fill the next page of the form” here.`
    : 'When this page is complete, go to the next page on the portal, then press “Fill the next page of the form” here.');
  return parts.join(' ');
}

/**
 * The server asked to see the options of some portal dropdowns: open each list, read
 * its option texts, close it, and return the fields with options added (or null).
 */
async function peekOptions(state, fields, frameMap, fieldIds) {
  const byFrame = new Map();
  for (const id of fieldIds) {
    const [frameNo, index] = id.split(':').map(Number);
    const frame = frameMap.find((f) => f.frameNo === frameNo);
    if (!frame) continue;
    if (!byFrame.has(frame.frameId)) byFrame.set(frame.frameId, { frameNo, indices: [] });
    byFrame.get(frame.frameId).indices.push(index);
  }
  let found = 0;
  const options = {};
  for (const [frameId, { frameNo, indices }] of byFrame) {
    const reply = await callFrame(state.tabId, frameId, { cmd: 'peekOptions', indices }, 6000);
    for (const [index, list] of Object.entries((reply && reply.ok && reply.options) || {})) {
      options[`${frameNo}:${index}`] = (list || []).slice(0, 400);
      found += 1;
    }
  }
  if (!found) return null;
  return fields.map((f) => (options[f.field_id] ? { ...f, options: options[f.field_id] } : f));
}

/** Open pop-ups without form boxes inside: these need the user (NyayaBot never presses their buttons). */
async function openDialogs(state) {
  const reply = await callFrame(state.tabId, 0, { cmd: 'scanDialogs' }, 2000);
  const dialogs = reply && reply.ok ? reply.dialogs || [] : [];
  return { url: reply && reply.url, dialogs: dialogs.filter((d) => !d.fields).map(({ title, options, buttons }) => ({ title, options, buttons })) };
}

async function runToForm(trust = 'auto') {
  let state = await update({ stage: 'finding', mood: 'think', message: 'Looking for the complaint form…', plan: null });
  for (let attempt = 0; ; attempt += 1) {
    state = await loadState();
    const top = await callFrame(state.tabId, 0, { cmd: 'loginSignals' });
    if (!top) {
      return update({ stage: 'not_found', mood: 'concerned', message: "I can't reach the portal tab. Is it still open?" });
    }
    if (!hostAllowed(top.url, state.portal.hosts)) {
      return update({ stage: 'not_found', mood: 'concerned', message: `This tab has left ${state.portal.label}. Go back to it, then press "Fill this page".` });
    }
    const s = top.signals;
    if (stillNeedsLogin(s, attempt === 0 ? trust : 'auto')) {
      await addStep(`Sign-in check: password box ${s.password}, OTP box ${s.otp}, CAPTCHA ${s.captcha}, fields ${s.inputs}.`);
      return update({ stage: 'login', mood: 'wave',
        message: 'This page still shows a sign-in or OTP box. Finish logging in yourself, then press "I\'m logged in". '
          + 'If you are already logged in, press "I\'m already on the complaint form".' });
    }
    const popups = await openDialogs(state);
    if (popups.dialogs.length) {
      const reply = await api('POST', '/autofill/navigate', { page_url: popups.url || top.url, links: [], visited: state.visited || [], dialogs: popups.dialogs });
      await addStep(`A pop-up is open: “${(popups.dialogs[0].title || 'untitled').slice(0, 60)}”.`);
      return update({ stage: 'your_turn', mood: 'wave', choice: null,
        message: reply.message || 'The portal opened a pop-up. Answer it yourself, then press “Done, continue”.' });
    }
    const ctx = await pageContextOf(state);
    let { fields, frameMap, topUrl } = await scanAll(state);
    let plan = await api('POST', '/autofill/plan', { page_url: topUrl, fields });
    if ((plan.peek || []).length) {
      // Portal dropdowns: read their options once (nothing is chosen), then plan again.
      const withOptions = await peekOptions(state, fields, frameMap, plan.peek);
      if (withOptions) {
        await addStep(`Read the options of ${plan.peek.length} list(s) on this page (nothing chosen).`);
        fields = withOptions;
        plan = await api('POST', '/autofill/plan', { page_url: topUrl, fields });
      }
    }
    if (plan.actions.length || plan.reason !== 'not_a_complaint_form') {
      await addStep(`Found a form with ${fields.length} field(s).`);
      return update({ stage: 'approval', mood: 'think', plan, frameMap, context: ctx, message: approvalMessage(plan, ctx) });
    }
    if (attempt >= MAX_NAV_STEPS) break;
    const links = await callFrame(state.tabId, 0, { cmd: 'scanLinks' });
    // The server decides every step from the portal's profile; this file knows no portal.
    const choice = links && links.ok
      ? await api('POST', '/autofill/navigate', { page_url: links.url, links: links.links.slice(0, 600), visited: state.visited || [] })
      : { index: null };
    // Headings the server did not recognise, so it filled nothing under them.
    const unknown = (plan.unknown_sections || []).slice(0, 4);
    if (unknown.length) await addStep(`Section headings I don't recognise here: ${unknown.map((h) => `“${h}”`).join(', ')}.`);
    if (choice.action === 'user') {
      return update({ stage: 'your_turn', mood: 'wave', choice: null, message: placeText(ctx) + (choice.message || '') });
    }
    if (choice.action === 'next') {
      // A route step: the page's one plain Next (never Submit, Save or Pay).
      const next = await callFrame(state.tabId, 0, { cmd: 'clickNext' }, 3000);
      if (!next || !next.ok) {
        return update({ stage: 'not_found', mood: 'wait', choice: null,
          message: 'I could not find one safe Next button on this step, so I stopped. I will not guess or submit anything. '
            + 'Press Next yourself, then press “Fill this page”.' });
      }
      state = await update({ visited: [...(state.visited || []), choice.step || 'Next'].slice(-20) });
      await addStep(`Pressed “${next.text || 'Next'}”.`);
      await sleep(2500);
      continue;
    }
    if (choice.index === null || choice.index === undefined) {
      if (choice.offer_choices === false) {
        // This portal has no "what happened" questions: its page cards are never offered as answers.
        await addStep('No form to fill and no next filing step on this page.');
        const sections = unknown.length
          ? `This page's sections (${unknown.map((h) => `“${h}”`).join(', ')}) aren't ones I know, so I filled nothing. `
          : '';
        return update({ stage: 'not_found', mood: 'wait', choice: null,
          message: placeText(ctx) + sections + (choice.message || "I couldn't find anything to fill on this page. Fill it yourself, then press “Fill this page”.") });
      }
      // No link to follow (or we're inside the complaint area): is this a
      // multiple-choice question, like "What type of complaint is this?"
      const offered = await offerChoice(state);
      if (offered) return offered;
      break;
    }
    const clicked = await callFrame(state.tabId, 0, { cmd: 'clickLink', index: choice.index });
    if (!clicked || !clicked.ok) break;
    state = await update({ visited: [...(state.visited || []), choice.step || choice.text].slice(-20) });
    await addStep(`Clicked “${choice.text}”.`);
    await sleep(2500);
  }
  return update({ stage: 'not_found', mood: 'concerned',
    message: 'I couldn\'t find form fields to fill here. If the portal asks you to choose something first '
      + '(like what happened, a category, or "Have you lost money?"), choose it yourself, then press "Fill this page".' });
}

/** One Activity line: what the answer scan skipped and why, with a few examples. */
function describeChoiceScan(s) {
  const dropped = Object.entries(s.dropped || {}).map(([why, n]) => {
    const seen = (s.samples && s.samples[why]) || [];
    const examples = seen.length ? ` (${seen.map((t) => `“${t}”`).join(', ')}${n > seen.length ? ', …' : ''})` : '';
    return `${n} ${why.replace(/_/g, ' ')}${examples}`;
  }).join(', ');
  const layout = Object.entries(s.chrome || {}).sort((a, b) => b[1] - a[1]).slice(0, 3)
    .map(([why, n]) => `${why} ×${n}`).join(', ');
  return `Looked for answer options: ${s.clickable} clickable, ${s.options} kept, ${s.groups} question(s)`
    + (dropped ? `; skipped ${dropped}` : '') + (layout ? `; menu or layout found by ${layout}` : '') + '.';
}

/** Show the page's question with a suggested answer. Nothing is clicked until the user approves. */
async function offerChoice(state) {
  const scan = await callFrame(state.tabId, 0, { cmd: 'scanChoices' }, 3000);
  if (!scan || !scan.ok || !scan.groups || !scan.groups.length) {
    // Explains, in the Activity list, why no answer options were found on this page.
    if (scan && scan.stats) await addStep(describeChoiceScan(scan.stats));
    return null;
  }
  // A question NyayaBot already answered can stay on screen above the next one:
  // offer a new question first.
  const answered = state.answered || [];
  const fresh = scan.groups.filter((g) => !answered.some((a) => sameQuestion(g, a)));
  const group = (fresh.length ? fresh : scan.groups)[0];
  let proposal = null;
  try {
    const result = await api('POST', '/autofill/choose', { page_url: scan.url, groups: [group] });
    proposal = (result.proposals || []).find((p) => p.group === group.group) || null;
  } catch (error) {
    if (error instanceof SessionEnded) throw error;   // otherwise just offer the options without a suggestion
  }
  const selected = group.options.find((o) => o.selected) || null;
  const question = group.question || '';
  await addStep(`Question: “${(question || 'no question text').slice(0, 90)}” (${group.options.length} options).`);
  const asks = question ? `This page asks: “${question}”. ` : 'This page asks you to choose one option. ';
  let message;
  if (selected) {
    message = `You've chosen “${selected.text}”. Press “Choose and continue” and I'll press Next.`;
  } else if (proposal) {
    message = `${asks}I suggest “${proposal.option_text}”. ${proposal.reason} `
      + 'Check it (or pick another), then press “Choose and continue”.';
  } else {
    message = `${asks}Pick the option that fits your case, then press “Choose and continue”.`;
  }
  return update({ stage: 'choice', mood: proposal ? 'think' : 'wait', message,
    choice: { group, proposal, selected: selected ? selected.index : null } });
}

/** Same question on the page as before? Compared by question text and option texts. */
function sameQuestion(a, b) {
  if (!a || !b) return false;
  if (a.question && b.question && a.question === b.question) return true;
  const before = new Set(b.options.map((o) => o.text));
  const shared = a.options.filter((o) => before.has(o.text)).length;
  return shared >= Math.ceil(Math.min(a.options.length, b.options.length) * 0.6);
}

/** Click the option the user approved, press a plain Next, then look at the new page. */
async function applyChoice(index) {
  const state = await loadState();
  const group = state.choice && state.choice.group;
  const option = group && group.options.find((o) => o.index === Number(index));
  if (!option) {
    return update({ stage: 'not_found', mood: 'concerned', message: 'Please pick one of the options first.' });
  }
  const clicked = await callFrame(state.tabId, 0, { cmd: 'clickChoice', index: option.index, text: option.text }, 3000);
  if (!clicked || !clicked.ok) {
    return update({ stage: 'not_found', mood: 'concerned',
      message: 'I couldn\'t click that option. Click it yourself on the page, then press "Fill this page".' });
  }
  if (!clicked.already) await addStep(`Chose “${option.text.slice(0, 90)}”.`);
  // Remembered, so this question is not offered again if it stays on screen.
  const answered = [...(state.answered || []),
    { question: group.question, options: group.options.map((o) => ({ text: o.text })) }].slice(-10);
  await update({ answered });
  await sleep(800);
  // Some portals show the next question as soon as an answer is clicked. Pressing
  // Next then would skip that new question unanswered, so press it only when no
  // other unanswered question has appeared.
  const after = await callFrame(state.tabId, 0, { cmd: 'scanChoices' }, 3000);
  const openGroups = after && after.ok ? (after.groups || []).filter((g) => !g.options.some((o) => o.selected)) : [];
  const newQuestion = openGroups.find((g) => !answered.some((a) => sameQuestion(g, a)));
  if (newQuestion) {
    await addStep('A new question appeared, so I did not press Next.');
    await update({ choice: null });
    return runToForm('none');
  }
  const next = await callFrame(state.tabId, 0, { cmd: 'clickNext', near: option.index }, 3000);
  if (!next || !next.ok) {
    // Offering the same question again would go round in circles: hand over instead.
    await addStep(next && next.reason === 'ambiguous'
      ? 'There is more than one Next button here, so I did not press one.'
      : 'I could not find a plain Next button here.');
    return update({ stage: 'not_found', mood: 'wait', choice: null,
      message: `I chose “${option.text.slice(0, 90)}”. Now press the portal's Next (or Continue) button yourself, `
        + 'then press “Fill this page”.' });
  }
  await addStep(`Pressed “${next.text || 'Next'}”.`);
  await sleep(2000);
  await update({ choice: null });
  if (await stillOnSameQuestion(state.tabId, group, answered)) {
    await addStep('The page did not move on after Next.');
    return update({ stage: 'not_found', mood: 'concerned',
      message: 'I pressed Next, but the page didn\'t move on. Check on the page that your answer is selected and '
        + 'nothing is marked in red, press Next yourself, then press “Fill this page”.' });
  }
  return runToForm('none');
}

/** After Next: is the same question still showing, with no new question and no form fields? */
async function stillOnSameQuestion(tabId, group, answered) {
  const scan = await callFrame(tabId, 0, { cmd: 'scanChoices' }, 3000);
  const groups = scan && scan.ok ? (scan.groups || []) : [];
  if (!groups.some((g) => sameQuestion(g, group))) return false;
  if (groups.some((g) => !answered.some((a) => sameQuestion(g, a)))) return false;
  const page = await callFrame(tabId, 0, { cmd: 'scanFields' }, 3000);
  return !(page && page.ok && page.fields && page.fields.length);
}

async function fillApproved(fieldIds) {
  let state = await loadState();
  if (!state.plan) return state;
  const chosen = new Set(fieldIds || []);
  let actions = state.plan.actions.filter((a) => chosen.has(a.field_id));
  if (!actions.length) {
    const unticked = state.plan.actions.filter((a) => a.needs_approval).map((a) => a.field_label);
    return update({ stage: 'approval', mood: 'wait',
      message: "You didn't tick anything, so I filled nothing. "
        + (unticked.length ? `Tick the boxes you want in the list below (for example ${quoteList(unticked, 2)}), then press “Fill selected fields”. ` : '')
        + 'Or fill them yourself on the portal.' });
  }
  await update({ stage: 'filling', mood: 'think', message: `Filling ${actions.length} field(s)…` });
  const labels = Object.fromEntries(state.plan.actions.map((a) => [a.field_id, a.field_label]));
  const attempted = new Set();
  const results = [];
  let frameMap = state.frameMap || [{ frameNo: 0, frameId: 0 }];
  let lastPlan = state.plan;
  for (let round = 0; round <= DEPENDENT_ROUNDS && actions.length; round += 1) {
    const byFrame = new Map();
    for (const action of actions) {
      attempted.add(action.field_id);
      labels[action.field_id] = action.field_label;
      const frameNo = Number(action.field_id.split(':')[0]);
      const index = Number(action.field_id.split(':')[1]);
      const frame = frameMap.find((f) => f.frameNo === frameNo);
      if (!frame) { results.push({ field_id: action.field_id, status: 'failed', reason: 'frame_gone' }); continue; }
      if (!byFrame.has(frame.frameId)) byFrame.set(frame.frameId, []);
      byFrame.get(frame.frameId).push({ index, field_id: action.field_id, kind: action.kind, value: action.value, replace_today: Boolean(action.replace_today) });
    }
    for (const [frameId, frameActions] of byFrame) {
      const response = await callFrame(state.tabId, frameId, { cmd: 'fill', actions: frameActions }, 3000);
      if (response && response.ok) results.push(...response.results);
      else frameActions.forEach((a) => results.push({ field_id: a.field_id, status: 'failed', reason: 'page_unreachable' }));
    }
    if (round === DEPENDENT_ROUNDS) break;
    // Dependent fields (e.g. district after state) appear a moment later: look again.
    await sleep(1500);
    state = await loadState();
    const scan = await scanAll(state);
    frameMap = scan.frameMap;
    try { lastPlan = await api('POST', '/autofill/plan', { page_url: scan.topUrl, fields: scan.fields }); } catch (e) { break; }
    actions = lastPlan.actions.filter((a) => !a.needs_approval && !attempted.has(a.field_id));
  }
  const withLabels = results.map((r) => ({ ...r, field_label: labels[r.field_id] || r.field_id }));
  const filled = withLabels.filter((r) => r.status === 'filled').length;
  const failed = withLabels.filter((r) => r.status === 'failed').length;
  try { await api('POST', '/autofill/report', { event: 'page_filled', filled, failed }); } catch (e) { /* reporting is best effort */ }
  await addStep(`Filled ${filled} field(s)${failed ? `, ${failed} could not be filled` : ''}.`);
  const notChosen = state.plan.actions.filter((a) => !chosen.has(a.field_id)).map((a) => a.field_label);
  const ctx = await pageContextOf(state);
  return update({
    stage: 'review', mood: filled ? 'happy' : 'concerned', results: withLabels, plan: lastPlan, frameMap, context: ctx,
    message: reviewMessage({ filled, failed, skipped: notChosen.length, left: [...notChosen, ...leftForYou(lastPlan)], ctx }),
  });
}

async function confirmReview() {
  await api('POST', '/autofill/report', { event: 'review_confirmed' });
  await addStep('You confirmed the review.');
  return update({ stage: 'done', mood: 'happy',
    message: "Great! Now submit the form yourself on the portal. NyayaBot can't see whether the portal accepted it, so keep the portal's acknowledgement number." });
}

async function cancelSession() {
  const state = await loadState();
  if (state.code) { try { await api('DELETE', '/autofill/session'); } catch (e) { /* already ended */ } }
  await chrome.storage.session.remove(STATE_KEY);
  await saveState({ ...EMPTY, message: 'Session closed. Nothing was submitted by NyayaBot.' });
}

// ── Wiring ──────────────────────────────────────────────────────────────────

let running = false;

async function guarded(task) {
  if (running) return { ok: false, error: 'busy' };
  running = true;
  try {
    await task();
    return { ok: true };
  } catch (error) {
    if (error instanceof SessionEnded) {
      await chrome.storage.session.remove(STATE_KEY);
      await saveState({ ...EMPTY, stage: 'expired', mood: 'concerned',
        message: 'This session has ended. Start again from your case in NyayaBot with "Fill on Portal".' });
    } else {
      await update({ stage: 'error', mood: 'concerned', message: `Something went wrong: ${String(error.message || error).slice(0, 160)}` });
    }
    return { ok: false, error: 'failed' };
  } finally {
    running = false;
  }
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
});
chrome.runtime.onStartup.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
});

chrome.tabs.onRemoved.addListener(async (tabId) => {
  const state = await loadState();
  if (state.tabId === tabId && state.code) {
    await update({ stage: 'tab_closed', mood: 'concerned', message: 'The portal tab was closed. Press "Open the portal again" to continue.' });
  }
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || typeof message.type !== 'string') return false;

  // From the NyayaBot page (content/bridge.js only).
  if (message.type === 'START_SESSION') {
    if (!sender.tab || !sender.url || !/^http:\/\/(localhost|127\.0\.0\.1):3000\//.test(sender.url)) {
      sendResponse({ ok: false, error: 'not_nyayabot' });
      return false;
    }
    startSession(message).then(sendResponse, () => sendResponse({ ok: false, error: 'extension_error' }));
    return true;
  }

  // From portal frames.
  if (message.type === 'FRAME_HELLO') {
    loadState().then((state) => {
      if (sender.tab && sender.tab.id === state.tabId && typeof sender.frameId === 'number') {
        const frames = Array.from(new Set([0, ...(state.frames || []), sender.frameId])).slice(0, 20);
        update({ frames });
      }
    });
    return false;
  }

  // From the side panel (an extension page, so it has no sender.tab).
  if (sender.tab) return false;
  const commands = {
    GET_STATE: async () => ({ ok: true, state: publicState(await loadState()) }),
    LOGGED_IN: () => guarded(() => runToForm('login')),
    FILL_THIS_PAGE: () => guarded(() => runToForm('none')),
    NEXT_PAGE: () => guarded(async () => { await update({ visited: [] }); await runToForm(); }),
    APPROVE: () => guarded(() => fillApproved(message.fieldIds)),
    APPROVE_CHOICE: () => guarded(() => applyChoice(message.index)),
    CONFIRM_REVIEW: () => guarded(confirmReview),
    CANCEL: () => cancelSession().then(() => ({ ok: true })),
    FOCUS_PORTAL: async () => {
      const state = await loadState();
      if (state.tabId) await chrome.tabs.update(state.tabId, { active: true }).catch(() => {});
      return { ok: true };
    },
    REOPEN: () => guarded(async () => {
      const state = await loadState();
      const tab = await chrome.tabs.create({ url: state.portal.url, active: true });
      await update({ tabId: tab.id, frames: [0], visited: [], stage: 'login', mood: 'wave',
        message: 'Portal opened again. Log in if it asks, then press "I\'m logged in".' });
    }),
  };
  const handler = commands[message.type];
  if (!handler) return false;
  handler().then(sendResponse, () => sendResponse({ ok: false }));
  return true;
});
