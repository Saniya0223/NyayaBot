// Offline tests for lib/page.js against the backend's local test form, in jsdom.
// No Chrome, no network, no backend: run with `npm test` in this folder.
'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const PAGE_JS = fs.readFileSync(path.join(__dirname, '..', 'lib', 'page.js'), 'utf8');
const TEST_FORM = fs.readFileSync(
  path.join(__dirname, '..', '..', 'backend', 'app', 'static', 'autofill_cybercrime_test_form.html'), 'utf8');
const EJAGRITI_FORM = fs.readFileSync(
  path.join(__dirname, '..', '..', 'backend', 'app', 'static', 'autofill_ejagriti_test_form.html'), 'utf8');

// Values from the jsdom window live in another JS realm; compare them as plain data.
const plain = (value) => JSON.parse(JSON.stringify(value));
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * Make jsdom behave like Chrome where page.js depends on it, then load page.js:
 *  - jsdom has no layout: an element is visible unless it or an ancestor is hidden;
 *  - Chrome passes `cursor` down to child elements and jsdom does not. Without this the
 *    tests missed that the text and icons inside a clickable card look clickable too.
 */
function prepare(w) {
  const own = w.getComputedStyle.bind(w);
  w.Element.prototype.getClientRects = function () {
    for (let n = this; n; n = n.parentElement) {
      if (n.hidden || own(n).display === 'none') return [];
    }
    return [{}];
  };
  w.getComputedStyle = (el, pseudo) => {
    const style = own(el, pseudo);
    let cursor = style.cursor;
    for (let n = el.parentElement; (!cursor || cursor === 'auto') && n; n = n.parentElement) cursor = own(n).cursor;
    return new Proxy(style, {
      get: (target, key) => (key === 'cursor' ? cursor || 'auto'
        : typeof target[key] === 'function' ? target[key].bind(target) : target[key]),
    });
  };
  Object.defineProperty(w.HTMLElement.prototype, 'innerText', { get() { return this.textContent; } });
  w.eval(PAGE_JS);
  return w;
}

async function loadForm(hash = 'home') {
  const dom = new JSDOM(TEST_FORM, {
    runScripts: 'dangerously', pretendToBeVisual: true,
    url: `http://localhost:8000/api/v1/autofill/test-form#${hash}`,
  });
  const w = prepare(dom.window);
  if (hash === 'form') await wait(900);     // the test form draws itself late, like a real app
  return w;
}

function loadEjagritiForm(hash = 'form', pageScript = PAGE_JS) {
  const dom = new JSDOM(EJAGRITI_FORM, {
    runScripts: 'dangerously', pretendToBeVisual: true,
    url: `http://localhost:8000/api/v1/autofill/test-ejagriti#${hash}`,
  });
  const w = prepare(dom.window);
  if (pageScript !== PAGE_JS) w.eval(pageScript);
  return w;
}

/** Any local portal test page (backend/app/static/<file>), Chrome-like. */
function loadPortalPage(file, hash) {
  const html = fs.readFileSync(path.join(__dirname, '..', '..', 'backend', 'app', 'static', file), 'utf8');
  const dom = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true,
    url: `http://localhost:8000/api/v1/autofill/test-page#${hash}` });
  return prepare(dom.window);
}

/** A small page of its own; answer cards set a pointer cursor, as on real portals. */
function pageWith(html) {
  const dom = new JSDOM(`<!doctype html><html><head><style>.choice { cursor: pointer; }</style></head><body>${html}</body></html>`,
    { runScripts: 'outside-only', pretendToBeVisual: true });
  return prepare(dom.window);
}

const received = (w) => JSON.parse(w.document.getElementById('received').textContent);
const byLabel = (fields, text) => fields.find((f) => [f.label, f.aria, f.neighbour].includes(text));

test('scan finds the visible form fields, skips hidden ones and reads no values', async () => {
  const w = await loadForm('form');
  const fields = w.NyayaPage.scanFields(w.document);
  assert.ok(fields.length >= 20);
  assert.ok(byLabel(fields, 'Transaction ID / UTR Number'));
  assert.ok(byLabel(fields, 'Name'), 'table-cell label is read as neighbour text');
  assert.ok(byLabel(fields, 'Relationship with the victim'), 'aria-labelledby is read');
  assert.ok(!fields.some((f) => f.name === 'mobileNo'), 'the hidden login view is not scanned');
  assert.ok(fields.every((f) => !('value' in f)));
  const bank = byLabel(fields, 'Bank / (Wallet / PG / PA) / Merchant');
  assert.ok(bank.options.includes('State Bank of India'));
});

test('e-Jagriti scan keeps the repeated party labels in their own sections', () => {
  const w = loadEjagritiForm();
  const fields = w.NyayaPage.scanFields(w.document);
  const nameFields = fields.filter((f) => f.label === 'Full Name');
  assert.equal(nameFields.length, 2);
  assert.deepEqual(plain(nameFields.map((f) => f.section)), ['Complainant Details', 'Opposite Party Details']);
  assert.equal(fields.find((f) => f.ident === 'paid').section, 'Case Details');
  assert.equal(fields.find((f) => f.ident === 'oppositeMobile').section, 'Opposite Party Details');
  assert.ok(fields.some((f) => f.tag === 'custom' && f.aria === 'Case Category'));
  assert.ok(fields.every((f) => !('value' in f)));
});

test('mutation check: removing section extraction loses the party distinction', () => {
  const oldScanner = PAGE_JS.replace(/section: sectionOf\(el\),/g, 'section: "",');
  assert.notEqual(oldScanner, PAGE_JS);
  const w = loadEjagritiForm('form', oldScanner);
  const names = w.NyayaPage.scanFields(w.document).filter((field) => field.label === 'Full Name');
  assert.deepEqual(plain(names.map((field) => field.section)), ['', '']);
  assert.notDeepEqual(plain(names.map((field) => field.section)),
    ['Complainant Details', 'Opposite Party Details']);
});

