'use client';

import { useEffect, useState } from 'react';
import { BriefcaseBusiness, MessageCircle, PanelRight } from 'lucide-react';
import CaseWorkspacePanel from '@/components/CaseWorkspacePanel';
import ChatInterface from '@/components/ChatInterface';
import DocumentIntelligenceModal from '@/components/DocumentIntelligenceModal';
import FactualConfirmModal from '@/components/FactualConfirmModal';
import { ChatMessageItem, fetchChatCase, StructuredCaseProfile } from '@/lib/api';

export default function HomeChatPage() {
  const [profile, setProfile] = useState<StructuredCaseProfile | null>(null);
  const [initialMessages, setInitialMessages] = useState<ChatMessageItem[]>([]);
  const [restoring, setRestoring] = useState(true);
  const [mobileView, setMobileView] = useState<'chat' | 'workspace'>('chat');
  const [documentModal, setDocumentModal] = useState({ open: false, type: '', label: '' });
  const [uploadOpen, setUploadOpen] = useState(false);
  const [injectedMessage, setInjectedMessage] = useState<{ text: string; profile: StructuredCaseProfile; quick_replies?: string[] }>();

  useEffect(() => {
    const caseId = new URLSearchParams(window.location.search).get('case');
    if (!caseId) {
      const timer = window.setTimeout(() => setRestoring(false), 0);
      return () => window.clearTimeout(timer);
    }
    let active = true;
    fetchChatCase(caseId)
      .then((session) => {
        if (!active) return;
        setProfile(session.case_profile);
        setInitialMessages(session.messages);
      })
      .catch(() => {
        if (active) window.history.replaceState({}, '', '/');
      })
      .finally(() => {
        if (active) setRestoring(false);
      });
    return () => { active = false; };
  }, []);

  function updateProfile(next: StructuredCaseProfile) {
    const isNew = !profile;
    setProfile(next);
    if (isNew) window.history.replaceState({}, '', `/?case=${encodeURIComponent(next.case_id)}`);
  }

  function openDocument(type: string, label: string) {
    setDocumentModal({ open: true, type, label });
  }

  if (restoring) {
    return (
      <div className="mx-auto grid min-h-[calc(100vh-9rem)] max-w-[1480px] place-items-center px-4 text-sm text-[#718078]">
        Reopening your case securely…
      </div>
    );
  }

  return (
    <div className="mx-auto flex min-h-[calc(100vh-9rem)] max-w-[1480px] flex-col px-3 py-3 sm:px-5 sm:py-5 lg:px-8">
      <div className="mb-3 flex rounded-2xl border border-[#dbe4de] bg-white p-1 md:hidden" aria-label="Mobile workspace view">
        <button type="button" onClick={() => setMobileView('chat')} className={`flex flex-1 items-center justify-center gap-2 rounded-xl py-2.5 text-xs font-bold ${mobileView === 'chat' ? 'bg-[#174e3b] text-white' : 'text-[#68766f]'}`}><MessageCircle className="size-4" />Chat</button>
        <button type="button" onClick={() => setMobileView('workspace')} className={`flex flex-1 items-center justify-center gap-2 rounded-xl py-2.5 text-xs font-bold ${mobileView === 'workspace' ? 'bg-[#174e3b] text-white' : 'text-[#68766f]'}`}><PanelRight className="size-4" />Case workspace</button>
      </div>

      <div className="grid flex-1 grid-cols-1 gap-4 overflow-hidden md:grid-cols-12 lg:gap-5">
        <div className={`${mobileView === 'chat' ? 'flex' : 'hidden'} min-h-0 flex-col md:col-span-7 md:flex lg:col-span-8`}>
          <ChatInterface
            activeProfile={profile}
            initialCaseId={profile?.case_id}
            initialMessages={initialMessages}
            onProfileUpdated={updateProfile}
            onOpenUploadModal={() => setUploadOpen(true)}
            onTriggerDocumentModal={openDocument}
            injectedBotMessage={injectedMessage}
          />
        </div>
        <div className={`${mobileView === 'workspace' ? 'flex' : 'hidden'} min-h-0 flex-col md:col-span-5 md:flex lg:col-span-4`}>
          <CaseWorkspacePanel profile={profile} onTriggerDocumentModal={openDocument} />
        </div>
      </div>

      <div className="mt-3 flex items-center justify-center gap-2 text-[10px] text-[#7e8a84]">
        <BriefcaseBusiness className="size-3.5" aria-hidden="true" /> Your conversation becomes a saved, actionable case—not a one-off chat.
      </div>

      {documentModal.open && profile ? (
        <FactualConfirmModal
          profile={profile}
          docType={documentModal.type}
          docLabel={documentModal.label}
          onClose={() => setDocumentModal((current) => ({ ...current, open: false }))}
        />
      ) : null}
      {uploadOpen && profile ? (
        <DocumentIntelligenceModal
          caseId={profile.case_id}
          onExtracted={(updatedProfile, replyText, quickReplies) => {
            setProfile(updatedProfile);
            setInjectedMessage({ text: replyText, profile: updatedProfile, quick_replies: quickReplies });
          }}
          onClose={() => setUploadOpen(false)}
        />
      ) : null}
    </div>
  );
}
