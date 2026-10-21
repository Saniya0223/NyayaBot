/*
 * NyayaBot Portal Helper: page logic, run inside the portal page (content script).
 *
 * Reads the visible form (labels and structure, never values), reads links,
 * fills only the fields in an approved plan, and reads every value back.
 * Matching decisions are made by the NyayaBot backend, not here.
 *
 * Never: clicks Submit/Pay, touches password/OTP/CAPTCHA/Aadhaar fields, or
 * overwrites a field that already has a different value.
 *
 * Plain script (no modules) so Chrome can load it as a content script and the
 * offline tests can load it into a simulated page.
 */
(function (root) {
  'use strict';

  const SENSITIVE_RE = /\b(password|passcode|otp|one ?time|captcha|verification code|security code|aadhaar|aadhar|uidai|pan|cvv|cvc|card number|upi pin|atm pin|mpin|first six digit|last four digit)\b/i;
  const FINAL_RE = /\b(submit|register complaint|file complaint|lodge complaint|pay now|pay|payment|make payment|proceed to pay|final submit|i agree|i accept|declare|sign)\b/i;
  const LOGIN_PATH_HINTS = ['login', 'signin', 'sign-in', 'sign_in', 'logon', 'auth', 'otp', 'captcha'];

  const clip = (value, n = 120) => String(value || '').replace(/\s+/g, ' ').trim().slice(0, n);

  function visible(el) {
    if (!el || !el.getClientRects || el.getClientRects().length === 0) return false;
    // `inert` marks parts the user cannot use right now, such as the other steps of an
    // Angular Material stepper: they stay in the page, off screen.
    if (el.closest('[inert]')) return false;
    const view = el.ownerDocument.defaultView;
    return !view || view.getComputedStyle(el).visibility !== 'hidden';
  }

  function labelText(label) {
    const copy = label.cloneNode(true);
    copy.querySelectorAll('input,select,textarea,button').forEach((n) => n.remove());
    return clip(copy.textContent);
  }

  function labelledBy(el) {
    return (el.getAttribute('aria-labelledby') || '').split(/\s+/)
      .map((id) => el.ownerDocument.getElementById(id)).filter(Boolean)
      .map((n) => clip(n.textContent)).join(' ');
  }

  const short = (text) => (text.length > 0 && text.length <= 80 ? text : '');

  function neighbour(el) {
    const cell = el.closest('td,th');
    if (cell && cell.previousElementSibling) {
      const t = short(clip(cell.previousElementSibling.textContent, 200));
      if (t) return t;
    }
    const prev = el.previousElementSibling;
    if (prev && !prev.matches('input,select,textarea,button')) {
      const t = short(clip(prev.textContent, 200));
      if (t) return t;
    }
    const parentPrev = el.parentElement && el.parentElement.previousElementSibling;
    if (parentPrev && parentPrev.matches('label,span,div,td,th,p,legend,b,strong,h3,h4')) {
      return short(clip(parentPrev.textContent, 200));
    }
    return '';
  }

  /** The closest preceding section heading/legend, not a field's own label. */
  function sectionOf(el) {
    const headingSelector = 'h1,h2,h3,h4,h5,h6,[role="heading"],.card-title,.card-primary-header-title';
    const following = el.ownerDocument.defaultView.Node.DOCUMENT_POSITION_FOLLOWING;
    for (let scope = el.parentElement; scope && scope !== el.ownerDocument.body; scope = scope.parentElement) {
      if (scope.matches('fieldset')) {
        const legend = scope.querySelector(':scope > legend');
        if (legend && visible(legend)) return clip(legend.textContent, 80);
      }
      const headings = Array.from(scope.querySelectorAll(headingSelector))
        .filter((heading) => visible(heading) && (heading.compareDocumentPosition(el) & following));
      if (headings.length) return clip(headings[headings.length - 1].textContent, 80);
    }
    return '';
  }

  function describe(el) {
    return [el.id, el.name, el.getAttribute('placeholder'), el.getAttribute('aria-label'),
      el.getAttribute('alt'), el.getAttribute('src'), typeof el.className === 'string' ? el.className : '']
      .filter((v) => typeof v === 'string' && v).join(' ').toLowerCase();
  }

  /**
   * Remove the markers a previous scan left. An element that is now hidden (an answered
   * question, an earlier step) would otherwise keep its old number, and a later click or
   * fill for that number could reach it instead of the element now on screen.
   */
  function untag(doc, ...attributes) {
    doc.querySelectorAll(attributes.map((a) => `[${a}]`).join(', '))
      .forEach((n) => attributes.forEach((a) => n.removeAttribute(a)));
  }

  /** Visible form controls: structure and whether filled, never the values. */
  function scanFields(doc) {
    untag(doc, 'data-nyaya-fill', 'data-nyaya-custom');
    const out = [];
    Array.from(doc.querySelectorAll('input, textarea, select')).forEach((el, i) => {
      const tag = el.tagName.toLowerCase();
      const type = tag === 'input' ? (el.getAttribute('type') || 'text').toLowerCase() : tag;
      if (type === 'hidden' || !visible(el)) return;
      // MUI can render a custom combobox as an input. It is not a text field.
      if (el.matches('[role=combobox], [role=listbox]')) return;
      el.setAttribute('data-nyaya-fill', String(i));
      const entry = {
        index: i, tag, type,
        label: clip(Array.from(el.labels || []).map(labelText).join(' '), 200),
        section: sectionOf(el),
        aria: clip(el.getAttribute('aria-label'), 200) || clip(labelledBy(el), 200),
        placeholder: clip(el.getAttribute('placeholder'), 200),
        name: clip(el.getAttribute('name'), 120),
        ident: clip(el.id, 120),
        neighbour: neighbour(el),
        disabled: Boolean(el.disabled),
        readonly: Boolean(el.readOnly),
        required: Boolean(el.required),
        maxlength: el.maxLength > 0 ? el.maxLength : null,
        options: [], selected_text: '', selected_value: '', filled: false,
      };
      if (tag === 'select') {
        entry.options = Array.from(el.options).slice(0, 400).map((o) => clip(o.text, 120));
        const chosen = el.options[el.selectedIndex];
        entry.selected_text = chosen ? clip(chosen.text, 120) : '';
        entry.selected_value = chosen ? clip(chosen.value, 60) : '';
      } else {
        entry.filled = type === 'checkbox' || type === 'radio' ? Boolean(el.checked) : el.value.length > 0;
        // Portals often pre-fill today's date. Only this yes/no is sent, never the value.
        entry.today_date = entry.filled && isTodaysDate(el.value);
      }
      out.push(entry);
    });
    // Custom dropdowns (e.g. Angular Material mat-select, MUI Select) are not <select>
    // elements. They are listed by their label; NyayaBot picks an option in one only
    // when the user approves it (fillActions, kind "pick").
    const offset = 100000;
    Array.from(doc.querySelectorAll('[role=combobox], [role=listbox]')).forEach((el, i) => {
      if (!visible(el)) return;
      // An open option list is not a field of its own.
      if (el.matches('[role=listbox]') && (el.querySelector('[role=option]') || el.closest('[role=combobox]'))) return;
      el.setAttribute('data-nyaya-custom', String(i));
      out.push({
        index: offset + i, tag: 'custom', type: 'custom',
        label: '', aria: clip(el.getAttribute('aria-label'), 200) || dropdownName(el) || clip(labelledBy(el), 200),
        section: sectionOf(el),
        placeholder: clip(el.getAttribute('placeholder'), 200), name: '', ident: clip(el.id, 120),
        neighbour: neighbour(el), disabled: el.getAttribute('aria-disabled') === 'true', readonly: false,
        required: el.getAttribute('aria-required') === 'true', maxlength: null,
        // Whether something is chosen (not "Select…"); the chosen text itself is not sent.
        options: [], selected_text: '', selected_value: '', filled: !shownIsPlaceholder(el),
      });
    });
    return out;
  }

  /** What a custom dropdown shows right now. */
  function shownValue(el) {
    return clip(el.tagName === 'INPUT' ? el.value : (el.innerText || el.textContent), 200);
  }

  function shownIsPlaceholder(el) {
    const shown = shownValue(el).replace(/\s*\*\s*$/, '');
    const name = dropdownName(el);
    return !shown || /^(select|choose|please select|--)/i.test(shown) || (name && shown.toLowerCase() === name.toLowerCase());
  }

  const pad = (n) => String(n).padStart(2, '0');
  /** Is this text today's date, in the formats portals use (ISO, DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY)? */
  function isTodaysDate(text) {
    const now = new Date();
    const d = pad(now.getDate()), m = pad(now.getMonth() + 1), y = String(now.getFullYear());
    const value = String(text || '').trim();
    return [`${y}-${m}-${d}`, `${d}/${m}/${y}`, `${d}-${m}-${y}`, `${d}.${m}.${y}`].includes(value)
      || value.startsWith(`${y}-${m}-${d}T`);
  }

  /** Counts that suggest a sign-in wall. Like the backend's _is_login_wall, minus the address rule (see isLoginWall). */
  function loginSignals(doc, url) {
    const inputs = Array.from(doc.querySelectorAll('input, textarea, select'))
      .filter((el) => el.type !== 'hidden' && visible(el));
    const signals = {
      inputs: inputs.length,
      password: inputs.filter((el) => el.type === 'password').length,
      otp: inputs.filter((el) => /otp|one.?time/.test(describe(el))).length,
      // A CAPTCHA the user must solve: a CAPTCHA text box, image or challenge frame.
      // Google's invisible reCAPTCHA badge sits on every page of many portals and
      // asks nothing of the user, so it is not counted.
      captcha: Array.from(doc.querySelectorAll('input, img, canvas, iframe'))
        .filter((el) => visible(el) && el.type !== 'hidden' && /captcha/.test(describe(el)) && !isInvisibleRecaptcha(el)).length,
      url_hint: false,
    };
    try {
      const path = new URL(url).pathname.toLowerCase();
      signals.url_hint = LOGIN_PATH_HINTS.some((hint) => path.includes(hint));
    } catch (e) { /* not a URL */ }
    signals.likely_login_wall = isLoginWall(signals);
    return signals;
  }

  function isInvisibleRecaptcha(el) {
    const src = String(el.getAttribute('src') || '');
    return Boolean(el.closest('.grecaptcha-badge')) || /[?&]size=invisible\b/.test(src)
      || (el.getAttribute('title') === 'reCAPTCHA' && Boolean(el.closest('[class*="grecaptcha"]')));
  }

  /**
   * Only a visible password, OTP or CAPTCHA box means "sign in first". The address
   * never does by itself: cybercrime.gov.in keeps "/login" in the address bar after
   * the user signs in, so its question pages (no text boxes at all) looked like a
   * sign-in page. url_hint is still reported, for the Activity list.
   */
  function isLoginWall(s) {
    return Boolean(s.password || s.otp || (s.captcha && s.inputs <= 3));
  }

  function blockText(el) {
    let node = el.parentElement;
    for (let depth = 0; node && depth < 6; depth += 1, node = node.parentElement) {
      const raw = clip(node.textContent, 2000);
      if (raw.length >= 60) return raw.length <= 700 ? raw.slice(0, 120) : '';
    }
    return '';
  }

  /** A dropdown's own label (never its current value): label[for], form-control label, aria. */
  function dropdownName(el) {
    const doc = el.ownerDocument;
    const names = [];
    if (el.id) names.push(...Array.from(doc.querySelectorAll('label')).filter((label) => label.htmlFor === el.id).map(labelText));
    const formControl = el.closest('.MuiFormControl-root, .MuiTextField-root, .form-group');
    if (formControl) names.push(...Array.from(formControl.querySelectorAll('label')).map(labelText));
    names.push(el.getAttribute('aria-label') || '');
    // MUI lists the select itself in aria-labelledby too; its own text is the value.
    names.push(...(el.getAttribute('aria-labelledby') || '').split(/\s+/)
      .filter((id) => id && id !== el.id).map((id) => clip(doc.getElementById(id)?.textContent)));
    names.push(neighbour(el));
    const name = names.map((n) => clip(n, 80).replace(/\s*\*\s*$/, '')).find(Boolean);
    return name || '';
  }

  /** Labelled dropdowns and the options of any open list, for a portal's route steps. */
  function listControls(doc) {
    const out = [];
    for (const el of doc.querySelectorAll('[role=combobox], input[aria-haspopup=listbox], .MuiSelect-select')) {
      if (!visible(el) || out.some((c) => c.el === el || c.el.contains(el) || el.contains(c.el))) continue;
      const name = dropdownName(el);
      if (!name) continue;
      const value = clip(el.tagName === 'INPUT' ? el.value : (el.innerText || el.textContent), 100);
      out.push({ el, kind: 'dropdown', text: name, value });
    }
    for (const el of doc.querySelectorAll('[role=option], .MuiMenuItem-root')) {
      const text = clip(el.innerText || el.textContent, 100);
      if (visible(el) && text && !out.some((c) => c.el === el)) out.push({ el, kind: 'option', text, value: '' });
    }
    return out;
  }

  /** Visible links and buttons (plus labelled dropdowns and open list options): text, target and card context only. */
  function scanLinks(doc) {
    untag(doc, 'data-nyaya-nav');
    const out = [];
    const nodes = Array.from(doc.querySelectorAll('a, button, [role=button], [role=link], [role=menuitem], input[type=button], input[type=submit]'));
    const lists = new Map();
    for (const control of listControls(doc)) {
      if (!nodes.includes(control.el)) nodes.push(control.el);
      lists.set(control.el, control);
    }
    Array.from(nodes).forEach((el, i) => {
      if (!visible(el)) return;
      const tag = el.tagName.toLowerCase();
      const list = lists.get(el);
      const rendered = clip(el.innerText || el.textContent || el.value);
      const accessible = clip(el.getAttribute('aria-label') || el.title);
      // A hamburger often renders only a symbol while its accessible name says
      // "Open menu". Preserve that name so the backend can choose it exactly.
      const text = list ? list.text
        : (accessible && (!rendered || !/[a-z0-9ऀ-ॿ]/i.test(rendered)) ? accessible : rendered);
      if (!text) return;
      el.setAttribute('data-nyaya-nav', String(i));
      out.push({
        index: i, tag, type: (el.getAttribute('type') || '').toLowerCase(), text,
        href: tag === 'a' ? String(el.href || '').slice(0, 2000) : '',
        context: blockText(el),
        in_dialog: Boolean(el.closest('[role=dialog], [aria-modal=true], dialog, .modal')),
        disabled: Boolean(el.disabled) || el.getAttribute('aria-disabled') === 'true',
        submits_form: (el.type === 'submit' || el.type === 'image') && Boolean(el.form),
        kind: list ? list.kind : 'link',
        value: list ? list.value : '',
      });
    });
    return out;
  }

  // ── Multiple-choice questions (option cards, radio-style buttons) ──
  // A control like Back/Next/Submit, or a short action label, is never an answer.
  const CONTROL_RE = /^(back|next|previous|prev|continue|proceed|submit|final submit|save|cancel|close|reset|clear|preview|login|log in|logout|log out|sign in|sign out|home|menu|ok|done|skip|upload|browse|add|remove|delete|edit|view|print|download|search|get otp|resend otp|verify)\b/i;
  const ACTION_LABEL_RE = /^(submit|final submit|save|pay now|pay|payment|make payment|proceed to pay|i agree|i accept|agree|accept|declare|sign|confirm|cancel|delete|withdraw)\b/i;
  const OPTION_ROLES = ['button', 'radio', 'option', 'checkbox', 'menuitemradio'];
  // Whole class names that mark an answer card or button ("card", "question-card",
  // "option-box", "btn"), never structural ones like "card-header" or "card-body".
  const OPTION_CLASS_RE = /^((card|option|choice|tile|answer|pill|chip|btn|selectable|clickable)|[a-z0-9]+-(card|option|choice|tile|answer|pill|chip)|(option|choice|answer|tile)-[a-z0-9-]+)$/;
  const hasOptionClass = (el) => typeof el.className === 'string'
    && el.className.toLowerCase().split(/\s+/).some((c) => OPTION_CLASS_RE.test(c) && !/-(header|body|title|footer|text|label|group|list|container|wrapper|grid|row)$/.test(c));
  // Page chrome is matched on exact tags, roles and whole class names only. Matching
  // parts of class names (e.g. "stepper" in "mat-horizontal-stepper-content", or
  // "sidenav" in "mat-sidenav-content") wrongly marked the whole main area as chrome,
  // which hid every answer card on the Cyber Crime portal.
  // role="tablist" is not chrome either: Angular Material puts it on the whole
  // <mat-stepper>, questions and answers included. The step tabs have role="tab".
  const CHROME_TAGS = ['nav', 'header', 'footer', 'aside', 'mat-sidenav', 'mat-drawer', 'mat-toolbar', 'mat-step-header'];
  const CHROME_ROLES = ['navigation', 'menu', 'menubar', 'tab', 'banner', 'contentinfo'];
  const CHROME_CLASSES = new Set(['sidebar', 'side-bar', 'sidenav', 'side-nav', 'navbar', 'nav-bar', 'topbar', 'top-bar',
    'breadcrumb', 'breadcrumbs', 'header', 'footer', 'menu', 'side-menu', 'main-menu', 'nav-menu', 'toolbar',
    'mat-step-header', 'mat-horizontal-stepper-header', 'mat-vertical-stepper-header', 'step-header', 'stepper-header',
    'mat-horizontal-stepper-header-container']);
  // Content areas: walking up from an element stops here, so a menu is found only below them.
  const CONTENT_ROLES = ['tabpanel', 'main', 'dialog'];
  // Icons inside a card ("?" help bubbles, Material icon ligatures like "help") are not answer text.
  const ICON_SELECTOR = 'mat-icon, .mat-icon, .material-icons, .material-icons-outlined, .material-symbols-outlined, i, svg, img, [aria-hidden="true"]';
  // ...and an icon on its own is never an answer.
  const ICON_ONLY = 'mat-icon, .mat-icon, .material-icons, .material-icons-outlined, .material-symbols-outlined, i, svg, img';
  const SKIP_TAGS = ['SCRIPT', 'STYLE', 'TEMPLATE', 'OPTION', 'INPUT', 'SELECT', 'TEXTAREA', 'HTML', 'BODY'];

  /** Visible text without icons, collapsed and clipped. */
  function cleanText(el, n = 300) {
    if (!el.querySelector(ICON_SELECTOR)) return clip(el.innerText || el.textContent, n);
    const copy = el.cloneNode(true);
    copy.querySelectorAll(ICON_SELECTOR).forEach((node) => node.remove());
    return clip(copy.textContent, n);
  }
  const roughText = (el) => clip(el.innerText || el.textContent, 400);
  const isActionLabel = (text) => text.length <= 40 && ACTION_LABEL_RE.test(text);
  // "← Back" and "« Previous" are controls too.
  const isControl = (text) => CONTROL_RE.test(text.replace(/^[^a-z0-9ऀ-ॿ]+/i, ''));

  function chromeReason(el) {
    for (let n = el; n && n !== el.ownerDocument.body; n = n.parentElement) {
      const tag = n.tagName.toLowerCase();
      const role = n.getAttribute && n.getAttribute('role');
      // A step's or tab's panel, a dialog or the main area is content, whatever is around it.
      if (CONTENT_ROLES.includes(role) || tag === 'main') return '';
      if (CHROME_TAGS.includes(tag)) return `tag:${tag}`;
      if (CHROME_ROLES.includes(role)) return `role:${role}`;
      const classes = typeof n.className === 'string' ? n.className.toLowerCase().split(/\s+/) : [];
      const hit = classes.find((c) => CHROME_CLASSES.has(c));
      if (hit) return `class:${hit}`;
    }
    return '';
  }

  /** The radio or checkbox a label points to with for="…". */
  function linkedInput(el) {
    const id = el.tagName === 'LABEL' ? el.getAttribute('for') : '';
    const input = id ? el.ownerDocument.getElementById(id) : null;
    return input && /^(radio|checkbox)$/i.test(input.type || '') ? input : null;
  }

  /**
   * Chrome passes a pointer cursor down to everything inside the element that sets it,
   * so the text and the "help" icon inside an answer card look clickable too, and each
   * card fell apart into two "answers". Only the element that sets the cursor counts,
   * unless the cursor comes from a box too big to be one answer (a whole grid of cards).
   */
  function setsPointer(el) {
    const view = el.ownerDocument.defaultView;
    if (!view || view.getComputedStyle(el).cursor !== 'pointer') return false;
    const parent = el.parentElement;
    if (!parent || parent === el.ownerDocument.body) return true;
    return view.getComputedStyle(parent).cursor !== 'pointer' || roughText(parent).length > 260;
  }

  function isClickable(el) {
    const tag = el.tagName.toLowerCase();
    if (tag === 'button') return true;
    if (OPTION_ROLES.includes(el.getAttribute('role'))) return true;
    if (tag === 'label' && (el.querySelector('input[type=radio], input[type=checkbox]') || linkedInput(el))) return true;
    if (el.hasAttribute('onclick')) return true;
    if (el.hasAttribute('tabindex') && el.getAttribute('tabindex') !== '-1') return true;
    if (setsPointer(el)) return true;
    // Many portals style answer cards without a pointer cursor: a card-like class is
    // enough, as long as the element holds a short answer (checked by the caller).
    return hasOptionClass(el);
  }

  function isRealLink(el) {
    if (el.tagName !== 'A' || !el.hasAttribute('href')) return false;
    const href = el.getAttribute('href') || '';
    return href !== '#' && !/^javascript:/i.test(href);
  }

  const CHOSEN_WORDS = new Set(['active', 'selected', 'checked']);
  const NOT_WORDS = new Set(['un', 'in', 'non', 'not', 'no']);
  /** Class names like "active", "is-selected", "selectedCard", "card--checked"; not "inactive" or "notSelected". */
  function classSaysChosen(n) {
    const raw = typeof n.className === 'string' ? n.className : '';
    return raw.split(/\s+/).some((cls) => {
      const words = cls.replace(/([a-z0-9])([A-Z])/g, '$1 $2').toLowerCase().split(/[\s_-]+/);
      return words.some((w, i) => CHOSEN_WORDS.has(w) && !NOT_WORDS.has(words[i - 1]));
    });
  }

  function isSelected(el) {
    const marked = (n) => ['aria-pressed', 'aria-checked', 'aria-selected'].some((a) => n.getAttribute(a) === 'true')
      || classSaysChosen(n);
    if (marked(el)) return true;
    if (Array.from(el.querySelectorAll('[aria-pressed], [aria-checked], [aria-selected], [class]')).slice(0, 30).some(marked)) return true;
    const input = el.querySelector('input[type=radio], input[type=checkbox]') || linkedInput(el);
    return Boolean(input && input.checked);
  }

  /** Step bars, tabs and menus are never the question. */
  function looksLikeChrome(el) {
    return Boolean(chromeReason(el)
      || el.querySelector('[role=tab], mat-step-header, nav, [role=navigation], [role=menu], [role=menubar]'));
  }

  /**
   * The question: the nearest text shown before the first answer, looking no further
   * out than its step or dialog, and never in a menu, the step bar or other answers.
   */
  function questionFor(container, members, allOptions) {
    let node = members[0];
    for (let depth = 0; node && node !== node.ownerDocument.body && depth < 9; depth += 1, node = node.parentElement) {
      if (node !== members[0] && CONTENT_ROLES.includes(node.getAttribute('role'))) break;
      for (let sib = node.previousElementSibling; sib; sib = sib.previousElementSibling) {
        if (!visible(sib) || allOptions.some((o) => sib.contains(o)) || looksLikeChrome(sib)) continue;
        const text = cleanText(sib, 260).replace(/\s*\*\s*$/, '');
        if (text.length >= 3 && text.length <= 200) return text;
      }
    }
    return clip(container.getAttribute('aria-label') || labelledBy(container), 200);
  }

  /** Tag and class names without state words, so a chosen card is the same kind as the rest. */
  function kindOf(el) {
    const classes = (typeof el.className === 'string' ? el.className : '').split(/\s+/)
      .filter((c) => c && !/active|selected|checked|focus|hover|disabled|ripple/i.test(c)).sort();
    return [el.tagName, ...classes].join('.');
  }

  /** Text in the box besides its answers, such as a question heading of its own. */
  function hasOwnText(container, members) {
    const size = (n) => String(n.textContent || '').replace(/\s+/g, '').length;
    return size(container) - members.reduce((sum, m) => sum + size(m), 0) > 2;
  }

  function nothingBetween(a, b) {
    for (let n = a.nextElementSibling; n; n = n.nextElementSibling) {
      if (n === b) return true;
      if (visible(n) && clip(n.textContent).length) return false;
    }
    return false;
  }

  /**
   * Answer cards set out in several rows ("row" / "col" grids) first come out as one
   * group per row, and a last card alone in its row comes out alone. Join neighbouring
   * rows of the same kind of card with nothing in between, and a lone card with the
   * rows inside its box. A row with text of its own (a heading) starts a new question.
   */
  function mergeRows(groups) {
    const merged = [];
    for (const g of groups) {
      const kind = kindOf(g.members[0]);
      const last = merged[merged.length - 1];
      if (last && last.kind === kind && last.tail.parentElement === g.container.parentElement
          && nothingBetween(last.tail, g.container) && !hasOwnText(g.container, g.members)) {
        last.members.push(...g.members);
        last.tail = g.container;
      } else {
        merged.push({ container: g.container, tail: g.container, kind, members: [...g.members] });
      }
    }
    for (const lone of merged) {
      if (lone.members.length !== 1) continue;
      const host = merged.find((g) => g !== lone && g.members.length > 1 && g.kind === lone.kind
        && lone.container.contains(g.container));
      if (!host) continue;
      host.members.push(lone.members[0]);
      host.members.sort((a, b) => (a.compareDocumentPosition(b) & 4 ? -1 : 1));   // 4: b comes after a
      lone.members = [];
    }
    return merged.filter((g) => g.members.length);
  }

  /**
   * Questions answered by clicking an option: groups of sibling option elements with
   * the question text shown above them. Menus, the step bar, links to other pages,
   * Back/Next and short action labels (Submit, Pay now, I agree) are never options.
   * Returns { groups, stats }: unanswered groups first, then best-scoring; each option
   * is tagged data-nyaya-choice. `stats` explains what was skipped, for diagnosis.
   */
  function scanChoiceGroups(doc) {
    untag(doc, 'data-nyaya-choice');
    const stats = { scanned: 0, clickable: 0, options: 0, groups: 0, dropped: {}, chrome: {}, samples: {} };
    // What was skipped and why, with a few short examples: shown in the Activity list,
    // so one screenshot explains a page where no answers were found.
    const drop = (why, text) => {
      stats.dropped[why] = (stats.dropped[why] || 0) + 1;
      if (text) {
        const seen = stats.samples[why] || (stats.samples[why] = []);
        if (seen.length < 3) seen.push(clip(text, 24));
      }
      return false;
    };
    const root = doc.body || doc.documentElement;
    const all = Array.from(root.getElementsByTagName('*')).slice(0, 8000);
    stats.scanned = all.length;
    // Only elements holding a short answer can be options: this also stops a big
    // clickable wrapper (e.g. a "card" around the whole question) hiding the answers.
    const clickable = all.filter((el) => {
      if (SKIP_TAGS.includes(el.tagName) || el.matches(ICON_ONLY) || el.closest('svg')) return false;
      const rough = roughText(el);
      return rough.length >= 2 && rough.length <= 260 && visible(el) && isClickable(el);
    });
    stats.clickable = clickable.length;
    // Something that holds two or more clickable things (a "card" wrapping a whole
    // question, a grid) is a container, not an answer, whatever its class or cursor.
    const leaves = clickable.filter((el) => {
      let inside = 0;
      for (const other of clickable) {
        if (other !== el && el.contains(other) && ++inside >= 2) return false;
      }
      return true;
    });
    const leafSet = new Set(leaves);
    let options = leaves.filter((el) => {
      for (let n = el.parentElement; n; n = n.parentElement) if (leafSet.has(n)) return false;
      return true;
    });
    options = options.filter((el) => {
      const text = cleanText(el);
      if (text.length < 2 || text.length > 220) return drop('length', text);
      if (!/[a-zऀ-ॿ]/i.test(text)) return drop('no_words', text);
      if (isControl(text)) return drop('control_button', text);
      if (isActionLabel(text)) return drop('action_label', text);
      if (isRealLink(el)) return drop('link', text);
      const chrome = chromeReason(el);
      if (chrome) {
        stats.chrome[chrome] = (stats.chrome[chrome] || 0) + 1;
        return drop('menu_or_layout', text);
      }
      if (el.disabled || el.getAttribute('aria-disabled') === 'true') return drop('disabled', text);
      if ((el.type === 'submit' || el.type === 'image') && el.form) return drop('form_submit', text);
      if (el.querySelector('input:not([type=radio]):not([type=checkbox]), select, textarea')) return drop('contains_field', text);
      return true;
    });
    stats.options = options.length;
    // Each option goes with the nearest box that also holds another option.
    const byContainer = new Map();
    for (const option of options) {
      let container = null;
      for (let n = option.parentElement, depth = 0; n && depth < 6; n = n.parentElement, depth += 1) {
        if (options.some((o) => o !== option && n.contains(o))) { container = n; break; }
      }
      if (!container) { drop('alone', cleanText(option)); continue; }
      if (!byContainer.has(container)) byContainer.set(container, []);
      byContainer.get(container).push(option);
    }
    const groups = mergeRows(Array.from(byContainer, ([container, members]) => ({ container, members })));
    let next = 0;
    const result = [];
    for (const { container, members } of groups) {
      if (members.length < 2) { drop('alone', cleanText(members[0])); continue; }
      const question = questionFor(container, members, options);
      const texts = members.map((el) => cleanText(el));
      const avg = texts.reduce((sum, t) => sum + t.length, 0) / texts.length;
      const tagged = members.slice(0, 40).map((el, i) => {
        const index = next;
        next += 1;
        el.setAttribute('data-nyaya-choice', String(index));
        return { index, text: texts[i], selected: isSelected(el) };
      });
      const answered = tagged.some((o) => o.selected);
      const score = (answered ? 0 : 20) + (/[?*]\s*$|\?/.test(question) ? 3 : 0) + (question ? 2 : 0)
        + Math.min(members.length, 5) + Math.min(avg / 10, 4) - (avg < 6 ? 6 : 0);
      result.push({ score, question, options: tagged });
    }
    result.sort((a, b) => b.score - a.score);
    const out = result.slice(0, 5).map((g, group) => ({ group, question: g.question, options: g.options }));
    stats.groups = out.length;
    return { groups: out, stats };
  }

  const scanChoices = (doc) => scanChoiceGroups(doc).groups;

  /** The deepest element that holds the option's text: clicking it reaches handlers on any ancestor. */
  function clickTarget(el) {
    const total = roughText(el).length || 1;
    let target = el;
    for (let depth = 0; depth < 6; depth += 1) {
      const inner = Array.from(target.children).filter((c) => !c.matches(ICON_SELECTOR) && roughText(c).length >= total * 0.7);
      if (inner.length !== 1) break;
      target = inner[0];
    }
    return target;
  }

  function fireClick(el) {
    const view = el.ownerDocument.defaultView;
    const opts = { bubbles: true, cancelable: true, view };
    for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup']) {
      try {
        const Ctor = type.startsWith('pointer') && view.PointerEvent ? view.PointerEvent : view.MouseEvent;
        el.dispatchEvent(new Ctor(type, opts));
      } catch (e) { /* older engines */ }
    }
    el.click();
  }

  /** Click one approved option; falls back to its exact text if the page re-rendered. */
  function clickChoice(doc, index, text) {
    let el = doc.querySelector(`[data-nyaya-choice="${Number(index)}"]`);
    if ((!el || !visible(el)) && text) {
      scanChoices(doc);
      const sameText = Array.from(doc.querySelectorAll('[data-nyaya-choice]')).filter((n) => cleanText(n) === clip(text, 300));
      el = sameText.length === 1 ? sameText[0] : null;
    }
    if (!el || !visible(el)) return { ok: false, reason: 'gone' };
    const label = cleanText(el);
    if (isActionLabel(label) || isControl(label) || ((el.type === 'submit' || el.type === 'image') && el.form)) {
      return { ok: false, reason: 'refused_action' };
    }
    if (isSelected(el)) return { ok: true, text: label, already: true };
    markBusy(3000);
    const input = el.querySelector('input[type=radio], input[type=checkbox]');
    fireClick(input && !visible(input) ? el : clickTarget(el));
    return { ok: true, text: label, selected: isSelected(el) };
  }

  const NEXT_RE = /^(next|continue|proceed)(\s+step)?$/i;

  /**
   * Press the page's one plain "Next" / "Continue" / "Proceed" button, to move to the
   * next question. Never Submit, Save, Pay or anything else. If that button submits
   * its form (common in Angular wizards), that one submit is allowed through the guard.
   * `near`: the option just chosen; if several Next buttons show, the one in the same
   * step, form or dialog as that option is the one meant.
   */
  function nextButtons(doc) {
    return Array.from(doc.querySelectorAll('button, [role=button], input[type=button], input[type=submit], a'))
      .filter((el) => visible(el) && !el.disabled && el.getAttribute('aria-disabled') !== 'true' && !chromeReason(el))
      .filter((el) => NEXT_RE.test(clip(cleanText(el, 60).replace(/[←-⇿➔➡>»›]+/g, '') || el.value || '', 40)));
  }

  function clickNext(doc, near) {
    let buttons = nextButtons(doc);
    const option = buttons.length > 1 && near != null ? doc.querySelector(`[data-nyaya-choice="${Number(near)}"]`) : null;
    const SCOPES = '[role=tabpanel], [role=dialog], form, section, main';
    for (let scope = option && option.closest(SCOPES); scope; scope = scope.parentElement && scope.parentElement.closest(SCOPES)) {
      const inScope = buttons.filter((b) => scope.contains(b));
      if (inScope.length === 1) { buttons = inScope; break; }
      if (inScope.length > 1) break;      // more than one even within this step: still ambiguous
    }
    if (buttons.length !== 1) return { ok: false, reason: buttons.length ? 'ambiguous' : 'no_next' };
    const el = buttons[0];
    allowSubmitUntil = Date.now() + 1500;
    allowedClick = el;
    markBusy(3000);
    try { fireClick(el); } finally { allowedClick = null; }
    return { ok: true, text: clip(cleanText(el, 60) || el.value, 40) };
  }

  // ── Guard: while NyayaBot is acting, nothing it causes may submit or pay ──
  let allowSubmitUntil = 0;
  let allowedClick = null;
  let busyUntil = 0;
  const busy = () => Date.now() < busyUntil;
  function markBusy(ms = 2000) { busyUntil = Math.max(busyUntil, Date.now() + ms); }

  function looksFinal(node) {
    const label = [node.innerText || node.textContent, node.value, node.getAttribute && node.getAttribute('aria-label')]
      .filter(Boolean).join(' ');
    return ((node.type === 'submit' || node.type === 'image') && Boolean(node.form)) || FINAL_RE.test(label);
  }

  /**
   * Blocks submit events and clicks on submit/pay controls that were NOT made by the
   * user (event.isTrusted false) while NyayaBot is filling or clicking. The user's own
   * clicks always pass, so the user can submit the form themselves.
   */
  // Per copy of this script, not a flag on the page: a copy re-injected after the
  // extension was reloaded needs its own guard, since only it knows when it is busy.
  const guardedDocs = new WeakSet();
  function installGuard(doc) {
    if (guardedDocs.has(doc)) return;
    guardedDocs.add(doc);
    doc.addEventListener('submit', (event) => {
      // A real user submit always goes through. A scripted one is blocked only
      // while NyayaBot is acting, so the portal's own scripts work normally otherwise.
      if (event.isTrusted || !busy()) return;
      if (Date.now() < allowSubmitUntil) { allowSubmitUntil = 0; return; }   // the one approved "Next"
      event.preventDefault();
      event.stopImmediatePropagation();
    }, true);
    doc.addEventListener('click', (event) => {
      if (event.isTrusted) return;
      if (allowedClick && event.target && allowedClick.contains(event.target)) return;
      const target = event.target && event.target.closest
        ? event.target.closest('button, input[type=submit], input[type=image], [role=button], a') : null;
      if (target && busy() && looksFinal(target)) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    }, true);
  }

  /** Click one link chosen by the backend. Refuses anything that could submit or pay. */
  function clickLink(doc, index) {
    const el = doc.querySelector(`[data-nyaya-nav="${Number(index)}"]`);
    if (!el || !visible(el)) return { ok: false, reason: 'gone' };
    if (looksFinal(el)) return { ok: false, reason: 'refused_final_action' };
    markBusy(3000);
    // MUI-style dropdowns open on mouse-down, not on a bare click: press them fully.
    if (el.matches('[role=combobox], [role=option], input[aria-haspopup=listbox], .MuiSelect-select, .MuiMenuItem-root')) fireClick(el);
    else el.click();
    return { ok: true, text: clip(el.innerText || el.textContent || el.value, 100) };
  }

  // ── Filling ──
  function nativeSetter(el) {
    const view = el.ownerDocument.defaultView;
    const proto = el.tagName === 'TEXTAREA' ? view.HTMLTextAreaElement.prototype
      : el.tagName === 'SELECT' ? view.HTMLSelectElement.prototype : view.HTMLInputElement.prototype;
    return Object.getOwnPropertyDescriptor(proto, 'value').set;
  }

  /**
   * Set a value so Angular/React notice it: the plain prototype setter bypasses
   * framework wrappers, then real input/change/blur events let them read it.
   */
  function setValue(el, value) {
    const view = el.ownerDocument.defaultView;
    try { el.focus({ preventScroll: true }); } catch (e) { /* not focusable */ }
    nativeSetter(el).call(el, value);
    el.dispatchEvent(new view.Event('input', { bubbles: true }));
    el.dispatchEvent(new view.Event('change', { bubbles: true }));
    try { el.blur(); } catch (e) { /* ignore */ }
  }

  function currentShown(el) {
    if (el.tagName === 'SELECT') {
      const chosen = el.options[el.selectedIndex];
      return chosen ? clip(chosen.text, 200) : '';
    }
    return String(el.value || '');
  }

  function fieldDescriptor(el) {
    return [describe(el), Array.from(el.labels || []).map(labelText).join(' ')].join(' ');
  }

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  /**
   * Fill approved actions [{index, field_id, kind, value}] and read each one back.
   * Returns [{field_id, status: filled|failed|skipped, reason}]. Never throws.
   */
  async function fillActions(doc, actions, options = {}) {
    const pause = options.pause == null ? 150 : options.pause;
    const results = [];
    for (const action of actions || []) {
      const out = { field_id: action.field_id, status: 'failed', reason: '' };
      try {
        markBusy(5000);
        if (action.kind === 'pick') { results.push(await pickOption(doc, action, pause)); continue; }
        const el = doc.querySelector(`[data-nyaya-fill="${Number(action.index)}"]`);
        if (!el || !visible(el)) { out.reason = 'field_missing'; results.push(out); continue; }
        if (el.disabled || el.readOnly) { out.reason = 'field_locked'; results.push(out); continue; }
        const tag = el.tagName.toLowerCase();
        const type = tag === 'input' ? (el.getAttribute('type') || 'text').toLowerCase() : tag;
        if (type === 'password' || SENSITIVE_RE.test(fieldDescriptor(el))) {
          out.status = 'skipped'; out.reason = 'sensitive'; results.push(out); continue;
        }
        if (['checkbox', 'radio', 'file', 'submit', 'button', 'image', 'reset'].includes(type)) {
          out.status = 'skipped'; out.reason = 'unsupported'; results.push(out); continue;
        }
        if (el.matches('[role=combobox], [role=listbox]')) {
          out.status = 'skipped'; out.reason = 'unsupported'; results.push(out); continue;
        }
        const value = String(action.value == null ? '' : action.value);
        if (action.kind === 'select') {
          if (tag !== 'select') { out.reason = 'field_changed'; results.push(out); continue; }
          const option = Array.from(el.options).find((o) => clip(o.text, 200) === clip(value, 200));
          if (!option) { out.reason = 'option_missing'; results.push(out); continue; }
          const chosen = el.options[el.selectedIndex];
          const placeholder = !chosen || ['', '0', '-1'].includes(chosen.value) || /^\W*(select|choose|please)/i.test(chosen.text);
          if (!placeholder && chosen.text !== option.text) { out.status = 'skipped'; out.reason = 'already_filled'; results.push(out); continue; }
          setValue(el, option.value);
        } else {
          if (tag === 'select') { out.reason = 'field_changed'; results.push(out); continue; }
          // Never overwrite what is there, except today's date that the portal put in itself,
          // and only when the user approved replacing it.
          const portalToday = action.replace_today && isTodaysDate(el.value);
          if (el.value && el.value !== value && !portalToday) { out.status = 'skipped'; out.reason = 'already_filled'; results.push(out); continue; }
          setValue(el, value);
        }
        if (pause) await sleep(pause);
        const shown = currentShown(el);
        const expected = action.kind === 'select' ? clip(value, 200) : value;
        if (shown === expected) { out.status = 'filled'; }
        else { out.reason = shown ? 'value_changed_by_page' : 'empty_after_fill'; }
      } catch (error) {
        out.reason = 'error';
      }
      results.push(out);
    }
    markBusy(1500);
    return results;
  }

  // ── Portal dropdowns (custom lists): read options, pick one the user approved ──
  const OPTION_SELECTOR = '[role=option], .MuiMenuItem-root, mat-option, .mat-mdc-option';
  const normText = (text) => clip(text, 300).toLowerCase().replace(/[^a-z0-9ऀ-ॿ]+/g, ' ').trim();

  function customField(doc, index) {
    const n = Number(index);
    return n >= 100000 ? doc.querySelector(`[data-nyaya-custom="${n - 100000}"]`) : doc.querySelector(`[data-nyaya-fill="${n}"]`);
  }

  const openOptions = (doc) => Array.from(doc.querySelectorAll(OPTION_SELECTOR)).filter(visible);

  function closeList(doc, el) {
    const view = doc.defaultView;
    for (const target of [doc.activeElement, el, doc.body]) {
      if (!target) continue;
      try { target.dispatchEvent(new view.KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true })); } catch (e) { /* ignore */ }
    }
  }

  async function openList(doc, el, wait) {
    markBusy(3000);
    fireClick(el);
    try { el.focus({ preventScroll: true }); } catch (e) { /* not focusable */ }
    await sleep(wait);
  }

  /**
   * Open each listed portal dropdown, read its option texts and close it again, so
   * NyayaBot can suggest an option. Nothing is chosen. Returns { "<index>": [texts] }.
   */
  async function peekOptions(doc, indices, options = {}) {
    const wait = options.wait == null ? 400 : options.wait;
    const found = {};
    for (const index of (indices || []).slice(0, 8)) {
      const el = customField(doc, index);
      if (!el || !visible(el) || el.getAttribute('aria-disabled') === 'true') continue;
      await openList(doc, el, wait);
      found[String(index)] = openOptions(doc).slice(0, 200).map((o) => clip(cleanText(o, 120), 120)).filter(Boolean);
      closeList(doc, el);
      await sleep(Math.min(wait, 150));
    }
    return found;
  }

  /** Choose the option with exactly this text in a portal dropdown, then read the dropdown back. */
  async function pickOption(doc, action, pause) {
    const out = { field_id: action.field_id, status: 'failed', reason: '' };
    const el = customField(doc, action.index);
    if (!el || !visible(el)) { out.reason = 'field_missing'; return out; }
    if (el.getAttribute('aria-disabled') === 'true' || el.disabled) { out.reason = 'field_locked'; return out; }
    if (SENSITIVE_RE.test(dropdownName(el))) { out.status = 'skipped'; out.reason = 'sensitive'; return out; }
    const want = normText(action.value);
    if (!shownIsPlaceholder(el)) {
      if (normText(shownValue(el)) === want) { out.status = 'filled'; return out; }
      out.status = 'skipped'; out.reason = 'already_filled'; return out;
    }
    await openList(doc, el, pause);
    let matches = openOptions(doc).filter((o) => normText(cleanText(o)) === want);
    if (!matches.length && el.tagName === 'INPUT') {
      setValue(el, String(action.value));          // a searchable list: type to show the option
      await sleep(pause);
      matches = openOptions(doc).filter((o) => normText(cleanText(o)) === want);
    }
    if (matches.length !== 1) { closeList(doc, el); out.reason = 'option_missing'; return out; }
    markBusy(3000);
    fireClick(matches[0]);
    await sleep(pause);
    if (openOptions(doc).length) closeList(doc, el);
    const shown = normText(shownValue(el));
    if (shown === want) out.status = 'filled';
    else out.reason = shown ? 'value_changed_by_page' : 'empty_after_fill';
    return out;
  }

  // ── Pop-ups and where the user is ──

  const DIALOG_SELECTOR = '[role=dialog], [role=alertdialog], [aria-modal=true], dialog[open], .MuiDialog-paper, .modal.show';

  /** Open pop-ups: title, choices (and which is selected) and button words. Never values typed by the user. */
  function scanDialogs(doc) {
    const seen = [];
    const out = [];
    for (const box of doc.querySelectorAll(DIALOG_SELECTOR)) {
      if (!visible(box) || seen.some((other) => other.contains(box) || box.contains(other))) continue;
      seen.push(box);
      const titled = (box.getAttribute('aria-labelledby') || '').split(/\s+/).map((id) => doc.getElementById(id)).find(Boolean)
        || box.querySelector('h1,h2,h3,h4,h5,h6,[role=heading],.MuiDialogTitle-root,.modal-title');
      const options = Array.from(box.querySelectorAll('input[type=radio], [role=radio]')).filter(visible).slice(0, 20).map((r) => {
        const label = r.tagName === 'INPUT'
          ? clip(Array.from(r.labels || []).map(labelText).join(' ') || r.closest('label')?.textContent || neighbour(r), 200)
          : clip(cleanText(r), 200);
        const selected = r.tagName === 'INPUT' ? r.checked : r.getAttribute('aria-checked') === 'true';
        return { text: label, selected };
      }).filter((o) => o.text);
      const buttons = Array.from(box.querySelectorAll('button, [role=button], input[type=submit], input[type=button]'))
        .filter((b) => visible(b) && !b.matches('[role=radio]'))
        .map((b) => clip(cleanText(b, 60) || b.value || '', 60)).filter(Boolean).slice(0, 10);
      // A pop-up that is itself a form (text boxes, lists) is filled like any page.
      const fields = Array.from(box.querySelectorAll('input:not([type=radio]):not([type=checkbox]):not([type=hidden]), textarea, select'))
        .filter(visible).length;
      out.push({ title: clip(titled ? cleanText(titled, 200) : '', 200), options, buttons, fields });
    }
    return out.slice(0, 5);
  }

  /** Where the user is: the page heading, the current wizard step, and the page's one Next button. */
  function pageContext(doc) {
    const ctx = { title: '', step: 0, steps: 0, step_label: '', next: '' };
    const heading = Array.from(doc.querySelectorAll('h1, h2, h3, h4, h5, .card-primary-header-title'))
      .find((h) => visible(h) && !chromeReason(h) && !h.closest(DIALOG_SELECTOR) && clip(h.textContent).length >= 3);
    ctx.title = heading ? clip(cleanText(heading, 80), 80) : '';
    const mui = Array.from(doc.querySelectorAll('.MuiStepLabel-root')).filter(visible);
    const mat = Array.from(doc.querySelectorAll('mat-step-header')).filter(visible);
    const steps = mui.length ? mui : mat;
    const current = steps.findIndex((s) => s.matches('.Mui-active, [aria-selected=true], [aria-current=step]')
      || Boolean(s.querySelector('.Mui-active, [aria-current=step]')));
    if (steps.length > 1 && current >= 0) {
      ctx.steps = steps.length;
      ctx.step = current + 1;
      const label = steps[current].querySelector('.MuiStepLabel-label, .mat-step-text-label') || steps[current];
      ctx.step_label = clip(cleanText(label, 80), 80);
    }
    const nexts = nextButtons(doc);
    if (nexts.length === 1) ctx.next = clip(cleanText(nexts[0], 40) || nexts[0].value, 40);
    return ctx;
  }

  root.NyayaPage = {
    scanFields, loginSignals, isLoginWall, scanLinks, clickLink, fillActions, installGuard, markBusy,
    scanChoices, scanChoiceGroups, clickChoice, clickNext, peekOptions, scanDialogs, pageContext,
    _internals: { SENSITIVE_RE, FINAL_RE, looksFinal, busy },
  };
})(typeof window !== 'undefined' ? window : globalThis);