test('e-Jagriti approved values reach the right party and PIN autofill is not overwritten', async () => {
  const w = loadEjagritiForm();
  const fields = w.NyayaPage.scanFields(w.document);
  const action = (id, value) => {
    const found = fields.find((field) => field.ident === id);
    return { index: found.index, field_id: `0:${found.index}`, value, kind: 'text' };
  };
  const results = await w.NyayaPage.fillActions(w.document, [
    action('complainantName', 'Asha Verma'),
    action('complainantMobile', '9876543210'),
    action('oppositeName', 'Example Retail'),
    action('oppositeMobile', '9123456780'),
    action('complainantPin', '302001'),
  ], { pause: 0 });
  assert.ok(results.every((result) => result.status === 'filled'));
  await wait(130);
  const received = JSON.parse(w.document.getElementById('received').textContent);
  assert.equal(received['complainant.fullName'], 'Asha Verma');
  assert.equal(received['complainant.mobile'], '9876543210');
  assert.equal(received['oppositeParty.fullName'], 'Example Retail');
  assert.equal(received['oppositeParty.mobile'], '9123456780');
  assert.equal(received['complainant.state'], 'Rajasthan');
  assert.equal(received['complainant.district'], 'Jaipur');
  assert.equal(received['complainant.postOffice'], 'Jaipur GPO');
});

test('e-Jagriti Submit and Pay alarms stay off while NyayaBot is acting', () => {
  const w = loadEjagritiForm();
  w.NyayaPage.installGuard(w.document);
  w.NyayaPage.markBusy(5000);
  w.document.getElementById('submitBtn').click();
  w.document.getElementById('payBtn').click();
  assert.equal(w.document.getElementById('alarm').style.display, '');
});

test('MUI combobox inputs are listed but never filled like plain text inputs', async () => {
  const w = loadEjagritiForm();
  const input = w.document.createElement('input');
  input.setAttribute('role', 'combobox');
  input.setAttribute('aria-label', 'Commission Name');
  w.document.querySelector('#caseForm section').appendChild(input);
  const fields = w.NyayaPage.scanFields(w.document);
  assert.ok(fields.some((field) => field.tag === 'custom' && field.aria === 'Commission Name'));
  assert.ok(!fields.some((field) => field.tag === 'input' && field.aria === 'Commission Name'));
  input.setAttribute('data-nyaya-fill', '99999');
  const [result] = await w.NyayaPage.fillActions(w.document,
    [{ index: 99999, field_id: '0:99999', value: 'District', kind: 'text' }], { pause: 0 });
  assert.deepEqual(plain([result.status, result.reason]), ['skipped', 'unsupported']);
  assert.equal(input.value, '');
});

test('e-Jagriti Complete Profile: boxes carry the "Address Information" section, and the PIN fills the place itself', async () => {
  const w = loadEjagritiForm('profile');
  const fields = plain(w.NyayaPage.scanFields(w.document));
  const house = fields.find((f) => f.label === 'House No./Door No./Building/Flat');
  const pin = fields.find((f) => f.label === 'Pin Code*');
  assert.equal(house.section, 'Address Information');
  assert.equal(pin.section, 'Address Information');
  assert.ok(fields.some((f) => f.tag === 'custom' && f.aria === 'State'), 'State is a portal list, listed for the user');
  const results = await w.NyayaPage.fillActions(w.document, [
    { index: house.index, field_id: '0:h', value: '12 Rose Lane', kind: 'text' },
    { index: pin.index, field_id: '0:p', value: '302001', kind: 'text' },
  ], { pause: 0 });
  assert.deepEqual(plain(results.map((r) => r.status)), ['filled', 'filled']);
  await wait(120);
  assert.equal(w.document.getElementById('profileState').textContent, 'Rajasthan');
  const received = JSON.parse(w.document.getElementById('profileReceived').textContent);
  assert.equal(received['profile.houseNumber'], '12 Rose Lane');
  assert.equal(received.profileDistrict, 'Jaipur');
});

test('e-Jagriti local dashboard, Case Type dropdown and Next lead to the complaint form', async () => {
  const w = loadEjagritiForm('dashboard');
  // Dropdowns are read generically (label + shown value); the server's route decides what to do.
  const dropdown = (name) => plain(w.NyayaPage.scanLinks(w.document)).find((l) => l.kind === 'dropdown' && l.text === name);
  assert.equal(dropdown('Case Type'), undefined);
  const firstLinks = w.NyayaPage.scanLinks(w.document);
  assert.ok(!firstLinks.some((link) => link.text === 'File New Case'));
  const menu = firstLinks.find((link) => link.text === 'Open menu');
  assert.ok(menu, 'the collapsed hamburger exposes its accessible name');
  assert.equal(w.NyayaPage.clickLink(w.document, menu.index).ok, true);
  const filing = w.NyayaPage.scanLinks(w.document).find((link) => link.text === 'File New Case');
  assert.ok(filing);
  assert.equal(w.NyayaPage.clickLink(w.document, filing.index).ok, true);
  await wait(20);
  assert.equal(w.location.hash, '#case-type');
  assert.equal(dropdown('Case Type').value, 'Select');
  assert.deepEqual(plain(w.NyayaPage.clickNext(w.document)), { ok: false, reason: 'no_next' });
  const control = dropdown('Case Type');
  assert.equal(w.NyayaPage.clickLink(w.document, control.index).ok, true);
  const consumer = plain(w.NyayaPage.scanLinks(w.document)).find((link) => link.kind === 'option' && /^Consumer Complaint/.test(link.text));
  assert.ok(consumer, 'an open list\'s options are navigation candidates');
  assert.equal(w.NyayaPage.clickLink(w.document, consumer.index).ok, true);
  assert.equal(dropdown('Case Type').value, 'Consumer Complaint');
  assert.equal(w.NyayaPage.clickNext(w.document).ok, true);
  await wait(20);
  // As on the live portal, Next opens "Select Filing Type". It is read for the user's
  // guidance (no form boxes inside); the helper presses nothing in it.
  assert.deepEqual(plain(w.NyayaPage.scanDialogs(w.document)), [{
    title: 'Select Filing Type',
    options: [{ text: 'Normal Filing', selected: true }, { text: 'Complaints Against Unfair Contract', selected: false }],
    buttons: ['Cancel', 'Submit'], fields: 0,
  }]);
  assert.equal(w.location.hash, '#case-type', 'still waiting for the user');
  w.document.getElementById('filingSubmit').click();     // the user's own press
  await wait(20);
  assert.equal(w.location.hash, '#form');
  assert.equal(w.document.getElementById('formView').hidden, false);
  assert.deepEqual(plain(w.NyayaPage.scanDialogs(w.document)), []);
});

