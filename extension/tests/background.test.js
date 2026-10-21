// Offline tests for background.js with a fake Chrome API and a fake NyayaBot backend.
// Checks the safety-relevant flow: who may start a session, which backend is
// trusted, what the panel can see, and that only approved fields are filled.
'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(path.join(__dirname, '..', 'background.js'), 'utf8');
const API = 'http://localhost:8000/api/v1';
const NYAYABOT_TAB = { tab: { id: 1 }, url: 'http://localhost:3000/chat' };
const PANEL = {};   // extension pages have no sender.tab

function makeWorld({ portalPages, plans, navChoice = { index: 3, text: 'Register a Complaint' }, portal, sessionStatus = 200, chooseReply = { proposals: [], ai_used: false }, advanceOnChoice = false, nextReply = { ok: true, text: 'Next' } } = {}) {
  const store = {};
  const calls = { fetch: [], tabMessages: [], created: [] };
  let listener = null;
  let page = 0;
  let navIndex = 0;
  const pages = portalPages || [{ url: 'https://cybercrime.gov.in/', signals: { likely_login_wall: false }, fields: [] }];

  const chrome = {
    storage: { session: {
      get: async (key) => (key in store ? { [key]: JSON.parse(JSON.stringify(store[key])) } : {}),
      set: async (obj) => { Object.assign(store, JSON.parse(JSON.stringify(obj))); },
      remove: async (key) => { delete store[key]; },
    } },
    runtime: {
      onMessage: { addListener: (fn) => { listener = fn; } },
      onInstalled: { addListener: () => {} }, onStartup: { addListener: () => {} },
      sendMessage: () => Promise.resolve(),
    },
    sidePanel: { open: () => Promise.reject(new Error('needs gesture')), setPanelBehavior: () => Promise.resolve() },
    tabs: {
      onRemoved: { addListener: () => {} },
      create: async ({ url }) => { calls.created.push(url); return { id: 7 }; },
      update: async () => ({}),
      sendMessage: async (tabId, message, { frameId }) => {
        calls.tabMessages.push({ tabId, frameId, message });
        if (frameId !== 0) throw new Error('no such frame');
        const current = pages[Math.min(page, pages.length - 1)];
        switch (message.cmd) {
          case 'loginSignals': return { ok: true, url: current.url, signals: current.signals };
          case 'scanFields': return { ok: true, url: current.url, fields: current.fields };
          case 'scanLinks': return { ok: true, url: current.url, links: current.links || [] };
          case 'clickLink': page += 1; return { ok: true, text: 'clicked' };
          case 'scanChoices': return { ok: true, url: current.url, groups: current.groups || [], stats: current.stats || { clickable: 0, options: 0, groups: 0, dropped: {} } };
          case 'clickChoice': if (advanceOnChoice) page += 1; return { ok: true, text: message.text };
          case 'clickNext': if (nextReply.ok) page += 1; return nextReply;
          case 'pageContext': return { ok: true, url: current.url, context: current.context || {} };
          case 'scanDialogs': return { ok: true, url: current.url, dialogs: current.dialogs || [] };
          case 'peekOptions': calls.peeked = (calls.peeked || 0) + 1; return { ok: true, options: current.peek || {} };
          case 'fill': return { ok: true, results: message.actions.map((a) => ({ field_id: a.field_id, status: 'filled', reason: '' })) };
          default: return null;
        }
      },
    },
  };

  let planIndex = 0;
  async function fetchFake(url, init = {}) {
    calls.fetch.push({ url, method: init.method, body: init.body ? JSON.parse(init.body) : undefined, auth: init.headers.Authorization });
    const route = `${init.method} ${url.replace(API, '')}`;
    const reply = (status, body) => ({ ok: status < 400, status, json: async () => body });
    if (route === 'GET /autofill/session') {
      if (sessionStatus !== 200) return reply(sessionStatus, { detail: 'ended' });
      return reply(200, { portal: portal || { key: 'cybercrime', label: 'National Cyber Crime Reporting Portal',
        url: 'https://cybercrime.gov.in/', hosts: ['cybercrime.gov.in'] }, case_title: 'UPI fraud', values: [] });
    }
    if (route === 'POST /autofill/plan') return reply(200, (plans || [])[Math.min(planIndex++, (plans || []).length - 1)]);
    if (route === 'POST /autofill/navigate') {
      const choices = Array.isArray(navChoice) ? navChoice : [navChoice];
      return reply(200, choices[Math.min(navIndex++, choices.length - 1)]);
    }
    if (route === 'POST /autofill/choose') return reply(200, chooseReply);
    if (route === 'POST /autofill/report' || route === 'DELETE /autofill/session') return reply(200, { ok: true });
    return reply(404, {});
  }

  const context = vm.createContext({
    chrome, fetch: fetchFake, URL, console, Map, Set, Promise, JSON, Object, Array, Number, String, Date, Math, Error,
    setTimeout: (fn) => setImmediate(fn),     // no real waiting in tests
  });
  vm.runInContext(SOURCE, context);

  function send(message, sender) {
    return new Promise((resolve) => {
      const async = listener(message, sender, resolve);
      if (!async) resolve(undefined);
    });
  }
  return { send, calls, store };
}

