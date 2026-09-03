'use client';

import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from 'react';
import {
  ArrowUp,
  Bot,
  FileText,
  LoaderCircle,
  Paperclip,
  ShieldCheck,
  Sparkles,
  UserRound,
} from 'lucide-react';
import { API_BASE_URL, ChatMessageItem, fetchLLMStatus, LLMStatus, sendChatMessage, StructuredCaseProfile } from '@/lib/api';

interface ChatInterfaceProps {
  activeProfile?: StructuredCaseProfile | null;
  initialCaseId?: string;
  initialMessages?: ChatMessageItem[];
  onProfileUpdated: (profile: StructuredCaseProfile) => void;
  onOpenUploadModal: () => void;
  onTriggerDocumentModal: (docType: string, docLabel: string) => void;
  injectedBotMessage?: { text: string; profile: StructuredCaseProfile; quick_replies?: string[] };
}

const suggestions = [
  "My landlord isn't returning my deposit.",
  "My company hasn't paid my salary.",
  'A seller refuses my refund.',
  "Police aren't taking my complaint.",
  'I was cheated through UPI.',
];

// Gemini replies arrive as markdown. Emphasis markers must be stripped or they
// render as literal asterisks in the chat bubble.
function stripEmphasis(value: string) {
  return value.replace(/\*\*/g, '').replace(/\*([^*]+)\*/g, '$1');
}

function BotMessageContent({ text }: { text: string }) {
  return (
    <div className="space-y-2.5">
      {text.split('\n').filter(Boolean).map((line, index) => {
        const cleaned = line.trim();
        if (cleaned.startsWith('### ')) {
          return <h3 key={`${cleaned}-${index}`} className="pt-1 text-xs font-bold uppercase tracking-[0.08em] text-[#174e3b]">{stripEmphasis(cleaned.slice(4))}</h3>;
        }
        // Gemini emits '*' bullets; '•' and '-' also appear in fallback copy.
        const bullet = /^([•*-])\s+/.exec(cleaned);
        if (bullet) {
          return <p key={`${cleaned}-${index}`} className="flex gap-2"><span className="text-[#2f755b]">•</span><span>{stripEmphasis(cleaned.slice(bullet[0].length))}</span></p>;
        }
        return <p key={`${cleaned}-${index}`}>{stripEmphasis(cleaned)}</p>;
      })}
    </div>
  );
}