test('Case Details: where the user is, today\'s pre-filled date, and the step\'s one Next', async () => {
  const w = loadEjagritiForm('form');
  assert.deepEqual(plain(w.NyayaPage.pageContext(w.document)), {
    title: 'File New Case', step: 1, steps: 6, step_label: 'Case Details', next: 'Next' });
  const date = plain(w.NyayaPage.scanFields(w.document)).find((f) => f.label === 'Date of Cause of Action');
  assert.equal(date.today_date, true, 'the portal pre-filled today\'s date (only this flag is sent, never the value)');
  const [kept] = await w.NyayaPage.fillActions(w.document,
    [{ index: date.index, field_id: '0:d', value: '2026-09-20', kind: 'date' }], { pause: 0 });
  assert.deepEqual(plain([kept.status, kept.reason]), ['skipped', 'already_filled'], 'not replaced without approval');
  const [replaced] = await w.NyayaPage.fillActions(w.document,
    [{ index: date.index, field_id: '0:d', value: '2026-09-20', kind: 'date', replace_today: true }], { pause: 0 });
  assert.equal(replaced.status, 'filled');
  assert.equal(w.document.getElementById('causeDate').value, '2026-09-20');
  const [again] = await w.NyayaPage.fillActions(w.document,
    [{ index: date.index, field_id: '0:d', value: '2026-01-01', kind: 'date', replace_today: true }], { pause: 0 });
  assert.equal(again.reason, 'already_filled', 'only today\'s pre-filled date may ever be replaced');
});

test('portal dropdowns: options are read without choosing, and an approved option is picked exactly', async () => {
  const w = loadEjagritiForm('form');
  const fields = plain(w.NyayaPage.scanFields(w.document));
  const category = fields.find((f) => f.tag === 'custom' && f.aria === 'Case Category');
  const state = fields.find((f) => f.tag === 'custom' && f.aria === 'State of Cause of Action');
  assert.equal(category.filled, false, '"Select" is not a choice');
  const peeked = plain(await w.NyayaPage.peekOptions(w.document, [category.index], { wait: 0 }));
  assert.deepEqual(peeked[String(category.index)], ['Banking', 'E-Commerce', 'Insurance', 'Telecom']);
  assert.equal(w.document.querySelectorAll('.test-list').length, 0, 'the list is closed again');
  assert.equal(w.document.getElementById('caseCategory').textContent, 'Select', 'nothing was chosen');
  const results = plain(await w.NyayaPage.fillActions(w.document, [
    { index: state.index, field_id: '0:s', value: 'rajasthan', kind: 'pick' },
    { index: category.index, field_id: '0:c', value: 'E-Commerce', kind: 'pick' },
  ], { pause: 0 }));
  assert.deepEqual(results.map((r) => r.status), ['filled', 'filled']);
  const received = JSON.parse(w.document.getElementById('received').textContent);
  assert.equal(received.causeState, 'Rajasthan');
  assert.equal(received.caseCategory, 'E-Commerce');
  const district = fields.find((f) => f.tag === 'custom' && f.aria === 'District of Cause of Action');
  const [missing] = plain(await w.NyayaPage.fillActions(w.document,
    [{ index: district.index, field_id: '0:x', value: 'Pune', kind: 'pick' }], { pause: 0 }));
  assert.deepEqual([missing.status, missing.reason], ['failed', 'option_missing']);
  assert.equal(w.document.querySelectorAll('.test-list').length, 0, 'no list is left open');
  const [kept] = plain(await w.NyayaPage.fillActions(w.document,
    [{ index: state.index, field_id: '0:s2', value: 'Delhi', kind: 'pick' }], { pause: 0 }));
  assert.equal(kept.reason, 'already_filled', 'an existing choice is never changed');
});

test('login page is recognised as a sign-in wall and the form is not', async () => {
  const login = await loadForm('login');
  assert.equal(login.NyayaPage.loginSignals(login.document, login.location.href).likely_login_wall, true);
  const form = await loadForm('form');
  assert.equal(form.NyayaPage.loginSignals(form.document, form.location.href).likely_login_wall, false);
});

