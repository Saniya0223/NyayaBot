'use client';

import React, { useState, useEffect } from 'react';
import { fetchPortalDossier, PortalFilingDossier } from '@/lib/api';
import { ExternalLink, Copy, Check, FileCheck2, Landmark, Clock, IndianRupee, Layers } from 'lucide-react';

interface PortalGuideProps {
  caseId: string;
}

export default function PortalGuide({ caseId }: PortalGuideProps) {
  const [dossier, setDossier] = useState<PortalFilingDossier | null>(null);
  const [loading, setLoading] = useState(true);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  useEffect(() => {
    async function loadDossier() {
      try {
        const data = await fetchPortalDossier(caseId);
        setDossier(data);
      } catch (err) {
        console.error('Failed to load dossier', err);
      } finally {
        setLoading(false);
      }
    }
    loadDossier();
  }, [caseId]);

  const copyToClipboard = (key: string, value: string) => {
    navigator.clipboard.writeText(value);
    setCopiedKey(key);
    setTimeout(() => setCopiedKey(null), 2000);
  };

  if (loading) {
    return (
      <div className="text-center py-16 text-slate-400 text-xs animate-pulse">
        Assembling Government Portal Filing Dossier...
      </div>
    );
  }

  if (!dossier) {
    return (
      <div className="text-center py-16 text-slate-400 text-xs">
        Failed to load filing dossier. Please retry.
      </div>
    );
  }

  return (
    <div className="space-y-6">
      
      {/* Portal Metadata Card */}
      <div className="p-6 rounded-2xl bg-gradient-to-r from-slate-900 via-slate-900 to-indigo-950 border border-indigo-500/20 shadow-xl space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <Landmark className="w-5 h-5 text-indigo-400" />
              <h3 className="font-semibold text-lg text-white">{dossier.portal_name}</h3>
            </div>
            <p className="text-xs text-slate-300 mt-1">
              Designated Official Forum: <span className="font-semibold text-amber-300">{dossier.forum_name}</span>
            </p>
          </div>

          <a
            href={dossier.portal_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold shadow-lg shadow-indigo-600/20 transition-all self-start sm:self-auto"
          >
            <span>Open Government Portal</span>
            <ExternalLink className="w-3.5 h-3.5" />
          </a>
        </div>

        {/* Quick Stats Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-2 border-t border-slate-800">
          <div className="flex items-center gap-3 p-3 rounded-xl bg-slate-950/70 border border-slate-800 text-xs">
            <IndianRupee className="w-4 h-4 text-emerald-400 shrink-0" />
            <div>
              <span className="text-[11px] text-slate-400 block">Prescribed Filing Fee</span>
              <span className="font-semibold text-white">{dossier.prescribed_fees}</span>
            </div>
          </div>

          <div className="flex items-center gap-3 p-3 rounded-xl bg-slate-950/70 border border-slate-800 text-xs">
            <Clock className="w-4 h-4 text-amber-400 shrink-0" />
            <div>
              <span className="text-[11px] text-slate-400 block">Expected Resolution Timeline</span>
              <span className="font-semibold text-white">{dossier.estimated_resolution_time}</span>
            </div>
          </div>
        </div>
      </div>

      {/* Step-by-Step Filing Roadmap */}
      <div className="space-y-4">
        <h4 className="font-semibold text-white text-sm flex items-center gap-2">
          <Layers className="w-4 h-4 text-amber-400" />
          <span>Step-by-Step Guided Filing Process</span>
        </h4>

        {dossier.steps.map((step) => (
          <div key={step.step_number} className="p-5 rounded-2xl bg-slate-900/80 border border-slate-800 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <span className="w-6 h-6 rounded-full bg-amber-500 text-slate-950 font-bold text-xs flex items-center justify-center">
                  {step.step_number}
                </span>
                <h5 className="font-semibold text-white text-sm">{step.title}</h5>
              </div>
              {step.portal_section && (
                <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-slate-950 text-slate-400 border border-slate-800">
                  {step.portal_section}
                </span>
              )}
            </div>

            <p className="text-xs text-slate-300 leading-relaxed pl-8">
              {step.description}
            </p>

            {/* One-Click Copyable Fields */}
            {step.fields_to_fill.length > 0 && (
              <div className="pl-8 pt-2 space-y-2">
                <span className="text-[11px] font-bold uppercase tracking-wider text-amber-400/90 block">
                  Copy-Paste Form Payload:
                </span>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  {step.fields_to_fill.map((f, i) => {
                    const uniqueKey = `${step.step_number}-${i}`;
                    const isCopied = copiedKey === uniqueKey;
                    return (
                      <div key={i} className="flex items-center justify-between p-2.5 rounded-lg bg-slate-950 border border-slate-800 text-xs">
                        <div>
                          <span className="text-[10px] text-slate-500 block">{f.label}</span>
                          <span className="font-mono text-slate-200 font-medium">{f.value}</span>
                        </div>
                        <button
                          onClick={() => copyToClipboard(uniqueKey, f.value)}
                          className="p-1.5 rounded bg-slate-900 hover:bg-slate-800 text-slate-400 hover:text-white transition-colors"
                          title="Copy field value"
                        >
                          {isCopied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                        </button>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Documents to upload */}
            {step.documents_to_upload.length > 0 && (
              <div className="pl-8 pt-2 space-y-1">
                <span className="text-[11px] font-bold uppercase tracking-wider text-sky-400/90 block">
                  Required Uploads for this Step:
                </span>
                <ul className="space-y-1 text-xs text-slate-300">
                  {step.documents_to_upload.map((doc, idx) => (
                    <li key={idx} className="flex items-center gap-2">
                      <FileCheck2 className="w-3.5 h-3.5 text-sky-400" />
                      <span>{doc}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Pro Tip */}
            {step.pro_tip && (
              <div className="ml-8 p-3 rounded-lg bg-amber-500/5 border border-amber-500/20 text-xs text-amber-200/90">
                <span className="font-bold text-amber-400">Pro Tip: </span>
                {step.pro_tip}
              </div>
            )}
          </div>
        ))}
      </div>

    </div>
  );
}
