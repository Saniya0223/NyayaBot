'use client';

import React, { useState } from 'react';
import { FactGraph, submitClarifications, CaseData } from '@/lib/api';
import { User, IndianRupee, FileCheck, AlertCircle, HelpCircle, Send, CheckCircle2, Layers } from 'lucide-react';

interface FactGraphCardProps {
  factGraph: FactGraph;
  caseId: string;
  onUpdateCase?: (updated: CaseData) => void;
}

export default function FactGraphCard({ factGraph, caseId, onUpdateCase }: FactGraphCardProps) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const handleAnswerChange = (question: string, value: string) => {
    setAnswers(prev => ({ ...prev, [question]: value }));
  };

  const handleSubmitClarifications = async (e: React.FormEvent) => {
    e.preventDefault();
    if (Object.keys(answers).length === 0) return;
    setIsSubmitting(true);
    setErrorMsg(null);
    try {
      const updated = await submitClarifications(caseId, answers);
      if (onUpdateCase) onUpdateCase(updated);
      setAnswers({});
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : 'Failed to submit clarification answers');
    } finally {
      setIsSubmitting(false);
    }
  };

  const scorePct = Math.round((factGraph.completion_score || 0) * 100);

  return (
    <div className="space-y-6">
      
      {/* Top Fact Graph Status Banner */}
      <div className="p-5 rounded-2xl bg-gradient-to-r from-slate-900 via-slate-900 to-slate-800 border border-slate-700/60 shadow-lg">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <Layers className="w-5 h-5 text-amber-400" />
              <h3 className="font-semibold text-lg text-white">Extracted Fact Graph</h3>
              <span className={`text-xs px-2.5 py-0.5 rounded-full font-medium ${
                factGraph.is_complete 
                  ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30' 
                  : 'bg-amber-500/15 text-amber-300 border border-amber-500/30'
              }`}>
                {factGraph.is_complete ? 'Court-Ready Structured' : 'Clarifications Needed'}
              </span>
            </div>
            <p className="text-xs text-slate-400 mt-1">
              Entities, dates, monetary claims, and evidence mapped to Indian statutory templates.
            </p>
          </div>

          {/* Completeness Ring */}
          <div className="flex items-center gap-3 bg-slate-950/60 px-4 py-2 rounded-xl border border-slate-800">
            <div className="text-right">
              <div className="text-xs text-slate-400">Fact Completeness</div>
              <div className="text-sm font-bold text-white">{scorePct}%</div>
            </div>
            <div className="w-10 h-10 rounded-full bg-slate-800 flex items-center justify-center relative">
              <div 
                className="w-full h-full rounded-full border-2 border-amber-500 flex items-center justify-center text-xs font-bold text-amber-400"
                style={{
                  borderColor: scorePct >= 75 ? '#10b981' : scorePct >= 50 ? '#f59e0b' : '#ef4444'
                }}
              >
                {scorePct}%
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Grid of Extracted Fact Nodes */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        
        {/* Node 1: Parties */}
        <div className="p-4 rounded-xl bg-slate-900/90 border border-slate-800 space-y-3">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-400 border-b border-slate-800 pb-2">
            <User className="w-4 h-4 text-amber-400" />
            <span>Memo of Parties</span>
          </div>

          <div className="space-y-3 text-sm">
            <div className="bg-slate-950/70 p-3 rounded-lg border border-slate-800/80">
              <span className="text-[11px] font-bold text-slate-400 uppercase block mb-1">Complainant / Aggrieved Party</span>
              <p className="font-medium text-white">{factGraph.complainant.name || 'Complainant'}</p>
              <p className="text-xs text-slate-400">{factGraph.complainant.city}, {factGraph.complainant.state}</p>
              {factGraph.complainant.phone && <p className="text-xs text-slate-400">Ph: {factGraph.complainant.phone}</p>}
            </div>

            <div className="bg-slate-950/70 p-3 rounded-lg border border-slate-800/80">
              <span className="text-[11px] font-bold text-slate-400 uppercase block mb-1">Opposite Party / Respondent</span>
              <p className="font-medium text-amber-300">{factGraph.opposite_party.name || 'Opposite Party'}</p>
              <p className="text-xs text-slate-400">{factGraph.opposite_party.address || 'Address'} | {factGraph.opposite_party.city}</p>
            </div>
          </div>
        </div>

        {/* Node 2: Financial Claims */}
        <div className="p-4 rounded-xl bg-slate-900/90 border border-slate-800 space-y-3">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-400 border-b border-slate-800 pb-2">
            <IndianRupee className="w-4 h-4 text-emerald-400" />
            <span>Valuation & Financial Breakdown</span>
          </div>

          <div className="space-y-2 text-sm">
            <div className="flex justify-between items-center py-1.5 border-b border-slate-800/60">
              <span className="text-xs text-slate-400">Principal Consideration Paid:</span>
              <span className="font-mono font-medium text-white">₹{factGraph.financials.amount_paid.toLocaleString('en-IN', { minimumFractionDigits: 2 })}</span>
            </div>
            <div className="flex justify-between items-center py-1.5 border-b border-slate-800/60">
              <span className="text-xs text-slate-400">Refund Claimed:</span>
              <span className="font-mono font-medium text-amber-300">₹{factGraph.financials.refund_claimed.toLocaleString('en-IN', { minimumFractionDigits: 2 })}</span>
            </div>
            <div className="flex justify-between items-center py-1.5 border-b border-slate-800/60">
              <span className="text-xs text-slate-400">Harassment / Damages Claim:</span>
              <span className="font-mono font-medium text-slate-300">₹{factGraph.financials.compensation_claimed.toLocaleString('en-IN', { minimumFractionDigits: 2 })}</span>
            </div>
            <div className="flex justify-between items-center py-1.5 border-b border-slate-800/60">
              <span className="text-xs text-slate-400">Legal Expenses:</span>
              <span className="font-mono font-medium text-slate-400">₹{factGraph.financials.litigation_costs_claimed.toLocaleString('en-IN', { minimumFractionDigits: 2 })}</span>
            </div>
            <div className="flex justify-between items-center pt-2 bg-emerald-500/10 p-2.5 rounded-lg border border-emerald-500/20">
              <span className="text-xs font-bold text-emerald-300">Total Statutory Claim Valuation:</span>
              <span className="font-mono font-bold text-emerald-400 text-base">₹{factGraph.financials.total_claim_amount.toLocaleString('en-IN', { minimumFractionDigits: 2 })}</span>
            </div>
          </div>
        </div>

      </div>

      {/* Evidence & Missing Facts Row */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        
        {/* Evidence Inventory */}
        <div className="p-4 rounded-xl bg-slate-900/90 border border-slate-800 space-y-3">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-400 border-b border-slate-800 pb-2">
            <FileCheck className="w-4 h-4 text-sky-400" />
            <span>Evidence Inventory ({factGraph.evidence_inventory.length})</span>
          </div>

          {factGraph.evidence_inventory.length === 0 ? (
            <p className="text-xs text-slate-400 italic">No evidence detected from narrative yet.</p>
          ) : (
            <div className="space-y-2">
              {factGraph.evidence_inventory.map((ev, idx) => (
                <div key={idx} className="flex items-center justify-between p-2.5 rounded-lg bg-slate-950/80 border border-slate-800 text-xs">
                  <div className="flex items-center gap-2">
                    <span className="font-bold text-sky-400 px-1.5 py-0.5 rounded bg-sky-950 border border-sky-800 font-mono">
                      {ev.annexure_label || `A-${idx+1}`}
                    </span>
                    <span className="text-slate-200">{ev.doc_name}</span>
                  </div>
                  <span className="text-[10px] text-emerald-400 font-medium">Mapped</span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Missing Facts / Clarification Questions */}
        <div className="p-4 rounded-xl bg-slate-900/90 border border-slate-800 space-y-3">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-400 border-b border-slate-800 pb-2">
            <HelpCircle className="w-4 h-4 text-amber-400" />
            <span>Missing Facts & Follow-up Questions ({factGraph.clarification_questions.length})</span>
          </div>

          {factGraph.clarification_questions.length === 0 ? (
            <div className="flex items-center gap-2 p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-emerald-300 text-xs">
              <CheckCircle2 className="w-4 h-4 shrink-0" />
              <span>All mandatory facts for filing have been extracted!</span>
            </div>
          ) : (
            <form onSubmit={handleSubmitClarifications} className="space-y-3">
              {factGraph.clarification_questions.map((q, idx) => (
                <div key={idx} className="space-y-1">
                  <label className="text-xs text-slate-300 font-medium flex items-start gap-1.5">
                    <span className="text-amber-400 font-bold">Q{idx+1}:</span> {q}
                  </label>
                  <input
                    type="text"
                    placeholder="Type your answer here..."
                    value={answers[q] || ''}
                    onChange={(e) => handleAnswerChange(q, e.target.value)}
                    className="w-full text-xs px-3 py-2 rounded-lg bg-slate-950 border border-slate-700 text-white placeholder-slate-500 focus:outline-none focus:border-amber-500"
                  />
                </div>
              ))}

              {errorMsg && (
                <p className="text-xs text-red-400 flex items-center gap-1">
                  <AlertCircle className="w-3.5 h-3.5" /> {errorMsg}
                </p>
              )}

              <button
                type="submit"
                disabled={isSubmitting || Object.keys(answers).length === 0}
                className="w-full flex items-center justify-center gap-2 py-2 px-4 rounded-lg bg-amber-500 hover:bg-amber-400 disabled:opacity-50 text-slate-950 font-semibold text-xs transition-colors shadow-md shadow-amber-500/10"
              >
                {isSubmitting ? (
                  <span>Updating Fact Graph...</span>
                ) : (
                  <>
                    <span>Submit Answers & Refine Case</span>
                    <Send className="w-3.5 h-3.5" />
                  </>
                )}
              </button>
            </form>
          )}
        </div>

      </div>

    </div>
  );
}