test('filled values reach the portal\'s own form model, as an Angular or React app would see them', async () => {
  const w = await loadForm('form');
  const fields = w.NyayaPage.scanFields(w.document);
  const act = (label, value, kind = 'text') => ({ index: byLabel(fields, label).index, field_id: `0:${byLabel(fields, label).index}`, value, kind });
  const results = await w.NyayaPage.fillActions(w.document, [
    act('Bank / (Wallet / PG / PA) / Merchant', 'State Bank of India', 'select'),
    act('Transaction ID / UTR Number', 'UTR123456789'),
    act('Amount', '25000'),
    act('Transaction Date', '2026-03-02', 'date'),
    act('Provide any additional information', 'Caller posing as bank staff took Rs 25,000.'),
    act('Name', 'Asha Verma'),
  ], { pause: 0 });
  assert.deepEqual(plain(results.map((r) => r.status)), ['filled', 'filled', 'filled', 'filled', 'filled', 'filled']);
  const model = received(w);
  assert.equal(model.bank, 'State Bank of India');
  assert.equal(model.utr, 'UTR123456789');
  assert.equal(model.amount, '25000');
  assert.equal(model.txnDate, '2026-03-02');
  assert.equal(model.name, 'Asha Verma');
});

test('a dependent dropdown fills after its parent once the page loads the options', async () => {
  const w = await loadForm('form');
  let fields = w.NyayaPage.scanFields(w.document);
  const state = byLabel(fields, 'State');
  await w.NyayaPage.fillActions(w.document, [{ index: state.index, field_id: 's', value: 'Rajasthan', kind: 'select' }], { pause: 0 });
  await wait(800);
  fields = w.NyayaPage.scanFields(w.document);
  const district = byLabel(fields, 'District');
  assert.equal(district.disabled, false);
  const [result] = await w.NyayaPage.fillActions(w.document, [{ index: district.index, field_id: 'd', value: 'Jaipur', kind: 'select' }], { pause: 0 });
  assert.equal(result.status, 'filled');
  assert.equal(received(w).district, 'Jaipur');
});

test('sensitive, unsupported, changed and already-filled fields are never written', async () => {
  const w = await loadForm('form');
  const fields = w.NyayaPage.scanFields(w.document);
  const firstSix = byLabel(fields, 'First Six Digit');
  const upload = byLabel(fields, 'Please Upload any National ID');
  const utr = byLabel(fields, 'Transaction ID / UTR Number');
  w.document.querySelector(`[data-nyaya-fill="${utr.index}"]`).value = 'TYPED-BY-USER';
  const results = await w.NyayaPage.fillActions(w.document, [
    { index: firstSix.index, field_id: 'a', value: '411111', kind: 'text' },
    { index: upload.index, field_id: 'b', value: 'x', kind: 'text' },
    { index: utr.index, field_id: 'c', value: 'UTR123', kind: 'text' },
    { index: 99999, field_id: 'd', value: 'x', kind: 'text' },
    { index: utr.index, field_id: 'e', value: 'State Bank of India', kind: 'select' },
  ], { pause: 0 });
  assert.deepEqual(plain(results.map((r) => [r.status, r.reason])), [
    ['skipped', 'sensitive'], ['skipped', 'unsupported'], ['skipped', 'already_filled'],
    ['failed', 'field_missing'], ['failed', 'field_changed'],
  ]);
  assert.equal(w.document.querySelector(`[data-nyaya-fill="${firstSix.index}"]`).value, '');
  assert.equal(w.document.querySelector(`[data-nyaya-fill="${utr.index}"]`).value, 'TYPED-BY-USER');
});

test('a login field is refused even if a plan asked for it', async () => {
  const w = await loadForm('login');
  const fields = w.NyayaPage.scanFields(w.document);
  const otp = fields.find((f) => f.name === 'otp');
  const [result] = await w.NyayaPage.fillActions(w.document, [{ index: otp.index, field_id: 'o', value: '123456', kind: 'text' }], { pause: 0 });
  assert.deepEqual([result.status, result.reason], ['skipped', 'sensitive']);
});

test('the guard stops a scripted submit while NyayaBot is acting, but never the user\'s own', async () => {
  const w = await loadForm('form');
  w.NyayaPage.installGuard(w.document);
  w.NyayaPage.markBusy(5000);
  const form = w.document.getElementById('complaintForm');
  form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  w.document.getElementById('submitBtn').click();
  assert.equal(w.document.getElementById('alarm').style.display, '', 'the portal never saw a submit');
});

test('links are read with their card context, and the cyber card button leads to login', async () => {
  const w = await loadForm('home');
  const links = w.NyayaPage.scanLinks(w.document);
  const registers = links.filter((l) => l.text === 'Register a Complaint');
  assert.equal(registers.length, 2);
  assert.match(registers[0].context, /Women/);
  assert.match(registers[1].context, /Cyber Crime Related Complaint/);
  const result = w.NyayaPage.clickLink(w.document, registers[1].index);
  assert.equal(result.ok, true);
  assert.equal(w.document.getElementById('view-login').hidden, false);
});

test('clickLink refuses a submit or payment control', async () => {
  const w = await loadForm('form');
  const links = w.NyayaPage.scanLinks(w.document);
  const submit = links.find((l) => l.text === 'Submit');
  assert.equal(submit.submits_form, true);
  assert.deepEqual(plain(w.NyayaPage.clickLink(w.document, submit.index)), { ok: false, reason: 'refused_final_action' });
  assert.equal(w.document.getElementById('alarm').style.display, '');
});

test('a custom (non-native) dropdown is listed but never filled', async () => {
  const w = await loadForm('form');
  const div = w.document.createElement('div');
  div.setAttribute('role', 'combobox');
  div.setAttribute('aria-label', 'Bank / (Wallet / PG / PA) / Merchant');
  w.document.getElementById('complaintForm').appendChild(div);
  const fields = w.NyayaPage.scanFields(w.document);
  const custom = fields.find((f) => f.tag === 'custom');
  assert.ok(custom && custom.aria.startsWith('Bank'));
  assert.ok(custom.index >= 100000);
  const [result] = await w.NyayaPage.fillActions(w.document, [{ index: custom.index, field_id: 'x', value: 'SBI', kind: 'select' }], { pause: 0 });
  assert.equal(result.status, 'failed');
});

