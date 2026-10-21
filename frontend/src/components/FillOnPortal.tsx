'use client';

import { useEffect, useState } from 'react';
import { MousePointerClick, PuzzleIcon } from 'lucide-react';
import { API_BASE_URL, getPortalOptions, PortalOption, startPortalAutofill, StructuredCaseProfile } from '@/lib/api';
import { detectPortalExtension, handOffToExtension } from '@/lib/portalExtension';

// Development only: open each portal's local test layout instead of the real portal.
const USE_TEST_FORM = process.env.NEXT_PUBLIC_AUTOFILL_TEST_FORM === '1';

type Status =
  | { kind: 'idle' }
  | { kind: 'working' }
  | { kind: 'no_extension' }
  | { kind: 'started'; portal: string; sidePanelOpened: boolean }
  | { kind: 'error'; message: string };

const ERRORS: Record<string, string> = {
  unknown_backend: 'The extension only works with the NyayaBot backend on this computer (localhost:8000).',
  session_expired: 'That session expired before the extension could use it. Please try again.',
  backend_unreachable: 'The extension could not reach the NyayaBot backend.',
  no_reply: 'The extension did not answer. Reload this page and try again.',
};

/**
 * "Fill on Portal": hands this case to the NyayaBot Portal Helper extension.
 * Which official portals fit the case comes from the server; with several (for
 * example Cyber Crime and RBI for a bank fraud), the user picks one.
 */
export default function FillOnPortal({ profile }: { profile: StructuredCaseProfile }) {
  const [status, setStatus] = useState<Status>({ kind: 'idle' });
  const [portals, setPortals] = useState<PortalOption[]>([]);
  const [chosen, setChosen] = useState<string>('');

  useEffect(() => {
    let alive = true;
    getPortalOptions(profile.case_id)
      .then((reply) => {
        if (!alive) return;
        setPortals(reply.portals);
        setChosen(reply.portals[0]?.key || '');
      })
      .catch(() => { if (alive) setPortals([]); });
    return () => { alive = false; };
  }, [profile.case_id, profile.category]);

  if (!portals.length) return null;
  const portal = portals.find((p) => p.key === chosen) || portals[0];

  async function start() {
    setStatus({ kind: 'working' });
    if (!(await detectPortalExtension())) {
      setStatus({ kind: 'no_extension' });
      return;
    }
    try {
      const session = await startPortalAutofill(profile.case_id, USE_TEST_FORM && portal.test ? portal.test : portal.key);
      const result = await handOffToExtension({ code: session.code, portal: session.portal, apiBase: API_BASE_URL });
      setStatus(result.ok
        ? { kind: 'started', portal: session.portal.label, sidePanelOpened: result.sidePanelOpened }
        : { kind: 'error', message: ERRORS[result.error] || 'The extension could not start. Please try again.' });
    } catch (error) {
      setStatus({ kind: 'error', message: error instanceof Error ? error.message : 'Could not start Fill on Portal.' });
    }
  }

  return (
    <section className="mt-3" aria-label="Fill on portal with the NyayaBot extension">
      {portals.length > 1 ? (
        <fieldset className="mb-2 rounded-xl border border-[#e6dccb] p-2 text-[11px] leading-4 text-[#303030]">
          <legend className="px-1 text-[10px] font-semibold text-[#6b6458]">Which portal?</legend>
          {portals.map((option) => (
            <label key={option.key} className="flex cursor-pointer items-start gap-2 rounded-lg p-1.5 hover:bg-[#faf6ee]">
              <input
                type="radio"
                name={`portal-${profile.case_id}`}
                value={option.key}
                checked={option.key === portal.key}
                onChange={() => setChosen(option.key)}
                className="mt-0.5"
              />
              <span>
                <span className="block font-semibold">{option.label}</span>
                {option.when ? <span className="block text-[#6b6458]">{option.when}</span> : null}
              </span>
            </label>
          ))}
        </fieldset>
      ) : null}
      <button
        type="button"
        onClick={start}
        disabled={status.kind === 'working'}
        className="flex w-full items-center justify-center gap-2 rounded-xl bg-[#181818] px-3 py-2.5 text-xs font-bold text-white transition hover:bg-[#080808] disabled:cursor-wait disabled:bg-[#c8bda8]"
      >
        <MousePointerClick className="size-4" />
        {status.kind === 'working' ? 'Starting…' : 'Fill on Portal'}
      </button>
      <p className="mt-1 text-center text-[10px] text-[#999187]">
        {USE_TEST_FORM && portal.test
          ? `Test mode: opens a local copy of ${portal.label}.`
          : `Opens ${portal.label} in your browser. You log in and submit.`}
      </p>

      {status.kind === 'started' ? (
        <div role="status" className="mt-2 rounded-xl border border-[#ead5a0] bg-[#fff8e2] p-3 text-[11px] leading-5 text-[#303030]">
          <p className="font-semibold">{status.portal} opened in a new tab.</p>
          {status.sidePanelOpened ? (
            <p>Your NyayaBot helper is open on the right. Follow its steps.</p>
          ) : (
            <p className="flex items-start gap-1.5"><PuzzleIcon className="mt-0.5 size-3.5 shrink-0" />Click the NyayaBot icon in Chrome&apos;s toolbar (under the puzzle piece, pin it for next time) to open your helper.</p>
          )}
        </div>
      ) : null}

      {status.kind === 'no_extension' ? (
        <div role="alert" className="mt-2 rounded-xl border border-[#e6d3a8] bg-[#fff8ea] p-3 text-[11px] leading-5 text-[#5b4a22]">
          <p className="font-semibold">The NyayaBot Portal Helper extension isn&apos;t installed or active.</p>
          <ol className="mt-1 list-decimal pl-4">
            <li>Open <span className="font-mono">chrome://extensions</span> and turn on Developer mode.</li>
            <li>Click &ldquo;Load unpacked&rdquo; and choose the project&apos;s <span className="font-mono">extension</span> folder.</li>
            <li>Reload this page, then press Fill on Portal again.</li>
          </ol>
        </div>
      ) : null}

      {status.kind === 'error' ? (
        <p role="alert" className="mt-2 text-center text-[11px] text-[#a33f32]">{status.message}</p>
      ) : null}
    </section>
  );
}
