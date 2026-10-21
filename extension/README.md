# NyayaBot Portal Helper (Chrome extension)

Fills your NyayaBot case details into an official portal form, inside your own
Chrome. You log in, enter OTPs and CAPTCHAs, review, and submit yourself.

**Supported now** (the server picks by case type; with two, the NyayaBot page asks which):

| Case type | Portal | When |
|---|---|---|
| Cyber fraud | Cyber Crime portal (cybercrime.gov.in) | Report the fraud first |
| Cyber fraud | RBI CMS (cms.rbi.org.in) | The bank didn't resolve your complaint within 30 days |
| Consumer | e-Jagriti (e-jagriti.gov.in) | Complaint before the Consumer Commission |
| General (government office) | CPGRAMS (pgportal.gov.in) | Grievance against a department |
| Employment | Samadhan (samadhan.labour.gov.in) | Unpaid wages, termination, other employer disputes |

Each has a local development test page. RBI CMS blocks automated downloads, so its
wording could not be read in advance: its test page uses common complaint-form wording,
and the live form is read by the general rules (check the plan carefully the first time).
The older National Consumer Helpline Auto-Fill path is separate and unchanged.
The e-Jagriti flow follows the portal's
[official Consumer Complaint filing guide](https://e-jagriti.gov.in/static/media/CC_Filing.34e0fc5dd03b1d864cab.pdf).

## How it works

1. In a cyber-fraud or consumer case, NyayaBot shows **Fill on Portal**. It asks the backend
   for a short-lived session code (30 minutes, this case only) and hands it to the
   extension. The extension opens the portal in a new tab.
2. Click the NyayaBot icon in Chrome's toolbar to open the helper in the side panel.
   Chrome only lets an extension open its panel after you click it.
3. Log in on the portal yourself. Then press **I'm logged in**.
4. The helper reads visible field labels and their nearest section heading (never
   field contents) and asks the
   NyayaBot backend for a plan. The backend matches fields using the portal's own
   wording first, then NyayaBot's general rules, and only then AI for unknown fields.
   AI sees field labels, section headings and value names only, never your details.
   On e-Jagriti, the complainant and opposite-party sections must be identified
   separately. An ambiguous section is left for you rather than guessed, and the
   helper names the headings it didn't recognise. The account's **Complete Profile**
   pages ("Personal Information", "Address Information") hold only your details, so
   they are filled from your NyayaBot profile, unless the same page also has the
   other party's section.

   **Portal lists, dates and pop-ups (every portal):** for a portal's own dropdowns
   the helper reads the options (choosing nothing), and after you tick it, picks the
   option with exactly matching text and reads it back. A date the portal pre-filled
   with today's date can be replaced with your case's date, only after you tick it.
   When a pop-up opens (for example e-Jagriti's "Select Filing Type" with SUBMIT), the
   helper never presses its buttons: the mascot explains what it asks and what fits
   your case, you press the button, then **Done, continue**. Every message says where
   you are (page and step), what was filled, what's left for you, and what to press next.

   **Where portal knowledge lives:** the extension contains no portal-specific steps.
   Each portal is a profile in `backend/app/services/portal_autofill.py`: its address,
   field wording, which section headings belong to whom (`sections`), and the steps
   from the signed-in page to the form (`route`). Adding or fixing a portal is a
   server change only.
5. If no form is showing, the helper clicks the one link that clearly leads to it
   ("Register a Complaint" on the cyber-crime card). For e-Jagriti it opens the
   sign-in route, opens the collapsed dashboard menu if needed, then follows
   **File New Case**, opens the **Case Type** selector, chooses the exact
   **Consumer Complaint** option, and presses one plain **Next** to reach the
   case-details step after you log in. It will not treat homepage success stories or case-search
   filters as filing questions, and it never wanders through payment or draft pages.
5a. On a cyber-crime multiple-choice question ("What type of complaint is this?", "A Loan /
   Lending Agent"), the helper shows the question and its options and suggests one:
   the first question by a fixed rule from your case type, deeper ones by AI using only
   what happened (emails, UPI IDs and long numbers masked; never your name, contacts or
   IDs). You keep or change the selection and press **Choose and continue**; it clicks
   that option and presses a plain **Next** (never Submit, Save, Pay or I agree).
   If there is no plain Next button, or the page does not move on after Next, it says
   so and hands over to you instead of asking the same question again.
6. You see the plan. AI suggestions are marked **please check** and start unticked.
   On e-Jagriti, "Paid as consideration" is also unticked: NyayaBot's disputed
   amount might not equal the price paid. "Claim Consideration" is always yours
   to enter because it affects the Commission and fee.
   Press **Fill selected fields**.
7. Each value is typed the way the portal's framework expects and then read back.
   Fields that depend on others (district after state) are filled once they load.
8. Review the checklist and check the portal yourself. If the form has more pages,
   press the portal's Next yourself, then **Fill the next page of the form**.
9. On the last page, press **I've checked everything** and submit on the portal.
   NyayaBot never presses Submit and cannot see whether the portal accepted your complaint.

**Never filled:** passwords, OTPs, CAPTCHAs, Aadhaar, PAN, card details, national-ID
uploads, payment and declaration controls. Fields that already have a value are left alone.

## Install for development

1. Open `chrome://extensions` and turn on **Developer mode** (top right).
2. Click **Load unpacked** and choose this `extension` folder.
3. Pin "NyayaBot Portal Helper" from the puzzle-piece menu so its icon is visible.
4. Reload the NyayaBot page (http://localhost:3000) so it detects the extension.

After changing any extension file, press the reload icon on its card in
`chrome://extensions` and reload the portal tab.

## Try it on the local test form first

The local forms copy key field layouts and wording, send nothing anywhere, and
show what the "portal" received. The e-Jagriti test page simulates the collapsed
dashboard menu, Case Type dropdown and Next step, repeats the same labels for
both parties, and simulates PIN-driven state/district/post-office values. A red
banner appears if anything ever presses Submit or Pay.

1. Backend: set `AUTOFILL_TEST_FORM=1` in `backend/.env`, restart the backend.
2. Frontend: set `NEXT_PUBLIC_AUTOFILL_TEST_FORM=1` in `frontend/.env.local`,
   restart the frontend. In this mode **Fill on Portal** appears on every case.
   Consumer cases open `/api/v1/autofill/test-ejagriti`; other cases open the
   existing cyber-crime test form.
3. Press **Fill on Portal**, open the helper, and go through the steps. Check the
   "What the portal received" box at the bottom of the form.

Turn both settings off again before trying the real portal.

## Using the real portal

- Use clearly fake test details first. Both portals may save a draft as you fill;
  delete any test draft from the portal afterwards. Do not use real identities
  or submit a test filing.
- The portal requires your own mobile number and OTP to log in.
- Watch the backend terminal for `event=autofill_plan` lines: they show how many
  fields matched, needed approval, were missing or were left for you.
- The panel's title shows the helper's version (`v0.3.1`). If it shows an
  older number after an update, reload the extension in `chrome://extensions`.
- If the helper finds nothing on a page, open **Activity**: the "Looked for answer
  options" line says what it skipped, with examples, and which layout rule matched.
- The portal keeps `/login` in the address bar after you sign in. The helper only
  treats a page as a sign-in page when it shows a password, OTP or CAPTCHA box.
- On e-Jagriti, review all party details carefully. The helper leaves ambiguous
  sections, additional/proforma parties, cause-of-action location, case category,
  address type, Commission, advocate details, uploads, declarations and payment
  to you. MUI custom dropdowns and date pickers may need your own input.
- The real e-Jagriti workflow has not been verified with a live login/OTP; the
  local form is an offline approximation, not proof that every live page matches.

## Limits

- Desktop Chrome or Edge only; not on phones.
- Works with the NyayaBot backend on this computer (`localhost:8000`) and the app
  on `localhost:3000`. Other addresses need adding to `manifest.json` and `background.js`.
- Custom dropdowns, date pickers and multi-step wizards may need manual completion;
  the checklist says which fields.
- Field matching depends on the portal's wording; if the portal changes it, fields
  show as "left for you" until the mapping is updated in
  `backend/app/services/portal_autofill.py`.
- The mascot is a placeholder drawn in `sidepanel/mascot.js`; replace it freely.

## Tests (offline, no Chrome needed)

```
cd extension
npm install
npm test
```

`tests/page.test.js` runs the page logic against the local test form in a simulated
page. `tests/background.test.js` runs the controller against a fake Chrome and a fake
backend. Backend tests: `backend/tests/test_autofill_extension_api.py`.