const plain = (v) => JSON.parse(JSON.stringify(v));
const START = { type: 'START_SESSION', code: 'secret-code-123', apiBase: API, portal: { url: 'https://evil.example/' } };
const PLAN = {
  actions: [
    { field_id: '0:4', field_label: 'Bank', key: 'bank_name', value_label: 'Bank', value: 'State Bank of India', kind: 'select', source: 'portal', needs_approval: false },
    { field_id: '0:6', field_label: 'UTR', key: 'transaction_id', value_label: 'UTR', value: 'UTR1', kind: 'text', source: 'portal', needs_approval: false },
    { field_id: '0:20', field_label: 'Naam', key: 'complainant_name', value_label: 'Name', value: 'Asha', kind: 'text', source: 'ai', needs_approval: true },
  ],
  missing: [], conflict: [], manual: [], sensitive: [], already_filled: [], unmatched: [], reason: '', ai_used: true,
};

test('only the NyayaBot page can start a session', async () => {
  const world = makeWorld();
  assert.deepEqual(plain(await world.send(START, { tab: { id: 2 }, url: 'https://evil.example/page' })), { ok: false, error: 'not_nyayabot' });
  assert.deepEqual(plain(await world.send(START, PANEL)), { ok: false, error: 'not_nyayabot' });
  assert.equal(world.calls.created.length, 0);
});

test('an unknown backend address is refused before anything is opened', async () => {
  const world = makeWorld();
  const reply = await world.send({ ...START, apiBase: 'https://attacker.example/api/v1' }, NYAYABOT_TAB);
  assert.deepEqual(plain(reply), { ok: false, error: 'unknown_backend' });
  assert.equal(world.calls.fetch.length, 0);
  assert.equal(world.calls.created.length, 0);
});

test('the portal opened is the one the server names, never one supplied by the page', async () => {
  const world = makeWorld();
  const reply = await world.send(START, NYAYABOT_TAB);
  assert.equal(reply.ok, true);
  assert.deepEqual(world.calls.created, ['https://cybercrime.gov.in/']);
  assert.equal(world.calls.fetch[0].auth, 'Bearer secret-code-123');
});

test('the side panel never receives the session code', async () => {
  const world = makeWorld();
  await world.send(START, NYAYABOT_TAB);
  const { state } = await world.send({ type: 'GET_STATE' }, PANEL);
  assert.equal(state.stage, 'login');
  assert.ok(!JSON.stringify(state).includes('secret-code-123'));
  assert.ok(!('code' in state) && !('apiBase' in state));
});

test('an expired code ends cleanly and opens nothing', async () => {
  const world = makeWorld({ sessionStatus: 401 });
  assert.deepEqual(plain(await world.send(START, NYAYABOT_TAB)), { ok: false, error: 'session_expired' });
  assert.equal(world.calls.created.length, 0);
});

test('a page that still asks for login keeps the user in the login step', async () => {
  const world = makeWorld({ portalPages: [{ url: 'https://cybercrime.gov.in/login', signals: { likely_login_wall: true, password: 0, otp: 1, captcha: 1, inputs: 3 }, fields: [] }] });
  await world.send(START, NYAYABOT_TAB);
  await world.send({ type: 'LOGGED_IN' }, PANEL);
  const { state } = await world.send({ type: 'GET_STATE' }, PANEL);
  assert.equal(state.stage, 'login');
  assert.ok(!world.calls.fetch.some((c) => c.url.endsWith('/autofill/plan')), 'no plan is asked for on a login page');
});

test('from the landing page it clicks the link the backend chose, then shows the plan for approval', async () => {
  const world = makeWorld({
    portalPages: [
      { url: 'https://cybercrime.gov.in/', signals: { likely_login_wall: false }, fields: [], links: [{ index: 3, text: 'Register a Complaint' }] },
      { url: 'https://cybercrime.gov.in/form', signals: { likely_login_wall: false }, fields: [{ index: 4, tag: 'select', type: 'select' }] },
    ],
    plans: [{ ...PLAN, actions: [], reason: 'not_a_complaint_form' }, PLAN],
  });
  await world.send(START, NYAYABOT_TAB);
  await world.send({ type: 'LOGGED_IN' }, PANEL);
  const clicks = world.calls.tabMessages.filter((m) => m.message.cmd === 'clickLink');
  assert.deepEqual(clicks.map((c) => c.message.index), [3]);
  const { state } = await world.send({ type: 'GET_STATE' }, PANEL);
  assert.equal(state.stage, 'approval');
  assert.equal(state.plan.actions.length, 3);
  assert.ok(!world.calls.tabMessages.some((m) => m.message.cmd === 'fill'), 'nothing is filled before approval');
});