test('Google\'s invisible reCAPTCHA badge on a logged-in page is not a sign-in wall', async () => {
  const w = await loadForm('home');
  const badge = w.document.createElement('div');
  badge.className = 'grecaptcha-badge';
  badge.innerHTML = '<iframe title="reCAPTCHA" src="https://www.google.com/recaptcha/api2/anchor?k=x&size=invisible"></iframe>';
  w.document.body.appendChild(badge);
  const signals = w.NyayaPage.loginSignals(w.document, 'https://cybercrime.gov.in/dashboard/crimereport');
  assert.equal(signals.captcha, 0);
  assert.equal(signals.likely_login_wall, false);
});

test('a CAPTCHA box the user must type into still counts', async () => {
  const w = await loadForm('login');
  const signals = w.NyayaPage.loginSignals(w.document, w.location.href);
  assert.ok(signals.captcha >= 1);
  assert.equal(signals.likely_login_wall, true);
});

// ── Multiple-choice questions ──

test('the questionnaire question and its option cards are found, without menus, stepper or Back/Next', async () => {
  const w = await loadForm('wizard');
  const groups = plain(w.NyayaPage.scanChoices(w.document));
  assert.ok(groups.length >= 1);
  const main = groups[0];
  assert.equal(main.question, 'What type of complaint is this?');
  assert.deepEqual(main.options.map((o) => o.text), [
    'I lost money, or someone is trying to take money from me',
    'Someone is harassing or blackmailing me online, or sharing obscene / explicit material',
    'My phone, computer, email, social media or device was hacked, locked or taken over',
    'I received fake / threatening calls, or someone is spreading false, hateful or dangerous information online',
    'Something else happened that I do not see above',
  ]);
  const everyText = groups.flatMap((g) => g.options.map((o) => o.text)).join(' | ');
  for (const notAnOption of ['Next', 'Back', 'Report Cyber Crime', 'Home', 'WHAT HAPPENED?']) {
    assert.ok(!everyText.includes(notAnOption), `${notAnOption} must not be an option`);
  }
});

test('choosing an option then pressing Next moves to the next question, and the portal records the choice', async () => {
  const w = await loadForm('wizard');
  const [group] = w.NyayaPage.scanChoices(w.document);
  const money = group.options.find((o) => o.text.startsWith('I lost money'));
  assert.equal(w.NyayaPage.clickChoice(w.document, money.index, money.text).ok, true);
  assert.equal(w.NyayaPage.clickNext(w.document).ok, true);
  assert.equal(w.document.getElementById('q2').hidden, false);
  assert.equal(w.document.getElementById('wizChosen').textContent, 'I lost money, or someone is trying to take money from me');
  const [loan] = w.NyayaPage.scanChoices(w.document);
  assert.equal(loan.question, 'A Loan / Lending Agent');
  assert.ok(loan.options.some((o) => o.text.startsWith('Pay a processing')), 'a descriptive "Pay ..." answer is an option');
});

test('an option is found again by its text if the page re-rendered it', async () => {
  const w = await loadForm('wizard');
  const [group] = w.NyayaPage.scanChoices(w.document);
  const target = group.options[2];
  w.document.querySelectorAll('[data-nyaya-choice]').forEach((n) => n.removeAttribute('data-nyaya-choice'));
  const result = plain(w.NyayaPage.clickChoice(w.document, 999, target.text));
  assert.equal(result.ok, true);
  assert.ok(w.document.querySelector('.choice.active span').textContent.startsWith('My phone'));
});

test('clickChoice refuses action labels and clickNext refuses anything but a plain Next', async () => {
  const dom = new JSDOM(`<main><h3>Confirm</h3><div class="grid">
    <button class="opt">I Agree</button><button class="opt">Pay now</button></div>
    <form><button>Save &amp; Next</button><button type="submit">Submit</button></form></main>`, { runScripts: 'outside-only' });
  const w = dom.window;
  w.Element.prototype.getClientRects = () => [{}];
  w.eval(PAGE_JS);
  assert.equal(w.NyayaPage.scanChoices(w.document).length, 0, 'action labels are never offered as answers');
  const agree = w.document.querySelector('.opt');
  agree.setAttribute('data-nyaya-choice', '0');
  assert.deepEqual(plain(w.NyayaPage.clickChoice(w.document, 0, 'I Agree')), { ok: false, reason: 'refused_action' });
  assert.deepEqual(plain(w.NyayaPage.clickNext(w.document)), { ok: false, reason: 'no_next' });
});

test('a form-submit Next button is allowed once by the guard, but Submit stays blocked', async () => {
  const dom = new JSDOM(`<form id="f"><div class="opt">A</div><button id="next">Next</button></form>
    <form id="g"><button id="submit" type="submit">Submit</button></form>`, { runScripts: 'outside-only' });
  const w = dom.window;
  w.Element.prototype.getClientRects = () => [{}];
  w.eval(PAGE_JS);
  const submitted = [];
  w.document.getElementById('f').addEventListener('submit', (e) => { e.preventDefault(); submitted.push('f'); });
  w.document.getElementById('g').addEventListener('submit', (e) => { e.preventDefault(); submitted.push('g'); });
  w.NyayaPage.installGuard(w.document);
  assert.equal(w.NyayaPage.clickNext(w.document).ok, true);
  w.NyayaPage.markBusy(3000);
  w.document.getElementById('submit').click();
  assert.deepEqual(submitted, ['f']);
});

