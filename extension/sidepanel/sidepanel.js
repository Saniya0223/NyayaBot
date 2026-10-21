/*
 * Side panel: shows the mascot, the current step and what will be / was filled,
 * and sends the user's choices to the background controller.
 * Field labels come from the portal page, so they are always set as text, never HTML.
 */
(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const STAGE_LABEL = {
    idle: 'Waiting to start', opening: 'Opening portal', login: 'Login required', finding: 'Finding the form',
    not_found: 'Form not found', choice: 'Answer the question', approval: 'Awaiting your approval', filling: 'Filling form',
    review: 'Review required', done: 'Autofill completed', error: 'Something went wrong',
    your_turn: 'Your turn on the portal',
    expired: 'Session ended', tab_closed: 'Portal tab closed',
  };
  let current = null;
  let working = false;

  function send(type, extra = {}) {
    working = true;
    render(current);
    return chrome.runtime.sendMessage({ type, ...extra })
      .catch(() => ({ ok: false }))
      .finally(() => { working = false; refresh(); });
  }

  function button(text, onClick, primary = false) {
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = text;
    if (primary) b.className = 'primary';
    b.disabled = working;
    b.addEventListener('click', onClick);
    return b;
  }

  function item(dot, dotClass, field, value, why, tag) {
    const li = document.createElement('li');
    const d = document.createElement('span');
    d.className = `dot ${dotClass}`;
    d.textContent = dot;
    const body = document.createElement('div');
    const f = document.createElement('div');
    f.className = 'field';
    f.textContent = field;
    if (tag) {
      const t = document.createElement('span');
      t.className = `tag ${tag.cls}`;
      t.textContent = tag.text;
      f.appendChild(t);
    }
    body.appendChild(f);
    if (value) { const v = document.createElement('div'); v.className = 'value'; v.textContent = value; body.appendChild(v); }
    if (why) { const w = document.createElement('div'); w.className = 'why'; w.textContent = why; body.appendChild(w); }
    li.append(d, body);
    return li;
  }

  function renderPlan(plan) {
    const actions = $('planActions');
    const other = $('planOther');
    actions.replaceChildren();
    other.replaceChildren();
    for (const a of plan.actions) {
      const li = document.createElement('li');
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.dataset.fieldId = a.field_id;
      box.checked = !a.needs_approval;     // AI guesses start unticked: the user must agree
      box.setAttribute('aria-label', `Fill ${a.field_label}`);
      const body = item('', '', a.field_label, a.value, `From: ${a.value_label}`,
        a.needs_approval ? { cls: 'check', text: 'please check' } : null).lastChild;
      li.append(box, body);
      actions.appendChild(li);
    }
    if (!plan.actions.length) actions.appendChild(item('·', 'skip', 'Nothing to fill on this page', '', '', null));
    const groups = [
      ['missing', '?', 'skip'], ['conflict', '!', 'bad'], ['manual', '✎', 'skip'],
      ['sensitive', '🔒', 'skip'], ['already_filled', '✓', 'ok'], ['unmatched', '?', 'skip'],
    ];
    for (const [key, dot, cls] of groups) {
      for (const note of plan[key] || []) other.appendChild(item(dot, cls, note.field_label, '', note.reason, null));
    }
    $('planOtherWrap').hidden = other.children.length === 0;
  }

  function renderChoice(choice) {
    const list = $('choiceOptions');
    list.replaceChildren();
    const { group, proposal, selected } = choice;
    $('choiceQuestion').textContent = group.question || 'Choose an option';
    const preselect = selected != null ? selected : (proposal ? proposal.option_index : null);
    for (const option of group.options) {
      const li = document.createElement('li');
      const radio = document.createElement('input');
      radio.type = 'radio';
      radio.name = 'nyaya-choice';
      radio.value = String(option.index);
      radio.checked = option.index === preselect;
      radio.id = `choice-${option.index}`;
      const label = document.createElement('label');
      label.htmlFor = radio.id;
      const isProposal = proposal && proposal.option_index === option.index;
      const tag = option.selected ? { cls: 'ok', text: 'chosen' }
        : isProposal ? { cls: 'check', text: proposal.source === 'ai' ? 'AI suggestion, please check' : 'suggested' } : null;
      const body = item('', '', option.text, '', isProposal ? proposal.reason : '', tag).lastChild;
      label.appendChild(body);
      li.append(radio, label);
      list.appendChild(li);
    }
  }

  function renderResults(results) {
    const list = $('resultList');
    list.replaceChildren();
    const why = {
      sensitive: 'Left for you: NyayaBot never fills this kind of field.', unsupported: 'Choose this yourself.',
      already_filled: 'Already had a value; NyayaBot left it alone.', field_missing: 'The field disappeared; fill it yourself.',
      field_locked: 'The field was locked.', option_missing: 'The option was not in the list; pick it yourself.',
      value_changed_by_page: 'The portal changed the value; check it.', empty_after_fill: 'Did not stick; type it yourself.',
      field_changed: 'The page changed; fill it yourself.', frame_gone: 'That part of the page reloaded.',
      page_unreachable: 'Could not reach the page.', error: 'Could not fill this field.',
    };
    for (const r of results || []) {
      const ok = r.status === 'filled';
      list.appendChild(item(ok ? '✓' : r.status === 'skipped' ? '–' : '✗', ok ? 'ok' : r.status === 'skipped' ? 'skip' : 'bad',
        r.field_label, '', ok ? 'Filled and read back.' : (why[r.reason] || r.reason), null));
    }
    const left = (current && current.plan) ? ['missing', 'conflict', 'manual', 'sensitive', 'unmatched']
      .flatMap((k) => current.plan[k] || []) : [];
    for (const note of left) list.appendChild(item('□', 'skip', note.field_label, '', note.reason, { cls: 'check', text: 'you' }));
  }

  function renderActions(state) {
    const box = $('actions');
    box.replaceChildren();
    const add = (el) => box.appendChild(el);
    switch (state.stage) {
      case 'login':
        add(button("I'm logged in", () => send('LOGGED_IN'), true));
        add(button("I'm already on the complaint form", () => send('FILL_THIS_PAGE')));
        break;
      case 'your_turn':
        add(button('Done, continue', () => send('FILL_THIS_PAGE'), true));
        break;
      case 'not_found':
        add(button('Fill this page', () => send('FILL_THIS_PAGE'), true));
        add(button('Try finding the form again', () => send('NEXT_PAGE')));
        break;
      case 'approval': {
        const fill = button('Fill selected fields', () => {
          const ids = Array.from(document.querySelectorAll('#planActions input[type=checkbox]:checked'))
            .map((b) => b.dataset.fieldId);
          send('APPROVE', { fieldIds: ids });
        }, true);
        add(fill);
        add(button('Look at the page again', () => send('FILL_THIS_PAGE')));
        break;
      }
      case 'choice':
        add(button('Choose and continue', () => {
          const picked = document.querySelector('#choiceOptions input[type=radio]:checked');
          if (!picked) { $('message').textContent = 'Please pick one of the options below first.'; return; }
          send('APPROVE_CHOICE', { index: Number(picked.value) });
        }, true));
        add(button("I chose on the page myself, continue", () => send('FILL_THIS_PAGE')));
        break;
      case 'review':
        add(button("I've checked everything", () => send('CONFIRM_REVIEW'), true));
        add(button('Fill the next page of the form', () => send('NEXT_PAGE')));
        add(button('Look at this page again', () => send('FILL_THIS_PAGE')));
        break;
      case 'done':
        add(button('Fill another page', () => send('NEXT_PAGE')));
        add(button('Close session', () => send('CANCEL')));
        break;
      case 'tab_closed':
        add(button('Open the portal again', () => send('REOPEN'), true));
        break;
      case 'error':
        add(button('Try again', () => send('FILL_THIS_PAGE'), true));
        break;
      default:
        break;
    }
  }

  function render(state) {
    if (!state) return;
    $('mascot').innerHTML = window.mascotSvg(working && ['finding', 'filling'].includes(state.stage) ? 'think' : (state.mood || 'wait'));
    $('message').textContent = working && !['finding', 'filling'].includes(state.stage)
      ? 'Working on it…'
      : (state.message || (state.stage === 'idle'
        ? 'Hi! Open your case in NyayaBot and press "Fill on Portal". I\'ll open the official portal and help you fill it.'
        : ''));
    $('portalName').textContent = state.portal ? state.portal.label : '';
    const step = state.step || 0;
    $('progressWrap').hidden = !step;
    $('stepLabel').textContent = STAGE_LABEL[state.stage] || '';
    $('stepCount').textContent = step ? `Step ${step} of ${state.totalSteps}` : '';
    $('bar').style.width = step ? `${Math.round((step / state.totalSteps) * 100)}%` : '0';
    renderActions(state);
    $('choice').hidden = state.stage !== 'choice' || !state.choice;
    if (!$('choice').hidden) renderChoice(state.choice);
    $('plan').hidden = state.stage !== 'approval' || !state.plan;
    if (!$('plan').hidden) renderPlan(state.plan);
    $('results').hidden = !['review', 'done'].includes(state.stage) || !state.results;
    if (!$('results').hidden) renderResults(state.results);
    const steps = $('steps');
    steps.replaceChildren(...(state.steps || []).slice().reverse().map((s) => {
      const li = document.createElement('li');
      li.textContent = s;
      return li;
    }));
    const active = Boolean(state.portal);
    $('focusPortal').hidden = !active;
    $('cancel').hidden = !active;
  }

  function refresh() {
    chrome.runtime.sendMessage({ type: 'GET_STATE' }).then((response) => {
      if (response && response.ok) { current = response.state; render(current); }
    }).catch(() => {});
  }

  chrome.runtime.onMessage.addListener((message) => {
    if (message && message.type === 'STATE') { current = message.state; render(current); }
  });
  // Shown so a screenshot tells which version is running (an old one keeps running
  // until the extension is reloaded in chrome://extensions).
  $('version').textContent = `v${chrome.runtime.getManifest().version}`;
  $('focusPortal').addEventListener('click', () => send('FOCUS_PORTAL'));
  $('cancel').addEventListener('click', () => send('CANCEL'));
  refresh();
})();
