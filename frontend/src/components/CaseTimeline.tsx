'use client';

import React, { useState } from 'react';
import { CaseTimelineMilestone, toggleTimelineEvent } from '@/lib/api';
import { Calendar, CheckCircle2, Circle, Clock, Hourglass } from 'lucide-react';

interface CaseTimelineProps {
  caseId: string;
  milestones: CaseTimelineMilestone[];
  limitationDeadline?: string;
  limitationDaysRemaining?: number;
  onRefresh?: () => void;
}

export default function CaseTimeline({
  caseId,
  milestones,
  limitationDeadline,
  limitationDaysRemaining,
  onRefresh
}: CaseTimelineProps) {
  const [loadingId, setLoadingId] = useState<string | null>(null);

  const handleToggle = async (eventId: string) => {
    setLoadingId(eventId);
    try {
      await toggleTimelineEvent(caseId, eventId);
      if (onRefresh) onRefresh();
    } catch (err) {
      console.error('Failed to toggle milestone', err);
    } finally {
      setLoadingId(null);
    }
  };

  const isCritical = limitationDaysRemaining !== undefined && limitationDaysRemaining <= 30;
  const isExpired = limitationDaysRemaining !== undefined && limitationDaysRemaining < 0;

  return (
    <div className="space-y-6">
      
      {/* Statutory Limitation Period Box */}
      <div className={`p-5 rounded-2xl border ${
        isExpired 
          ? 'bg-red-950/40 border-red-500/50 text-red-200' 
          : isCritical 
          ? 'bg-amber-950/40 border-amber-500/50 text-amber-200' 
          : 'bg-slate-900/90 border-slate-800 text-slate-300'
      }`}>
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div className="flex items-start gap-3">
            <div className={`p-2.5 rounded-xl ${
              isExpired ? 'bg-red-500/20 text-red-400' : isCritical ? 'bg-amber-500/20 text-amber-400' : 'bg-slate-800 text-amber-400'
            }`}>
              <Hourglass className="w-5 h-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h4 className="font-semibold text-white text-sm">Statutory Limitation Period</h4>
                <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full uppercase ${
                  isExpired 
                    ? 'bg-red-500/20 text-red-400 border border-red-500/30' 
                    : isCritical 
                    ? 'bg-amber-500/20 text-amber-400 border border-amber-500/30' 
                    : 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                }`}>
                  {isExpired ? 'Limitation Expired' : isCritical ? 'Critical Action Required' : 'Within Limitation'}
                </span>
              </div>
              <p className="text-xs text-slate-400 mt-1">
                Statutory filing deadline under Indian Limitation Act / Sectoral Tribunal Rules.
              </p>
            </div>
          </div>

          {/* Limitation Metric Badge */}
          <div className="bg-slate-950/80 px-4 py-2 rounded-xl border border-slate-800 flex items-center gap-4">
            <div>
              <div className="text-[11px] text-slate-400 uppercase font-semibold">Final Expiry Date</div>
              <div className="text-sm font-bold text-white">{limitationDeadline || 'N/A'}</div>
            </div>
            <div className="h-8 w-px bg-slate-800"></div>
            <div>
              <div className="text-[11px] text-slate-400 uppercase font-semibold">Days Remaining</div>
              <div className={`text-sm font-bold font-mono ${
                isExpired ? 'text-red-400' : isCritical ? 'text-amber-400' : 'text-emerald-400'
              }`}>
                {limitationDaysRemaining !== undefined ? `${limitationDaysRemaining} Days` : 'N/A'}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Interactive Milestones Tracker */}
      <div className="p-6 rounded-2xl bg-slate-900/70 border border-slate-800 space-y-4">
        <div className="flex items-center justify-between border-b border-slate-800 pb-3">
          <h3 className="font-semibold text-white text-base flex items-center gap-2">
            <Clock className="w-4 h-4 text-amber-400" />
            <span>Procedural Case Milestones & Action Roadmap</span>
          </h3>
          <span className="text-xs text-slate-400">Click circle to mark step complete</span>
        </div>

        <div className="relative pl-6 space-y-6 before:absolute before:left-2.5 before:top-3 before:bottom-3 before:w-0.5 before:bg-slate-800">
          {milestones.map((item, idx) => {
            const isCompleted = item.status === 'COMPLETED';
            return (
              <div key={item.id || idx} className="relative group">
                {/* Node icon toggle */}
                <button
                  onClick={() => handleToggle(item.id)}
                  disabled={loadingId === item.id}
                  className={`absolute -left-6 top-1 w-5 h-5 rounded-full flex items-center justify-center transition-transform hover:scale-110 ${
                    isCompleted 
                      ? 'bg-emerald-500 text-slate-950' 
                      : 'bg-slate-950 border border-slate-700 text-slate-500 hover:border-amber-400 hover:text-amber-400'
                  }`}
                  title={isCompleted ? 'Mark as pending' : 'Mark as complete'}
                >
                  {isCompleted ? (
                    <CheckCircle2 className="w-3.5 h-3.5 stroke-[3]" />
                  ) : (
                    <Circle className="w-2.5 h-2.5" />
                  )}
                </button>

                <div className={`p-4 rounded-xl border transition-all ${
                  isCompleted 
                    ? 'bg-slate-950/40 border-slate-800/60 opacity-80' 
                    : 'bg-slate-950 border-slate-800 hover:border-slate-700'
                }`}>
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <h4 className={`text-sm font-semibold ${isCompleted ? 'line-through text-slate-400' : 'text-white'}`}>
                        {item.title}
                      </h4>
                      {item.is_mandatory && (
                        <span className="text-[10px] uppercase font-bold px-2 py-0.5 rounded bg-amber-500/10 text-amber-400 border border-amber-500/20">
                          Mandatory
                        </span>
                      )}
                    </div>

                    <div className="flex items-center gap-2 text-xs text-slate-400">
                      <Calendar className="w-3.5 h-3.5 text-slate-500" />
                      <span>Target: {item.target_date || 'TBD'}</span>
                    </div>
                  </div>

                  {item.description && (
                    <p className="text-xs text-slate-400 mt-2 leading-relaxed">
                      {item.description}
                    </p>
                  )}
                </div>
              </div>
            );
          })}
        </div>

      </div>

    </div>
  );
}