test('regression: answer cards inside an Angular Material stepper and sidenav layout are found', async () => {
  // The real portal puts its questions inside <mat-stepper> / "mat-horizontal-stepper-content"
  // within "mat-sidenav-content". The first version treated those as page chrome and
  // dropped every card, so the helper said it found nothing to fill.
  const w = await loadForm('wizard');
  const { groups, stats } = plain(w.NyayaPage.scanChoiceGroups(w.document));
  assert.equal(groups[0].options.length, 5);
  // Skipped as menu/layout: the sidebar (4), the step bar (4) and the page's top menu, never a card.
  assert.ok(stats.dropped.menu_or_layout >= 8);
  assert.equal(stats.chrome['tag:mat-sidenav'], 4);
  assert.equal(stats.chrome['tag:mat-step-header'], 4);
  const texts = groups.flatMap((g) => g.options.map((o) => o.text));
  for (const notAnOption of ['Report Cyber Crime', 'Draft Complaint', 'WHAT HAPPENED?', 'PREVIEW & SUBMIT']) {
    assert.ok(!texts.includes(notAnOption), `${notAnOption} must not be an option`);
  }
  assert.ok(texts.every((t) => !t.startsWith('help')), 'Material icon ligature text is not part of an answer');
  assert.equal(groups[0].question, 'What type of complaint is this?');
});

test('answer pills without a pointer cursor are found by their class, chosen, and Next reaches the form', async () => {
  const w = await loadForm('wizard');
  let [group] = w.NyayaPage.scanChoices(w.document);
  const money = group.options.find((o) => o.text.startsWith('I lost money'));
  w.NyayaPage.clickChoice(w.document, money.index, money.text);
  assert.equal(w.NyayaPage.clickNext(w.document).ok, true);
  [group] = w.NyayaPage.scanChoices(w.document);
  assert.equal(group.question, 'A Loan / Lending Agent');
  assert.equal(group.options.length, 4);
  const fee = group.options.find((o) => o.text.startsWith('Pay a processing'));
  const clicked = plain(w.NyayaPage.clickChoice(w.document, fee.index, fee.text));
  assert.equal(clicked.ok, true);
  assert.equal(clicked.selected, true, 'the page shows the pill as chosen');
  assert.equal(w.NyayaPage.clickNext(w.document).ok, true);
  await wait(900);
  assert.equal(w.document.getElementById('view-form').hidden, false);
  assert.equal(w.document.getElementById('wizChosen').textContent,
    'I lost money, or someone is trying to take money from me / Pay a processing / legal / registration fee before giving the loan');
});

test('an answered question is listed after unanswered ones', async () => {
  const w = await loadForm('wizard');
  const [group] = w.NyayaPage.scanChoices(w.document);
  w.NyayaPage.clickChoice(w.document, group.options[0].index, group.options[0].text);
  const again = plain(w.NyayaPage.scanChoices(w.document));
  assert.equal(again[0].options.find((o) => o.selected).text, group.options[0].text);
});

// ── Regressions from the live Cyber Crime portal ──

test('regression: role="tablist" on the whole <mat-stepper>, as Angular Material sets it, does not hide the answers', async () => {
  // On cybercrime.gov.in the stepper element itself carries role="tablist" and holds the
  // questions too. Treating everything inside a tab list as a menu dropped every card
  // ("38 clickable, 0 kept … 10 menu or layout") and hid the Next button as well.
  const w = await loadForm('wizard');
  assert.equal(w.document.querySelector('mat-stepper').getAttribute('role'), 'tablist', 'the test page keeps the real markup');
  const { groups, stats } = plain(w.NyayaPage.scanChoiceGroups(w.document));
  assert.equal(groups[0].question, 'What type of complaint is this?');
  assert.equal(groups[0].options.length, 5);
  assert.ok(!Object.keys(stats.chrome).some((why) => why.includes('tablist')));
  assert.deepEqual(plain(w.NyayaPage.clickNext(w.document)), { ok: true, text: 'Next' });
});

test('the text and "help" icon inside a clickable card are not answers of their own', async () => {
  // In Chrome every child of a pointer-cursor card has a pointer cursor too (the test
  // harness does the same). Each card then split into a "help" answer and a text answer.
  const w = await loadForm('wizard');
  const card = w.document.querySelector('#q1 .choice');
  assert.equal(w.getComputedStyle(card.querySelector('span')).cursor, 'pointer', 'the harness passes the cursor down, like Chrome');
  const groups = plain(w.NyayaPage.scanChoices(w.document));
  assert.equal(groups.length, 1, 'one question, not one per card');
  assert.ok(!groups[0].options.some((o) => o.text === 'help'));
});

test('a page whose address still says /login after sign-in is not a sign-in wall without a sign-in box', async () => {
  // cybercrime.gov.in keeps "/login" in the address bar after the user signs in, and its
  // question pages have no text boxes at all.
  const w = await loadForm('wizard');
  const signals = w.NyayaPage.loginSignals(w.document, 'https://cybercrime.gov.in/login');
  assert.equal(signals.url_hint, true);
  assert.equal(signals.inputs, 0);
  assert.equal(signals.likely_login_wall, false);
});

test('the stepper\'s other steps, in the page but hidden and inert, are ignored', async () => {
  const w = await loadForm('wizard');
  assert.ok(!w.NyayaPage.scanFields(w.document).some((f) => f.name === 'nextStepAmount'));
  const texts = plain(w.NyayaPage.scanChoices(w.document)).flatMap((g) => g.options.map((o) => o.text));
  assert.ok(!texts.some((t) => t.includes('later step')));
  assert.equal(w.NyayaPage.clickNext(w.document).ok, true, 'only the current step\'s Next counts, so it is not ambiguous');
});

test('"← Back" is a control, not an answer', async () => {
  const w = await loadForm('wizard');
  const { groups, stats } = plain(w.NyayaPage.scanChoiceGroups(w.document));
  assert.ok(!groups.some((g) => g.options.some((o) => o.text.includes('Back'))));
  assert.ok(stats.samples.control_button.includes('← Back'));
});

