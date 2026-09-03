'use client';

import { PhoneCall, ShieldAlert, UserCheck } from 'lucide-react';

interface EscalationBannerProps {
  reason: string;
}

export default function EscalationBanner({ reason }: EscalationBannerProps) {
  return (
    <div className="rounded-xl border border-red-500/40 bg-red-950/30 p-5 text-red-200 shadow-xl backdrop-blur-sm">
      <div className="flex items-start gap-4">
        <div className="p-2.5 rounded-lg bg-red-500/20 text-red-400 shrink-0">
          <ShieldAlert className="w-6 h-6" />
        </div>
        <div className="space-y-2 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="font-semibold text-red-100 text-base tracking-tight">
              Legal Safety & Escalation Guardrail Triggered
            </h3>
            <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded bg-red-500/20 text-red-300 border border-red-500/30">
              Lawyer Consultation Required
            </span>
          </div>
          <p className="text-sm text-red-200/90 leading-relaxed">
            {reason}
          </p>
          <div className="pt-2 flex flex-wrap gap-3">
            <a
              href="tel:15100"
              className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-lg bg-red-600 hover:bg-red-500 text-white font-medium text-xs shadow-md transition-colors"
            >
              <PhoneCall className="w-3.5 h-3.5" />
              Call NALSA Free Legal Aid Helpline (15100)
            </a>
            <div className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-900/80 border border-red-500/30 text-xs text-red-300">
              <UserCheck className="w-3.5 h-3.5 text-amber-400" />
              <span>Contact District Legal Services Authority (DLSA)</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