const EJAGRITI = { key: 'ejagriti', label: 'e-Jagriti Consumer Disputes Portal',
  url: 'https://e-jagriti.gov.in/login', hosts: ['e-jagriti.gov.in'] };
const NO_FORM = { ...PLAN, actions: [], reason: 'not_a_complaint_form' };

// e-Jagriti's steps live in its server profile; these tests check the extension only
// follows what the server says (it has no portal-specific code of its own).
const EJAGRITI_HINT = 'I know e-Jagriti\'s Complete Profile pages and File New Case → Case Type → Consumer Complaint.';

test('when the server says a portal has no case questions, page cards are never offered as answers', async () => {
  const world = makeWorld({
    portal: EJAGRITI,
    portalPages: [{ url: 'https://e-jagriti.gov.in/', signals: { likely_login_wall: false }, fields: [],
      groups: [{ group: 0, question: 'Citizens Share Their Success Stories', options: [
        { index: 0, text: 'DC/44/CC/285/2026', selected: false },
        { index: 1, text: 'DC/72/CC/88/2026', selected: false },
      ] }] }],
    plans: [NO_FORM], navChoice: { index: null, text: '', offer_choices: false, message: EJAGRITI_HINT },
  });
  await world.send(START, NYAYABOT_TAB);
  await world.send({ type: 'LOGGED_IN' }, PANEL);
  const { state } = await world.send({ type: 'GET_STATE' }, PANEL);
  assert.equal(state.stage, 'not_found');
  assert.equal(state.message, EJAGRITI_HINT, 'the message is the server\'s, not one written into the extension');
  assert.ok(!world.calls.tabMessages.some((m) => ['scanChoices', 'clickChoice', 'clickNext'].includes(m.message.cmd)));
  assert.ok(!world.calls.fetch.some((c) => c.url.endsWith('/autofill/choose')));
});