test('answer cards set out in several rows are one question, in page order', () => {
  const w = pageWith(`<main><h3>How were you cheated?</h3><div class="opts">
    <div class="row"><div class="col"><div class="choice">Fake loan app took a fee</div></div><div class="col"><div class="choice">Online shopping order never came</div></div></div>
    <div class="row"><div class="col"><div class="choice">Investment or trading scheme</div></div><div class="col"><div class="choice">Someone posing as bank staff</div></div></div>
    <div class="row"><div class="col"><div class="choice">Something else happened</div></div></div>
  </div><button>Next</button></main>`);
  const groups = plain(w.NyayaPage.scanChoices(w.document));
  assert.equal(groups.length, 1);
  assert.equal(groups[0].question, 'How were you cheated?');
  assert.deepEqual(groups[0].options.map((o) => o.text), ['Fake loan app took a fee', 'Online shopping order never came',
    'Investment or trading scheme', 'Someone posing as bank staff', 'Something else happened']);
});

test('two questions side by side, each with its own heading, stay separate', () => {
  const w = pageWith(`<main>
    <div class="q"><p>Did you lose money?</p><button class="opt">Yes, I lost money</button><button class="opt">No money was lost</button></div>
    <div class="q"><p>Did you share an OTP?</p><button class="opt">Yes, I shared it</button><button class="opt">No, I never shared it</button></div></main>`);
  const groups = plain(w.NyayaPage.scanChoices(w.document));
  assert.equal(groups.length, 2);
  assert.deepEqual(groups.map((g) => g.question).sort(), ['Did you lose money?', 'Did you share an OTP?']);
});

test('a heading inside the same box as its answers is read as the question', () => {
  const w = pageWith(`<main><div class="q"><h4>Did the caller ask you to install an app?</h4>
    <button type="button">Yes, they asked me to install an app</button><button type="button">No, they did not</button></div></main>`);
  const [group] = plain(w.NyayaPage.scanChoices(w.document));
  assert.equal(group.question, 'Did the caller ask you to install an app?');
});

test('radio buttons with separate labels are offered, and choosing one ticks its radio', () => {
  const w = pageWith(`<main><p>Have you lost money in INR?</p><div class="radios">
    <input type="radio" name="lost" id="lostYes"><label for="lostYes">Yes</label>
    <input type="radio" name="lost" id="lostNo"><label for="lostNo">No</label></div><button>Next</button></main>`);
  const [group] = plain(w.NyayaPage.scanChoices(w.document));
  assert.equal(group.question, 'Have you lost money in INR?');
  assert.deepEqual(group.options.map((o) => o.text), ['Yes', 'No']);
  const result = plain(w.NyayaPage.clickChoice(w.document, group.options[0].index, 'Yes'));
  assert.equal(result.ok, true);
  assert.equal(w.document.getElementById('lostYes').checked, true);
  assert.equal(result.selected, true);
});

test('a card marked by a class such as "selectedCard" counts as chosen, "inactive" does not', () => {
  const w = pageWith(`<main><h3>Pick one</h3><div class="grid">
    <div class="choice selectedCard">The first answer here</div><div class="choice">The second answer here</div>
    <div class="choice inactive">The third answer here</div></div></main>`);
  const [group] = plain(w.NyayaPage.scanChoices(w.document));
  assert.deepEqual(group.options.map((o) => o.selected), [true, false, false]);
});

test('a card with a title and a description is one answer, not two', () => {
  // Chrome gives the title and description a pointer cursor inherited from the card.
  const w = pageWith(`<main><h3>What did the caller say?</h3><div class="grid">
    <div class="choice"><b>Bank staff</b> <span>They said they were from my bank</span></div>
    <div class="choice"><b>Police</b> <span>They said I was under digital arrest</span></div></div></main>`);
  const groups = plain(w.NyayaPage.scanChoices(w.document));
  assert.equal(groups.length, 1);
  assert.deepEqual(groups[0].options.map((o) => o.text),
    ['Bank staff They said they were from my bank', 'Police They said I was under digital arrest']);
});

test('answers in an inert part of the page (behind a dialog) are not offered', () => {
  const w = pageWith(`<main><div inert><h3>Behind the dialog</h3><div class="grid">
      <div class="choice">An answer behind the dialog</div><div class="choice">Another answer behind it</div></div></div>
    <div role="dialog"><h3>Which one applies?</h3><div class="grid">
      <div class="choice">The first answer in the dialog</div><div class="choice">The second answer in the dialog</div></div></div></main>`);
  const groups = plain(w.NyayaPage.scanChoices(w.document));
  assert.equal(groups.length, 1);
  assert.equal(groups[0].question, 'Which one applies?');
});

test('a click goes to the answer on screen now, not to one numbered by an earlier scan', () => {
  const w = pageWith(`<main><div id="q1"><h3>First question here</h3><div class="grid">
      <div class="choice active">First question answer A</div><div class="choice">First question answer B</div></div></div>
    <div id="q2" hidden><h3>Second question here</h3><div class="grid">
      <div class="choice">Second question answer A</div><div class="choice">Second question answer B</div></div></div></main>`);
  w.NyayaPage.scanChoices(w.document);                  // numbers the first question's answers 0 and 1
  w.document.querySelectorAll('#q1 .choice').forEach((c) => c.setAttribute('aria-disabled', 'true'));   // locked once answered
  w.document.getElementById('q2').hidden = false;
  const [group] = plain(w.NyayaPage.scanChoices(w.document));
  assert.equal(group.question, 'Second question here');
  const clicked = [];
  w.document.addEventListener('click', (e) => clicked.push(e.target.textContent));
  assert.equal(w.NyayaPage.clickChoice(w.document, group.options[0].index, group.options[0].text).ok, true);
  assert.deepEqual(clicked, ['Second question answer A']);
});