export default function ChatInterface({
  activeProfile,
  initialCaseId,
  initialMessages = [],
  onProfileUpdated,
  onOpenUploadModal,
  onTriggerDocumentModal,
  injectedBotMessage,
}: ChatInterfaceProps) {
  const [messages, setMessages] = useState<ChatMessageItem[]>(initialMessages);
  const [inputText, setInputText] = useState('');
  const [isSending, setIsSending] = useState(false);
  const [caseId, setCaseId] = useState<string | undefined>(initialCaseId);
  const [sendError, setSendError] = useState<string | null>(null);
  const [llmStatus, setLLMStatus] = useState<LLMStatus | null>(null);
  const [statusUnreachable, setStatusUnreachable] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const mountedRef = useRef(true);
  const messageSequence = useRef(0);

  useEffect(() => {
    mountedRef.current = true;
    void fetchLLMStatus()
      .then((status) => {
        if (mountedRef.current) { setLLMStatus(status); setStatusUnreachable(false); }
      })
      .catch((error: unknown) => {
        // Surface the real reason during development instead of silently
        // fabricating an "unconfigured" state that blames a missing key.
        console.error(`[NyayaBot] LLM status fetch failed against ${API_BASE_URL}:`, error);
        if (mountedRef.current) { setLLMStatus(null); setStatusUnreachable(true); }
      });
    return () => { mountedRef.current = false; };
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, [messages, isSending]);

  useEffect(() => {
    if (!injectedBotMessage) return;
    const timer = window.setTimeout(() => {
      messageSequence.current += 1;
      setMessages((current) => [
        ...current,
        { id: `upload-${messageSequence.current}`, sender: 'bot', text: injectedBotMessage.text, quick_replies: injectedBotMessage.quick_replies || ['Continue my case'] },
      ]);
      setCaseId(injectedBotMessage.profile.case_id);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [injectedBotMessage]);

  async function handleSend(textOverride?: string) {
    const text = (textOverride ?? inputText).trim();
    if (!text || isSending) return;

    messageSequence.current += 1;
    const userMessage: ChatMessageItem = { id: `user-${messageSequence.current}`, sender: 'user', text };
    const history = [...messages, userMessage];
    setMessages(history);
    setInputText('');
    setSendError(null);
    setIsSending(true);

    try {
      const response = await sendChatMessage({ message: text, case_id: caseId, history });
      if (!mountedRef.current) return;
      setCaseId(response.case_profile.case_id);
      setLLMStatus((current) => ({
        provider: response.llm_provider,
        model: response.llm_model || current?.model || 'unknown',
        configured: response.llm_mode === 'gemini',
        mode: response.llm_mode,
        message: response.llm_mode === 'gemini'
          ? 'This response was generated through the backend Gemini API.'
          : 'This response used limited demo workflow rules.',
      }));
      onProfileUpdated(response.case_profile);
      setMessages((current) => [
        ...current,
        {
          id: response.message_id,
          sender: 'bot',
          text: response.reply_text,
          quick_replies: response.quick_replies,
          suggested_action: response.suggested_action,
        },
      ]);
    } catch (error) {
      // Log the real, non-sensitive failure so URL/schema/CORS faults are
      // diagnosable instead of hidden behind the generic bubble below.
      console.error(`[NyayaBot] chat request failed against ${API_BASE_URL}/chat/message:`, error);
      if (!mountedRef.current) return;
      const message = error instanceof Error ? error.message : 'Please try again.';
      setSendError(message);
      setMessages((current) => [
        ...current,
        {
          id: `error-${messageSequence.current}`,
          sender: 'bot',
          text: "I couldn't process that request right now. Your case information has been preserved. Please try again.",
        },
      ]);
    } finally {
      if (mountedRef.current) setIsSending(false);
    }
  }

  // The backend status endpoint is the only source of truth. A failed fetch means
  // the API is unreachable, which must not be reported as a missing key.
  const statusLabel = statusUnreachable
    ? 'Backend unreachable'
    : llmStatus
      ? llmStatus.mode === 'gemini'
        ? 'Gemini'
        : 'Limited demo'
      : 'Checking';

  const statusDetail = statusUnreachable
    ? `Cannot reach ${API_BASE_URL}`
    : llmStatus
      ? llmStatus.mode === 'gemini'
        ? `${llmStatus.model} · English · Hindi · Hinglish`
        : llmStatus.configured
          ? 'Provider unavailable this turn · local workflow fallback'
          : 'Gemini key required · local workflow fallback'
      : 'Checking provider status';

  function submit(event: FormEvent) {
    event.preventDefault();
    void handleSend();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void handleSend();
    }
  }

  function handleDocumentAction(action: { type: string; doc_type?: string; label: string }) {
    if (llmStatus?.mode === 'gemini' && activeProfile?.missing_document_fields?.length) {
      void handleSend(`I want to prepare the ${action.label}. Please ask me for the missing details.`);
      return;
    }
    onTriggerDocumentModal(action.doc_type || 'GENERAL_COMPLAINT_LETTER', action.label || 'Prepare document');
  }

  return (
    <section className="flex h-full min-h-[620px] flex-col overflow-hidden rounded-[26px] border border-[#dbe4de] bg-white paper-shadow" aria-label="Conversation with NyayaBot">
      <header className="flex items-center justify-between border-b border-[#e4eae6] px-4 py-3.5 sm:px-5">
        <div className="flex items-center gap-3">
          <span className="grid size-9 place-items-center rounded-xl bg-[#e8f2ec] text-[#174e3b]"><Bot className="size-[18px]" aria-hidden="true" /></span>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-bold text-[#1c2b25]">Ask NyayaBot</h2>
              <span
                className={`flex items-center gap-1 text-[10px] font-semibold ${llmStatus?.mode === 'gemini' ? 'text-[#34705a]' : 'text-[#9a681c]'}`}
                title={llmStatus?.message || 'Checking AI provider status'}
              >
                <span className={`size-1.5 rounded-full ${llmStatus?.mode === 'gemini' ? 'bg-[#3ca276]' : 'bg-[#d89a32]'}`} />
                {statusLabel}
              </span>
            </div>
            <p className="text-[11px] text-[#738079]">
              {statusDetail}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={onOpenUploadModal}
          disabled={!caseId}
          className="flex items-center gap-2 rounded-xl border border-[#dce5df] px-3 py-2 text-xs font-semibold text-[#4d5d55] transition hover:border-[#b9ccc0] hover:bg-[#f5f8f6] disabled:opacity-45"
          aria-label={caseId ? 'Upload a document' : 'Start a case before uploading'}
        >
          <Paperclip className="size-4" aria-hidden="true" />
          <span className="hidden sm:inline">Add evidence</span>
        </button>
      </header>

      <div className="soft-scrollbar flex-1 overflow-y-auto px-4 py-5 sm:px-6">
        {messages.length === 0 ? (
          <div className="mx-auto flex min-h-full max-w-2xl flex-col justify-center py-5">
            <div className="mb-5 flex items-center gap-2 text-xs font-bold uppercase tracking-[0.13em] text-[#2f755b]">
              <Sparkles className="size-4" aria-hidden="true" /> Start in your own words
            </div>
            <h1 className="max-w-xl text-4xl font-bold tracking-[-0.045em] text-[#17231f] sm:text-5xl">What happened?</h1>
            <p className="mt-4 max-w-xl text-sm leading-6 text-[#65736d] sm:text-[15px]">
              Describe your legal problem in your own words. You can write in English, Hindi, or Hinglish.
            </p>
            <div className="mt-7 flex flex-wrap gap-2" aria-label="Example legal problems">
              {suggestions.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  onClick={() => void handleSend(suggestion)}
                  disabled={isSending}
                  className="rounded-full border border-[#d8e2dc] bg-[#fafcfb] px-3.5 py-2 text-left text-xs font-medium text-[#4e5f57] transition hover:border-[#9eb9aa] hover:bg-[#edf5f0] hover:text-[#174e3b]"
                >
                  {suggestion}
                </button>
              ))}
            </div>
            <div className="mt-8 flex items-start gap-2.5 rounded-2xl bg-[#f5f8f6] p-3.5 text-[11px] leading-5 text-[#68766f]">
              <ShieldCheck className="mt-0.5 size-4 shrink-0 text-[#2f755b]" aria-hidden="true" />
              <span>Share only what is needed. Aadhaar, PAN, and card numbers are masked before intake processing.</span>
            </div>
          </div>
        ) : (
          <div className="space-y-5">
            {messages.map((message) => {
              const isUser = message.sender === 'user';
              return (
                <article key={message.id} className={`flex gap-2.5 ${isUser ? 'justify-end' : 'justify-start'}`}>
                  {!isUser && <span className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-xl bg-[#e8f2ec] text-[#174e3b]"><Bot className="size-4" aria-hidden="true" /></span>}
                  <div className={`max-w-[86%] sm:max-w-[78%] ${isUser ? 'items-end' : 'items-start'} flex flex-col gap-2`}>
                    <div className={`rounded-2xl px-4 py-3 text-[13px] leading-6 ${isUser ? 'rounded-tr-md bg-[#174e3b] text-white' : 'rounded-tl-md border border-[#e1e8e4] bg-[#fbfcfb] text-[#34433c]'}`}>
                      {isUser ? <p className="whitespace-pre-wrap">{message.text}</p> : <BotMessageContent text={message.text} />}
                      {message.suggested_action ? (
                        <div className="mt-4 border-t border-[#dce6e0] pt-3">
                          <button
                            type="button"
                            onClick={() => handleDocumentAction(message.suggested_action!)}
                            className="flex w-full items-center justify-between gap-3 rounded-xl bg-[#174e3b] px-3.5 py-2.5 text-left text-xs font-bold text-white transition hover:bg-[#103c2d]"
                          >
                            <span className="flex items-center gap-2"><FileText className="size-4" aria-hidden="true" />{message.suggested_action.label}</span>
                            <span aria-hidden="true">→</span>
                          </button>
                        </div>
                      ) : null}
                    </div>
                    {!isUser && message.quick_replies?.length ? (
                      <div className="flex flex-wrap gap-2">
                        {message.quick_replies.map((reply) => (
                          <button key={reply} type="button" onClick={() => void handleSend(reply)} disabled={isSending} className="rounded-full border border-[#d8e2dc] bg-white px-3 py-1.5 text-[11px] font-semibold text-[#526159] transition hover:border-[#9eb9aa] hover:bg-[#eef5f1] hover:text-[#174e3b] disabled:opacity-50">
                            {reply}
                          </button>
                        ))}
                      </div>
                    ) : null}
                  </div>
                  {isUser && <span className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-xl bg-[#eef1ef] text-[#5b6962]"><UserRound className="size-4" aria-hidden="true" /></span>}
                </article>
              );
            })}
            {isSending ? (
              <div className="flex items-center gap-2.5 text-xs text-[#6f7c75]" role="status">
                <span className="grid size-8 place-items-center rounded-xl bg-[#e8f2ec] text-[#174e3b]"><LoaderCircle className="size-4 animate-spin" aria-hidden="true" /></span>
                <span>Understanding your situation and updating the case…</span>
              </div>
            ) : null}
            <div ref={endRef} />
          </div>
        )}
      </div>

      <form onSubmit={submit} className="border-t border-[#e3e9e5] bg-[#fbfcfb] p-3 sm:p-4">
        <div className="flex items-end gap-2 rounded-2xl border border-[#ccd9d1] bg-white p-2 shadow-sm focus-within:border-[#69917d] focus-within:ring-4 focus-within:ring-[#e8f2ec]">
          <textarea
            ref={inputRef}
            rows={1}
            value={inputText}
            onChange={(event) => setInputText(event.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Describe what happened…"
            aria-label="Describe your legal problem"
            className="max-h-28 min-h-11 flex-1 resize-none bg-transparent px-2.5 py-2 text-sm leading-6 text-[#1d2b25] placeholder:text-[#98a39e] focus:outline-none"
          />
          <button type="submit" disabled={!inputText.trim() || isSending} className="grid size-10 shrink-0 place-items-center rounded-xl bg-[#174e3b] text-white transition hover:bg-[#103c2d] disabled:bg-[#c9d2cd]" aria-label="Send message">
            <ArrowUp className="size-[18px]" aria-hidden="true" />
          </button>
        </div>
        <div className="mt-2 flex items-center justify-between px-1 text-[10px] text-[#87938d]">
          <span>Enter to send · Shift + Enter for a new line</span>
          {sendError ? <span className="text-[#a14d42]">Connection recovered—retry when ready</span> : <span>Information, not representation</span>}
        </div>
      </form>
    </section>
  );
}