test('unrecognised section headings are named in the message and the Activity list', async () => {
  const world = makeWorld({
    portal: EJAGRITI,
    portalPages: [{ url: 'https://e-jagriti.gov.in/profile', signals: { likely_login_wall: false },
      fields: [{ index: 1, tag: 'input', type: 'text' }] }],
    plans: [{ ...NO_FORM, unknown_sections: ['Bank Account Details'] }],
    navChoice: { index: null, text: '', offer_choices: false, message: EJAGRITI_HINT },
  });
  await world.send(START, NYAYABOT_TAB);
  await world.send({ type: 'LOGGED_IN' }, PANEL);
  const { state } = await world.send({ type: 'GET_STATE' }, PANEL);
  assert.match(state.message, /sections \(“Bank Account Details”\) aren't ones I know/);
  assert.ok(state.steps.includes('Section headings I don\'t recognise here: “Bank Account Details”.'));
});

test('the extension follows server-chosen route steps, including one plain Next, then shows the plan', async () => {
  const world = makeWorld({
    portal: EJAGRITI,
    portalPages: [
      { url: 'https://e-jagriti.gov.in/login', signals: { likely_login_wall: false }, fields: [],
        links: [{ index: 1, text: 'Open menu' }] },
      { url: 'https://e-jagriti.gov.in/login', signals: { likely_login_wall: false }, fields: [],
        links: [{ index: 2, text: 'File New Case' }] },
      { url: 'https://e-jagriti.gov.in/case/new', signals: { likely_login_wall: false }, fields: [],
        links: [{ index: 5, text: 'Case Type', kind: 'dropdown', value: 'Select' }] },
      { url: 'https://e-jagriti.gov.in/case/new', signals: { likely_login_wall: false }, fields: [],
        links: [{ index: 7, text: 'Consumer Complaint CC', kind: 'option' }] },
      { url: 'https://e-jagriti.gov.in/case/new', signals: { likely_login_wall: false }, fields: [],
        links: [{ index: 5, text: 'Case Type', kind: 'dropdown', value: 'Consumer Complaint' }] },
      { url: 'https://e-jagriti.gov.in/case/details', signals: { likely_login_wall: false },
        fields: [{ index: 4, tag: 'input', type: 'text' }] },
    ],
    plans: [NO_FORM, NO_FORM, NO_FORM, NO_FORM, NO_FORM, PLAN],
    navChoice: [{ index: 1, text: 'Open menu' }, { index: 2, text: 'File New Case' },
      { index: 5, text: 'Case Type' }, { index: 7, text: 'Consumer Complaint CC', step: 'Consumer Complaint' },
      { index: null, text: 'Next', action: 'next', step: 'Case Type Next' }],
  });
  await world.send(START, NYAYABOT_TAB);
  assert.deepEqual(world.calls.created, ['https://e-jagriti.gov.in/login']);
  await world.send({ type: 'LOGGED_IN' }, PANEL);
  assert.deepEqual(world.calls.tabMessages.filter((m) => m.message.cmd === 'clickLink').map((m) => m.message.index), [1, 2, 5, 7]);
  assert.equal(world.calls.tabMessages.filter((m) => m.message.cmd === 'clickNext').length, 1);
  const navigations = world.calls.fetch.filter((c) => c.url.endsWith('/autofill/navigate'));
  assert.deepEqual(navigations.at(-1).body.visited, ['Open menu', 'File New Case', 'Case Type', 'Consumer Complaint'],
    'the server\'s step name is recorded, not the option\'s longer text');
  assert.deepEqual(navigations[2].body.links[0], { index: 5, text: 'Case Type', kind: 'dropdown', value: 'Select' },
    'dropdowns reach the server with their label and current value');
  const { state } = await world.send({ type: 'GET_STATE' }, PANEL);
  assert.equal(state.stage, 'approval');
  assert.ok(!world.calls.tabMessages.some((m) => ['scanChoices', 'clickChoice', 'fill'].includes(m.message.cmd)));
});

test('a server-chosen Next with no single safe Next button stops without guessing', async () => {
  const world = makeWorld({
    portal: EJAGRITI,
    portalPages: [
      { url: 'https://e-jagriti.gov.in/login', signals: { likely_login_wall: false }, fields: [],
        links: [{ index: 2, text: 'File New Case' }] },
      { url: 'https://e-jagriti.gov.in/case/new', signals: { likely_login_wall: false }, fields: [] },
    ],
    plans: [NO_FORM, NO_FORM],
    navChoice: [{ index: 2, text: 'File New Case' }, { index: null, text: 'Next', action: 'next', step: 'Case Type Next' }],
    nextReply: { ok: false, reason: 'ambiguous' },
  });
  await world.send(START, NYAYABOT_TAB);
  await world.send({ type: 'LOGGED_IN' }, PANEL);
  const { state } = await world.send({ type: 'GET_STATE' }, PANEL);
  assert.equal(state.stage, 'not_found');
  assert.match(state.message, /could not find one safe Next/);
  assert.equal(world.calls.tabMessages.filter((m) => m.message.cmd === 'clickNext').length, 1);
  assert.ok(!world.calls.tabMessages.some((m) => m.message.cmd === 'fill'));
});

test('the extension holds no portal-specific steps or wording', () => {
  const portalWords = /ejagriti|e-jagriti|consumer complaint|case type|cpgrams|pgportal|\brbi\b|regulated entity|samadhan|establishment|lodge public grievance|file new case/i;
  for (const file of ['background.js', 'lib/page.js', 'content/portal.js', 'sidepanel/sidepanel.js']) {
    const source = fs.readFileSync(path.join(__dirname, '..', file), 'utf8');
    assert.ok(!portalWords.test(source), `${file} mentions a specific portal: ${(source.match(portalWords) || [])[0]}`);
  }
});

test('only the fields the user approved are filled, then the result is reported', async () => {
  const world = makeWorld({
    portalPages: [{ url: 'https://cybercrime.gov.in/form', signals: { likely_login_wall: false }, fields: [{ index: 4, tag: 'select', type: 'select' }] }],
    plans: [PLAN, { ...PLAN, actions: [] }],
  });
  await world.send(START, NYAYABOT_TAB);
  await world.send({ type: 'FILL_THIS_PAGE' }, PANEL);
  await world.send({ type: 'APPROVE', fieldIds: ['0:4', '0:6'] }, PANEL);
  const fills = world.calls.tabMessages.filter((m) => m.message.cmd === 'fill');
  assert.deepEqual(plain(fills[0].message.actions.map((a) => a.field_id)), ['0:4', '0:6']);
  assert.ok(!fills.some((f) => f.message.actions.some((a) => a.field_id === '0:20')), 'the unticked AI guess is never filled');
  const report = world.calls.fetch.find((c) => c.url.endsWith('/autofill/report'));
  assert.deepEqual(report.body, { event: 'page_filled', filled: 2, failed: 0 });
  const { state } = await world.send({ type: 'GET_STATE' }, PANEL);
  assert.equal(state.stage, 'review');
  assert.match(state.message, /go to the next page on the portal, then press “Fill the next page of the form”/,
    'multi-page forms: the user moves on, then asks for the next page');
});

test('a page off the portal is never planned or filled', async () => {
  const world = makeWorld({ portalPages: [{ url: 'https://example.com/', signals: { likely_login_wall: false }, fields: [] }] });
  await world.send(START, NYAYABOT_TAB);
  await world.send({ type: 'FILL_THIS_PAGE' }, PANEL);
  const { state } = await world.send({ type: 'GET_STATE' }, PANEL);
  assert.equal(state.stage, 'not_found');
  assert.ok(!world.calls.fetch.some((c) => c.url.endsWith('/autofill/plan')));
});

test('cancel ends the session on the server and clears local state', async () => {
  const world = makeWorld();
  await world.send(START, NYAYABOT_TAB);
  await world.send({ type: 'CANCEL' }, PANEL);
  assert.ok(world.calls.fetch.some((c) => c.method === 'DELETE' && c.url.endsWith('/autofill/session')));
  assert.ok(!JSON.stringify(world.store).includes('secret-code-123'));
});


test('after "I\'m logged in", a CAPTCHA hint alone does not send the user back to login', async () => {
  // Regression: a logged-in portal page was reported as "still asks you to sign in".
  const world = makeWorld({
    portalPages: [{ url: 'https://cybercrime.gov.in/dashboard', signals: { likely_login_wall: true, password: 0, otp: 0, captcha: 1, inputs: 0 }, fields: [] }],
    plans: [PLAN],
  });
  await world.send(START, NYAYABOT_TAB);
  await world.send({ type: 'LOGGED_IN' }, PANEL);
  const { state } = await world.send({ type: 'GET_STATE' }, PANEL);
  assert.equal(state.stage, 'approval');
});

test('"I\'m already on the complaint form" skips the sign-in check entirely', async () => {
  const world = makeWorld({
    portalPages: [{ url: 'https://cybercrime.gov.in/dashboard', signals: { likely_login_wall: true, password: 0, otp: 1, captcha: 0, inputs: 6 }, fields: [] }],
    plans: [PLAN],
  });
  await world.send(START, NYAYABOT_TAB);
  await world.send({ type: 'FILL_THIS_PAGE' }, PANEL);
  const { state } = await world.send({ type: 'GET_STATE' }, PANEL);
  assert.equal(state.stage, 'approval');
});

test('the sign-in check explains itself in the activity list', async () => {
  const world = makeWorld({ portalPages: [{ url: 'https://cybercrime.gov.in/login', signals: { likely_login_wall: true, password: 1, otp: 0, captcha: 1, inputs: 3 }, fields: [] }] });
  await world.send(START, NYAYABOT_TAB);
  await world.send({ type: 'LOGGED_IN' }, PANEL);
  const { state } = await world.send({ type: 'GET_STATE' }, PANEL);
  assert.ok(state.steps.some((s) => s.startsWith('Sign-in check: password box 1')));
});


// ── Multiple-choice questions ──

const QUESTION = { group: 0, question: 'What type of complaint is this?', options: [
  { index: 0, text: 'I lost money, or someone is trying to take money from me', selected: false },
  { index: 1, text: 'Someone is harassing or blackmailing me online', selected: false },
] };
const NOT_FORM = { ...PLAN, actions: [], reason: 'not_a_complaint_form' };
const ON_QUESTION = { url: 'https://cybercrime.gov.in/dashboard/crimereport', signals: { likely_login_wall: false }, fields: [], links: [], groups: [QUESTION] };
const ON_FORM = { url: 'https://cybercrime.gov.in/dashboard/crimereport', signals: { likely_login_wall: false }, fields: [{ index: 4, tag: 'select', type: 'select' }] };

test('a question page shows the suggested answer and clicks nothing until the user approves', async () => {
  const world = makeWorld({ portalPages: [ON_QUESTION], plans: [NOT_FORM], navChoice: { index: null, text: '' },
    chooseReply: { proposals: [{ group: 0, option_index: 0, option_text: QUESTION.options[0].text, source: 'rules', reason: 'Your case is about money lost to a fraud.' }], ai_used: false } });
  await world.send(START, NYAYABOT_TAB);
  await world.send({ type: 'FILL_THIS_PAGE' }, PANEL);
  const { state } = await world.send({ type: 'GET_STATE' }, PANEL);
  assert.equal(state.stage, 'choice');
  assert.equal(state.choice.proposal.option_index, 0);
  assert.match(state.message, /I suggest “I lost money/);
  assert.ok(!world.calls.tabMessages.some((m) => ['clickChoice', 'clickNext', 'clickLink'].includes(m.message.cmd)));
});

test('approving clicks exactly the chosen option, presses Next, and moves on to the form', async () => {
  const world = makeWorld({ portalPages: [ON_QUESTION, ON_FORM], plans: [NOT_FORM, PLAN], navChoice: { index: null, text: '' } });
  await world.send(START, NYAYABOT_TAB);
  await world.send({ type: 'FILL_THIS_PAGE' }, PANEL);
  await world.send({ type: 'APPROVE_CHOICE', index: 1 }, PANEL);
  const clicks = world.calls.tabMessages.filter((m) => m.message.cmd === 'clickChoice');
  assert.deepEqual(clicks.map((c) => c.message.index), [1]);
  assert.equal(world.calls.tabMessages.filter((m) => m.message.cmd === 'clickNext').length, 1);
  const { state } = await world.send({ type: 'GET_STATE' }, PANEL);
  assert.equal(state.stage, 'approval');
  assert.ok(state.steps.some((s) => s.startsWith('Chose “Someone is harassing')));
});

test('an option index that was not offered is refused', async () => {
  const world = makeWorld({ portalPages: [ON_QUESTION], plans: [NOT_FORM], navChoice: { index: null, text: '' } });
  await world.send(START, NYAYABOT_TAB);
  await world.send({ type: 'FILL_THIS_PAGE' }, PANEL);
  await world.send({ type: 'APPROVE_CHOICE', index: 42 }, PANEL);
  assert.ok(!world.calls.tabMessages.some((m) => m.message.cmd === 'clickChoice'));
});

test('with no suggestion the options are still offered for the user to pick', async () => {
  const world = makeWorld({ portalPages: [ON_QUESTION], plans: [NOT_FORM], navChoice: { index: null, text: '' } });
  await world.send(START, NYAYABOT_TAB);
  await world.send({ type: 'FILL_THIS_PAGE' }, PANEL);
  const { state } = await world.send({ type: 'GET_STATE' }, PANEL);
  assert.equal(state.stage, 'choice');
  assert.equal(state.choice.proposal, null);
  assert.equal(state.choice.group.options.length, 2);
});


test('if clicking an answer shows a new question, Next is not pressed so that question is not skipped', async () => {
  const LEVEL_2 = { group: 0, question: 'A Loan / Lending Agent', options: [
    { index: 5, text: 'Pay a processing / legal / registration fee before giving the loan', selected: false },
    { index: 6, text: 'Something else', selected: false },
  ] };
  // After the click, the page shows the answered first question AND a new open one.
  const answeredFirst = { ...QUESTION, options: QUESTION.options.map((o, i) => ({ ...o, selected: i === 0 })) };
  const world = makeWorld({
    portalPages: [ON_QUESTION, { ...ON_QUESTION, groups: [LEVEL_2, answeredFirst] }],
    plans: [NOT_FORM, NOT_FORM], navChoice: { index: null, text: '' }, advanceOnChoice: true,
  });
  await world.send(START, NYAYABOT_TAB);
  await world.send({ type: 'FILL_THIS_PAGE' }, PANEL);
  world.calls.tabMessages.length = 0;
  await world.send({ type: 'APPROVE_CHOICE', index: 0 }, PANEL);
  const nexts = world.calls.tabMessages.filter((m) => m.message.cmd === 'clickNext');
  assert.equal(nexts.length, 0);
  const { state } = await world.send({ type: 'GET_STATE' }, PANEL);
  assert.equal(state.stage, 'choice');
  assert.equal(state.choice.group.question, 'A Loan / Lending Agent', 'the new question is offered next');
});

test('when nothing on a page looks like an answer, the Activity list says what was skipped', async () => {
  const world = makeWorld({
    portalPages: [{ ...ON_QUESTION, groups: [], stats: { clickable: 12, options: 0, groups: 0, dropped: { menu_or_layout: 9, control_button: 3 } } }],
    plans: [NOT_FORM], navChoice: { index: null, text: '' },
  });
  await world.send(START, NYAYABOT_TAB);
  await world.send({ type: 'FILL_THIS_PAGE' }, PANEL);
  const { state } = await world.send({ type: 'GET_STATE' }, PANEL);
  assert.ok(state.steps.some((s) => s === 'Looked for answer options: 12 clickable, 0 kept, 0 question(s); skipped 9 menu or layout, 3 control button.'));
});

test('the skipped-items line gives examples and names the layout rule that matched', async () => {
  const stats = { clickable: 23, options: 0, groups: 0, dropped: { control_button: 4, menu_or_layout: 8 },
    samples: { control_button: ['Logout', 'Back', 'Next'], menu_or_layout: ['WHAT HAPPENED?'] },
    chrome: { 'tag:mat-step-header': 4, 'tag:mat-sidenav': 4 } };
  const world = makeWorld({ portalPages: [{ ...ON_QUESTION, groups: [], stats }], plans: [NOT_FORM], navChoice: { index: null, text: '' } });
  await world.send(START, NYAYABOT_TAB);
  await world.send({ type: 'FILL_THIS_PAGE' }, PANEL);
  const { state } = await world.send({ type: 'GET_STATE' }, PANEL);
  assert.ok(state.steps.includes('Looked for answer options: 23 clickable, 0 kept, 0 question(s); skipped '
    + '4 control button (“Logout”, “Back”, “Next”, …), 8 menu or layout (“WHAT HAPPENED?”, …); '
    + 'menu or layout found by tag:mat-step-header ×4, tag:mat-sidenav ×4.'));
});

const LEVEL_2 = { group: 0, question: 'A Loan / Lending Agent', options: [
  { index: 5, text: 'Pay a processing / legal / registration fee before giving the loan', selected: false },
  { index: 6, text: 'Something else', selected: false },
] };

test('with no plain Next button, the helper says so and stops instead of asking the same question again', async () => {
  const world = makeWorld({ portalPages: [ON_QUESTION], plans: [NOT_FORM], navChoice: { index: null, text: '' },
    nextReply: { ok: false, reason: 'no_next' } });
  await world.send(START, NYAYABOT_TAB);
  await world.send({ type: 'FILL_THIS_PAGE' }, PANEL);
  await world.send({ type: 'APPROVE_CHOICE', index: 0 }, PANEL);
  const { state } = await world.send({ type: 'GET_STATE' }, PANEL);
  assert.equal(state.stage, 'not_found');
  assert.match(state.message, /press the portal's Next/);
  assert.ok(state.steps.includes('I could not find a plain Next button here.'));
});

test('if Next does not move the page on, the helper explains instead of going round in circles', async () => {
  const world = makeWorld({ portalPages: [ON_QUESTION, ON_QUESTION], plans: [NOT_FORM], navChoice: { index: null, text: '' } });
  await world.send(START, NYAYABOT_TAB);
  await world.send({ type: 'FILL_THIS_PAGE' }, PANEL);
  await world.send({ type: 'APPROVE_CHOICE', index: 0 }, PANEL);
  const { state } = await world.send({ type: 'GET_STATE' }, PANEL);
  assert.equal(state.stage, 'not_found');
  assert.match(state.message, /didn't move on/);
  assert.ok(state.steps.includes('The page did not move on after Next.'));
});

test('an answered question that stays on screen is not offered again when a new one is open', async () => {
  // The portal may keep the first question visible (with no visible "chosen" mark) and
  // show the next one below it.
  const world = makeWorld({
    portalPages: [ON_QUESTION, { ...ON_QUESTION, groups: [QUESTION, LEVEL_2] }],
    plans: [NOT_FORM, NOT_FORM], navChoice: { index: null, text: '' },
  });
  await world.send(START, NYAYABOT_TAB);
  await world.send({ type: 'FILL_THIS_PAGE' }, PANEL);
  await world.send({ type: 'APPROVE_CHOICE', index: 0 }, PANEL);
  const { state } = await world.send({ type: 'GET_STATE' }, PANEL);
  assert.equal(state.stage, 'choice');
  assert.equal(state.choice.group.question, 'A Loan / Lending Agent');
});

test('"Fill the next page" on a question page that keeps /login in its address goes on to the question', async () => {
  // The page reports no sign-in box (see page.js isLoginWall): the helper must not
  // send the user back to the login step.
  const onLoginAddress = { ...ON_QUESTION, url: 'https://cybercrime.gov.in/login',
    signals: { likely_login_wall: false, url_hint: true, password: 0, otp: 0, captcha: 0, inputs: 0 } };
  const world = makeWorld({ portalPages: [onLoginAddress], plans: [NOT_FORM], navChoice: { index: null, reason: 'not_entry_page' } });
  await world.send(START, NYAYABOT_TAB);
  await world.send({ type: 'NEXT_PAGE' }, PANEL);
  const { state } = await world.send({ type: 'GET_STATE' }, PANEL);
  assert.equal(state.stage, 'choice');
  assert.ok(!state.steps.some((s) => s.startsWith('Sign-in check')));
});


// ── Guidance: pop-ups, portal lists, and saying where the user is ──

const CASE_DETAILS = { title: 'File New Case', step: 1, steps: 6, step_label: 'Case Details', next: 'Next' };
const ON_CASE_DETAILS = { url: 'https://e-jagriti.gov.in/case/new', signals: { likely_login_wall: false },
  fields: [{ index: 4, tag: 'select', type: 'select' }], context: CASE_DETAILS };

test('an open pop-up is the user\'s turn: the server explains it and nothing is clicked or planned', async () => {
  const dialog = { title: 'Select Filing Type', options: [{ text: 'Normal Filing', selected: true },
    { text: 'Complaints Against Unfair Contract', selected: false }], buttons: ['Cancel', 'Submit'], fields: 0 };
  const guidance = 'The portal opened a box: “Select Filing Type”. Press “Submit” in the box yourself.';
  const world = makeWorld({
    portal: EJAGRITI,
    portalPages: [{ url: 'https://e-jagriti.gov.in/case/new', signals: { likely_login_wall: false }, fields: [], dialogs: [dialog] }],
    plans: [NO_FORM], navChoice: { index: null, text: '', action: 'user', reason: 'dialog', message: guidance },
  });
  await world.send(START, NYAYABOT_TAB);
  await world.send({ type: 'FILL_THIS_PAGE' }, PANEL);
  const { state } = await world.send({ type: 'GET_STATE' }, PANEL);
  assert.equal(state.stage, 'your_turn');
  assert.equal(state.message, guidance);
  const [nav] = world.calls.fetch.filter((c) => c.url.endsWith('/autofill/navigate'));
  assert.deepEqual(nav.body.dialogs, [{ title: dialog.title, options: dialog.options, buttons: dialog.buttons }]);
  assert.ok(!world.calls.fetch.some((c) => c.url.endsWith('/autofill/plan')), 'no plan while a pop-up covers the page');
  assert.ok(!world.calls.tabMessages.some((m) => ['clickLink', 'clickNext', 'clickChoice', 'fill'].includes(m.message.cmd)));
});

test('a pop-up that is itself a form is filled like any page', async () => {
  const world = makeWorld({
    portalPages: [{ ...ON_FORM, dialogs: [{ title: 'Add details', options: [], buttons: ['Save'], fields: 3 }] }],
    plans: [PLAN],
  });
  await world.send(START, NYAYABOT_TAB);
  await world.send({ type: 'FILL_THIS_PAGE' }, PANEL);
  const { state } = await world.send({ type: 'GET_STATE' }, PANEL);
  assert.equal(state.stage, 'approval');
});

test('when the server asks, a portal list\'s options are read once and the page is planned again', async () => {
  const world = makeWorld({
    portal: EJAGRITI,
    portalPages: [{ ...ON_CASE_DETAILS, fields: [{ index: 100003, tag: 'custom', type: 'custom', aria: 'Case Category' }],
      peek: { 100003: ['Banking', 'E-Commerce'] } }],
    plans: [{ ...PLAN, actions: [], reason: 'nothing_to_fill', peek: ['0:100003'] }, PLAN],
  });
  await world.send(START, NYAYABOT_TAB);
  await world.send({ type: 'FILL_THIS_PAGE' }, PANEL);
  assert.equal(world.calls.peeked, 1);
  const plans = world.calls.fetch.filter((c) => c.url.endsWith('/autofill/plan'));
  assert.equal(plans.length, 2);
  assert.deepEqual(plans[1].body.fields[0].options, ['Banking', 'E-Commerce']);
  const { state } = await world.send({ type: 'GET_STATE' }, PANEL);
  assert.equal(state.stage, 'approval');
  assert.ok(state.steps.includes('Read the options of 1 list(s) on this page (nothing chosen).'));
});

test('the plan message says where the user is, what is ticked and what needs their OK', async () => {
  const world = makeWorld({ portal: EJAGRITI, portalPages: [ON_CASE_DETAILS], plans: [PLAN] });
  await world.send(START, NYAYABOT_TAB);
  await world.send({ type: 'FILL_THIS_PAGE' }, PANEL);
  const { state } = await world.send({ type: 'GET_STATE' }, PANEL);
  assert.equal(state.message, 'You\'re on “Case Details” (step 1 of 6). I can fill 3 box(es) from your case. '
    + '2 are ticked and ready. 1 marked “please check” is unticked: tick it if right (“Naam”). Then press “Fill selected fields”.');
});

test('pressing Fill with nothing ticked keeps the list open and says what to tick', async () => {
  const world = makeWorld({ portal: EJAGRITI, portalPages: [ON_CASE_DETAILS], plans: [PLAN] });
  await world.send(START, NYAYABOT_TAB);
  await world.send({ type: 'FILL_THIS_PAGE' }, PANEL);
  await world.send({ type: 'APPROVE', fieldIds: [] }, PANEL);
  const { state } = await world.send({ type: 'GET_STATE' }, PANEL);
  assert.equal(state.stage, 'approval');
  assert.match(state.message, /^You didn't tick anything, so I filled nothing\. Tick the boxes you want in the list below \(for example “Naam”\)/);
  assert.ok(!world.calls.tabMessages.some((m) => m.message.cmd === 'fill'));
});

test('after filling, the message says what was done, what is left, and which portal button comes next', async () => {
  const left = { field_id: '0:9', field_label: 'Case Category', reason: 'Choose this from the portal\'s list yourself.' };
  const world = makeWorld({ portal: EJAGRITI, portalPages: [ON_CASE_DETAILS],
    plans: [PLAN, { ...PLAN, actions: [], manual: [left] }] });
  await world.send(START, NYAYABOT_TAB);
  await world.send({ type: 'FILL_THIS_PAGE' }, PANEL);
  await world.send({ type: 'APPROVE', fieldIds: ['0:4'] }, PANEL);
  const { state } = await world.send({ type: 'GET_STATE' }, PANEL);
  assert.equal(state.stage, 'review');
  assert.equal(state.message, 'Done: I filled 1 box(es) on “Case Details”. Still for you: “UTR”, “Naam”, “Case Category”. '
    + '2 you left unticked are included there. When this page is complete, press “Next” on the portal, '
    + 'then “Fill the next page of the form” here.');
});