test('with two Next buttons showing, the one in the chosen answer\'s step is pressed', () => {
  const w = pageWith(`<main>
    <div role="tabpanel"><section><h3>What happened?</h3><div class="grid"><div class="choice">The first answer here</div>
      <div class="choice">The second answer here</div></div></section><button id="stepNext">Next</button></div>
    <section><p>Latest advisories</p><button id="carouselNext">Next</button></section></main>`);
  const [group] = plain(w.NyayaPage.scanChoices(w.document));
  assert.deepEqual(plain(w.NyayaPage.clickNext(w.document)), { ok: false, reason: 'ambiguous' });
  let pressed = '';
  w.document.addEventListener('click', (e) => { if (e.target.id) pressed = e.target.id; });
  assert.equal(w.NyayaPage.clickNext(w.document, group.options[0].index).ok, true);
  assert.equal(pressed, 'stepNext');
});


// ── CPGRAMS, RBI CMS and Samadhan layouts (same page logic as every portal) ──

test('CPGRAMS sign-up: the state list fills, its districts arrive, and the security code is never touched', async () => {
  const w = loadPortalPage('autofill_cpgrams_test_form.html', 'register');
  let fields = plain(w.NyayaPage.scanFields(w.document));
  const by = (label) => fields.find((f) => f.label === label);
  assert.equal(by('District').disabled, true, 'districts load only after a state is chosen');
  const results = plain(await w.NyayaPage.fillActions(w.document, [
    { index: by('Name').index, field_id: '0:n', value: 'Asha Verma', kind: 'text' },
    { index: by('State').index, field_id: '0:s', value: 'Rajasthan', kind: 'select' },
    { index: by('Security Code').index, field_id: '0:c', value: 'X1Y2', kind: 'text' },
  ], { pause: 0 }));
  assert.deepEqual(results.map((r) => [r.status, r.reason]), [['filled', ''], ['filled', ''], ['skipped', 'sensitive']]);
  await wait(120);
  fields = plain(w.NyayaPage.scanFields(w.document));
  const [district] = plain(await w.NyayaPage.fillActions(w.document,
    [{ index: by('District').index, field_id: '0:d', value: 'Jaipur', kind: 'select' }], { pause: 0 }));
  assert.equal(district.status, 'filled');
  const received = JSON.parse(w.document.getElementById('received').textContent);
  assert.deepEqual([received.Name, received.State, received.District, received.Captcha], ['Asha Verma', 'Rajasthan', 'Jaipur', undefined]);
});

test('RBI CMS: "File A Complaint" is a link to follow, the OTP step is a sign-in wall, and the bank is picked from its list', async () => {
  const w = loadPortalPage('autofill_rbi_test_form.html', 'home');
  const file = plain(w.NyayaPage.scanLinks(w.document)).find((l) => l.text === 'File A Complaint');
  assert.ok(file);
  assert.equal(w.NyayaPage.clickLink(w.document, file.index).ok, true);
  assert.equal(w.NyayaPage.loginSignals(w.document, w.location.href).likely_login_wall, true, 'the OTP is the user\'s job');
  w.document.getElementById('verifyBtn').click();       // the user verifies
  const fields = plain(w.NyayaPage.scanFields(w.document));
  assert.equal(fields.find((f) => f.label === 'Name').section, 'Complainant Details');
  const bank = fields.find((f) => f.tag === 'custom' && f.aria === 'Name of the Regulated Entity');
  assert.equal(bank.section, 'Details of the Complaint');
  const [picked] = plain(await w.NyayaPage.fillActions(w.document,
    [{ index: bank.index, field_id: '0:b', value: 'State Bank of India', kind: 'pick' }], { pause: 0 }));
  assert.equal(picked.status, 'filled');
  assert.equal(JSON.parse(w.document.getElementById('received').textContent).entityName, 'State Bank of India');
  w.NyayaPage.installGuard(w.document);
  w.NyayaPage.markBusy(3000);
  w.document.getElementById('submitBtn').click();
  assert.equal(w.document.getElementById('alarm').style.display, '', 'nothing NyayaBot does can submit');
});

test('Samadhan: worker and employer boxes carry their own section, and the application type is read then picked', async () => {
  const w = loadPortalPage('autofill_samadhan_test_form.html', 'dashboard');
  const start = plain(w.NyayaPage.scanLinks(w.document)).find((l) => l.text === 'File New Application');
  assert.ok(start, 'the filing entry is a link the server can choose');
  w.NyayaPage.clickLink(w.document, start.index);
  await wait(20);
  const fields = plain(w.NyayaPage.scanFields(w.document));
  const sections = (label) => fields.filter((f) => f.label === label).map((f) => f.section);
  assert.deepEqual(sections('Mobile'), ['Worker Details', 'Employer / Establishment Details']);
  assert.deepEqual(sections('Name of Establishment'), ['Employer / Establishment Details']);
  const category = fields.find((f) => f.tag === 'custom' && f.aria === 'Select Category');
  assert.equal(category.section, 'Application Details');
  const peeked = plain(await w.NyayaPage.peekOptions(w.document, [category.index], { wait: 0 }));
  assert.equal(peeked[String(category.index)][0], 'Claim adjudication under code on wages,2019');
  const results = plain(await w.NyayaPage.fillActions(w.document, [
    { index: category.index, field_id: '0:c', value: 'Claim adjudication under code on wages,2019', kind: 'pick' },
    { index: fields.find((f) => f.label === 'Salary').index, field_id: '0:s', value: '18000', kind: 'text' },
  ], { pause: 0 }));
  assert.deepEqual(results.map((r) => r.status), ['filled', 'filled']);
  const received = JSON.parse(w.document.getElementById('received').textContent);
  assert.equal(received.category, 'Claim adjudication under code on wages,2019');
  assert.equal(received.workerSalary, '18000');
});
