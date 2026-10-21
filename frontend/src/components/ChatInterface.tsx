'use client';

import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from 'react';
import Image from 'next/image';
import {
  ArrowUp,
  FileText,
  LoaderCircle,
  Pause,
  Play,
  Plus,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  Square,
  UserRound,
  Volume2,
} from 'lucide-react';
import { API_BASE_URL, ChatMessageItem, fetchLLMStatus, LLMStatus, retryChatStage, sendChatMessage, StructuredCaseProfile } from '@/lib/api';
import { openEligibleDocumentHandoff, routeDocumentAction } from '@/lib/documentHandoff';
import FormattedMessage from './FormattedMessage';
import VoiceChatOrb from './VoiceChatOrb';

interface ChatInterfaceProps {
  activeProfile?: StructuredCaseProfile | null;
  initialCaseId?: string;
  initialMessages?: ChatMessageItem[];
  onProfileUpdated: (profile: StructuredCaseProfile) => void;
  onOpenUploadModal: () => void;
  onTriggerDocumentModal: (docType: string, docLabel: string) => void;
  onNewCase?: () => void;
  injectedBotMessage?: { text: string; profile: StructuredCaseProfile; quick_replies?: string[] };
}

const suggestions = [
  "My landlord isn't returning my deposit.",
  "My company hasn't paid my salary.",
  'A seller refuses my refund.',
  "Police aren't taking my complaint.",
  'I was cheated through UPI.',
];

export default function ChatInterface({
  activeProfile,
  initialCaseId,
  initialMessages = [],
  onProfileUpdated,
  onOpenUploadModal,
  onTriggerDocumentModal,
  onNewCase,
  injectedBotMessage,
}: ChatInterfaceProps) {
  const [messages, setMessages] = useState<ChatMessageItem[]>(initialMessages);
  const [inputText, setInputText] = useState('');
  const [isSending, setIsSending] = useState(false);
  const [caseId, setCaseId] = useState<string | undefined>(initialCaseId);
  const [sendError, setSendError] = useState<string | null>(null);
  const [llmStatus, setLLMStatus] = useState<LLMStatus | null>(null);
  const [statusUnreachable, setStatusUnreachable] = useState(false);
  const [voiceOpen, setVoiceOpen] = useState(false);
  const [voiceBusy, setVoiceBusy] = useState(false);
  const [voiceSessionKey, setVoiceSessionKey] = useState(0);
  const [responseToSpeak, setResponseToSpeak] = useState<{ id: string; messageId: string; text: string } | null>(null);
  const [playbackCommand, setPlaybackCommand] = useState<{ id: number; action: 'pause' | 'resume' | 'stop' } | null>(null);
  const [playback, setPlayback] = useState<{ messageId: string; status: 'preparing' | 'playing' | 'paused'; provider?: 'sarita' | 'browser' } | null>(null);
  const [playbackError, setPlaybackError] = useState<{ messageId: string; text: string } | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const mountedRef = useRef(true);
  const messageSequence = useRef(0);
  const requestInFlight = useRef(false);
  const voiceOpenRef = useRef(false);
  const voiceBusyRef = useRef(false);
  const speechSequence = useRef(0);
  const playbackCommandSequence = useRef(0);

  function requestSpokenReply(text: string, messageId: string) {
    speechSequence.current += 1;
    setPlaybackError(null);
    setResponseToSpeak({ id: `speech-${speechSequence.current}`, messageId, text });
  }

  function controlPlayback(action: 'pause' | 'resume' | 'stop') {
    playbackCommandSequence.current += 1;
    setPlaybackCommand({ id: playbackCommandSequence.current, action });
  }

  function handlePlaybackChange(messageId: string | null, status: 'idle' | 'preparing' | 'playing' | 'paused', error?: string, provider?: 'sarita' | 'browser') {
    setPlayback(messageId && status !== 'idle' ? { messageId, status, provider } : null);
    if (error && messageId) setPlaybackError({ messageId, text: error });
  }

  function setVoiceModeOpen(open: boolean) {
    voiceOpenRef.current = open;
    setVoiceOpen(open);
    if (!open) setResponseToSpeak(null);
  }

  function setVoiceRecordingBusy(busy: boolean) {
    voiceBusyRef.current = busy;
    setVoiceBusy(busy);
  }

  function handleNewCaseClick() {
    setMessages([]);
    setCaseId(undefined);
    setInputText('');
    setSendError(null);
    setResponseToSpeak(null);
    setPlayback(null);
    setPlaybackError(null);
    setPlaybackCommand(null);
    setVoiceModeOpen(false);
    setVoiceRecordingBusy(false);
    setVoiceSessionKey((current) => current + 1);
    if (onNewCase) onNewCase();
  }

  useEffect(() => {
    mountedRef.current = true;
    void fetchLLMStatus()
      .then((status) => {
        if (mountedRef.current) { setLLMStatus(status); setStatusUnreachable(false); }
      })
      .catch((error: unknown) => {
        // Surface the real reason during development instead of silently
        // fabricating an "unconfigured" state that blames a missing key.
        const detail = error instanceof Error ? error.message : 'Unknown connection error';
        console.warn(`[NyayaBot] LLM status fetch failed against ${API_BASE_URL}: ${detail}`);
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
    if (!text || isSending || requestInFlight.current || voiceBusyRef.current) return;
    requestInFlight.current = true;
    controlPlayback('stop');

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
        configured: response.llm_mode !== 'limited_demo' || Boolean(current?.configured),
        mode: response.llm_mode,
        message: response.llm_mode !== 'limited_demo'
          ? `This response was generated through the backend ${response.llm_provider || 'AI'} API.`
          : 'This response used limited demo workflow rules.',
      }));
      onProfileUpdated(response.case_profile);
      openEligibleDocumentHandoff(
        response.suggested_action,
        response.case_profile.recommended_doc_type,
        onTriggerDocumentModal,
      );
      setMessages((current) => [
        ...current,
        {
          id: response.message_id,
          sender: 'bot',
          text: response.reply_text,
          quick_replies: response.quick_replies,
          suggested_action: response.suggested_action,
          retry_action: response.retry_action,
        },
      ]);
      if (voiceOpenRef.current) {
        requestSpokenReply(response.reply_text, response.message_id);
      }
    } catch (error: unknown) {
      // Log the real, non-sensitive failure so URL/schema/CORS faults are
      // diagnosable instead of hidden behind the generic bubble below.
      const message = error instanceof Error ? error.message : 'Please try again.';
      console.warn(`[NyayaBot] chat request failed against ${API_BASE_URL}/chat/message: ${message}`);
      if (!mountedRef.current) return;
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
      requestInFlight.current = false;
      if (mountedRef.current) setIsSending(false);
    }
  }

  async function handleRetry(message: ChatMessageItem) {
    if (!caseId || !message.retry_action || isSending || requestInFlight.current) return;
    requestInFlight.current = true;
    controlPlayback('stop');
    setSendError(null);
    setIsSending(true);
    try {
      // This is a control action, never a new user message or history entry.
      const response = await retryChatStage({ case_id: caseId, message_id: message.retry_action.message_id });
      if (!mountedRef.current) return;
      onProfileUpdated(response.case_profile);
      setLLMStatus((current) => ({
        provider: response.llm_provider,
        model: response.llm_model || current?.model || 'unknown',
        configured: response.llm_mode !== 'limited_demo' || Boolean(current?.configured),
        mode: response.llm_mode,
        message: response.llm_mode !== 'limited_demo'
          ? `This response was generated through the backend ${response.llm_provider || 'AI'} API.`
          : 'This response used limited demo workflow rules.',
      }));
      openEligibleDocumentHandoff(
        response.suggested_action, response.case_profile.recommended_doc_type, onTriggerDocumentModal,
      );
      setMessages((current) => current.map((item) => item.id === message.id ? {
        id: response.message_id, sender: 'bot', text: response.reply_text,
        quick_replies: response.quick_replies, suggested_action: response.suggested_action,
        retry_action: response.retry_action,
      } : item));
      if (voiceOpenRef.current) {
        requestSpokenReply(response.reply_text, response.message_id);
      }
    } catch (error: unknown) {
      if (mountedRef.current) setSendError(error instanceof Error ? error.message : 'Could not retry this turn.');
    } finally {
      requestInFlight.current = false;
      if (mountedRef.current) setIsSending(false);
    }
  }

  const isLLMActive = llmStatus?.mode === 'groq' || llmStatus?.mode === 'gemini';

  // The backend status endpoint is the only source of truth. A failed fetch means
  // the API is unreachable, which must not be reported as a missing key.
  const statusLabel = statusUnreachable
    ? 'Backend unreachable'
    : llmStatus
      ? isLLMActive
        ? null
        : 'Limited demo'
      : 'Checking';

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
    routeDocumentAction(action, {
      isLLMActive,
      missingDocumentFields: activeProfile?.missing_document_fields,
      recommendedDocType: activeProfile?.recommended_doc_type,
      openModal: onTriggerDocumentModal,
      sendMessage: (message) => { void handleSend(message); },
    });
  }

  return (
    <section className="flex h-full min-h-[620px] flex-col overflow-hidden rounded-[26px] border border-[#ebe6dc] bg-white paper-shadow" aria-label="Conversation with NyayaBot">
      <header className="flex items-center justify-between border-b border-[#eee8dd] px-4 py-3.5 sm:px-5">
        <div className="flex items-center gap-3">
          <Image src="/nyayabot-mascot-avatar-v2.png" alt="NyayaBot mascot" width={44} height={44} className="size-11 shrink-0 rounded-full border border-[#f1dc9e] object-cover" />
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-bold text-[#222222]">Ask NyayaBot</h2>
              {statusLabel ? (
                <span className="flex items-center gap-1 text-[10px] font-semibold text-[#9a681c]" title={llmStatus?.message || 'Checking AI provider status'}>
                  <span className="size-1.5 rounded-full bg-[#d89a32]" />
                  {statusLabel}
                </span>
              ) : null}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={handleNewCaseClick}
            className="flex items-center gap-1.5 rounded-xl bg-gradient-to-r from-[#fff4c4] to-[#ffea97] px-3 py-2 text-xs font-semibold text-[#1b1b1b] transition hover:from-[#ffedaa] hover:to-[#ffdf6d] active:scale-[0.98]"
            title="Start a new case conversation"
            aria-label="Start a new case"
          >
            <Plus className="size-3.5" aria-hidden="true" />
            <span className="hidden sm:inline">New case</span>
          </button>
        </div>
      </header>

      <div className="soft-scrollbar flex-1 overflow-y-auto px-4 py-5 sm:px-6">
        {messages.length === 0 ? (
          <div className="mx-auto flex min-h-full max-w-2xl flex-col justify-center py-5">
            <div className="mb-5 flex items-center gap-2 text-xs font-bold uppercase tracking-[0.13em] text-[#9d6b00]">
              <Sparkles className="size-4" aria-hidden="true" /> Start in your own words
            </div>
            <h1 className="max-w-xl text-4xl font-bold tracking-[-0.045em] text-[#171717] sm:text-5xl">What happened?</h1>
            <p className="mt-4 max-w-xl text-sm leading-6 text-[#716c64] sm:text-[15px]">
              Describe your legal problem in your own words. You can write in English, Hindi, or Hinglish.
            </p>
            <div className="mt-7 flex flex-wrap gap-2" aria-label="Example legal problems">
              {suggestions.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  onClick={() => void handleSend(suggestion)}
                  disabled={isSending}
                  className="rounded-full border border-[#e3dccf] bg-[#fffdf8] px-3.5 py-2 text-left text-xs font-medium text-[#55514a] transition hover:border-[#d9bd6b] hover:bg-[#fff8e2] hover:text-[#181818]"
                >
                  {suggestion}
                </button>
              ))}
            </div>
            <div className="mt-8 flex items-start gap-2.5 rounded-2xl bg-[#fcfaf5] p-3.5 text-[11px] leading-5 text-[#746f67]">
              <ShieldCheck className="mt-0.5 size-4 shrink-0 text-[#9d6b00]" aria-hidden="true" />
              <span>Share only what is needed. Aadhaar, PAN, and card numbers are masked before intake processing.</span>
            </div>
          </div>
        ) : (
          <div className="space-y-5">
            {messages.map((message) => {
              const isUser = message.sender === 'user';
              return (
                <article key={message.id} className={`flex gap-2.5 ${isUser ? 'justify-end' : 'justify-start'}`}>
                  {!isUser && <Image src="/nyayabot-voice-orb.png" alt="" width={36} height={36} className="mt-0.5 size-9 shrink-0 rounded-full border border-[#f1dc9e] object-cover" />}
                  <div className={`max-w-[86%] sm:max-w-[78%] ${isUser ? 'items-end' : 'items-start'} flex flex-col gap-2`}>
                    <div className={`rounded-2xl px-4 py-3 text-[13px] leading-6 ${isUser ? 'rounded-tr-md bg-[#171717] text-white' : 'rounded-tl-md border border-[#eeeae3] bg-[#fdfcf9] text-[#383530]'}`}>
                      {isUser ? <p className="whitespace-pre-wrap">{message.text}</p> : <FormattedMessage text={message.text} />}
                      {message.suggested_action ? (
                        <div className="mt-4 border-t border-[#e9e3d7] pt-3">
                          <button
                            type="button"
                            onClick={() => handleDocumentAction(message.suggested_action!)}
                            className="flex w-full items-center justify-between gap-3 rounded-xl bg-[#181818] px-3.5 py-2.5 text-left text-xs font-bold text-white transition hover:bg-[#080808]"
                          >
                            <span className="flex items-center gap-2"><FileText className="size-4" aria-hidden="true" />{message.suggested_action.label}</span>
                            <span aria-hidden="true">→</span>
                          </button>
                        </div>
                      ) : null}
                    </div>
                    {!isUser && message.quick_replies?.length ? (
                      <div className="flex flex-wrap gap-2">
                        {message.quick_replies.map((reply) => {
                          const retryControl = reply === message.retry_action?.label || /^Try (?:Groq|Gemini) again$/i.test(reply);
                          return (
                            <button
                              key={reply}
                              type="button"
                              onClick={() => { if (retryControl) void handleRetry(message); else void handleSend(reply); }}
                              disabled={isSending || (retryControl && !message.retry_action)}
                              aria-label={retryControl ? 'Retry failed response' : undefined}
                              title={retryControl ? (message.retry_action ? 'Retry failed response' : 'This older response has no saved retry stage. Send the original message again.') : undefined}
                              className="rounded-full border border-[#e3dccf] bg-white px-3 py-1.5 text-[11px] font-semibold text-[#5e5a53] transition hover:border-[#d9bd6b] hover:bg-[#fff7df] hover:text-[#181818] disabled:opacity-50"
                            >
                              {retryControl ? <RotateCcw className="size-4" aria-hidden="true" /> : reply}
                            </button>
                          );
                        })}
                      </div>
                    ) : null}
                    {!isUser && message.text && (
                      <div className="flex flex-wrap items-center gap-1.5">
                        {playback?.messageId === message.id ? (
                          <>
                            {playback.status === 'preparing' ? <span role="status" className="inline-flex items-center gap-1 px-2 py-1 text-[11px] text-[#76684c]"><LoaderCircle className="size-3.5 animate-spin" aria-hidden="true" /> Preparing Sarita…</span> :
                              <button type="button" onClick={() => controlPlayback(playback.status === 'paused' ? 'resume' : 'pause')} className="inline-flex items-center gap-1 rounded-full bg-[#fff4d6] px-2 py-1 text-[11px] font-medium text-[#5b421d] transition hover:bg-[#ffe8a4]" aria-label={playback.status === 'paused' ? 'Resume reading this reply' : 'Pause reading this reply'}>
                                {playback.status === 'paused' ? <Play className="size-3.5" aria-hidden="true" /> : <Pause className="size-3.5" aria-hidden="true" />}
                                {playback.status === 'paused' ? 'Resume' : 'Pause'}
                              </button>}
                            <button type="button" onClick={() => controlPlayback('stop')} className="inline-flex items-center gap-1 rounded-full px-2 py-1 text-[11px] text-[#76684c] transition hover:bg-[#fff4d6]" aria-label="Stop reading this reply"><Square className="size-3" aria-hidden="true" /> Stop</button>
                            {playback.provider && <span className="text-[10px] text-[#8b8170]">{playback.provider === 'sarita' ? 'Sarita' : 'Browser voice'}</span>}
                          </>
                        ) : (
                          <button type="button" onClick={() => requestSpokenReply(message.text, message.id)} className="inline-flex items-center gap-1 rounded-full px-2 py-1 text-[11px] text-[#76684c] transition hover:bg-[#fff4d6] hover:text-[#30250f]" aria-label="Read this reply aloud" title="Uses Sarita when configured (reply text sent to Shunya Labs), otherwise your browser voice"><Volume2 className="size-3.5" aria-hidden="true" /> Read aloud</button>
                        )}
                        {playbackError?.messageId === message.id && <span role="alert" className="text-[11px] text-[#a24335]">{playbackError.text}</span>}
                      </div>
                    )}
                  </div>
                  {isUser && <span className="mt-0.5 grid size-9 shrink-0 place-items-center rounded-full bg-[#fff2bd] text-[#2d291f]"><UserRound className="size-4" aria-hidden="true" /></span>}
                </article>
              );
            })}
            {isSending ? (
              <div className="flex items-center gap-2.5 text-xs text-[#817b72]" role="status">
                <span className="grid size-9 place-items-center rounded-full bg-[#fff2bd] text-[#171717]"><LoaderCircle className="size-4 animate-spin" aria-hidden="true" /></span>
                <span>Understanding your situation and updating the case…</span>
              </div>
            ) : null}
            <div ref={endRef} />
          </div>
        )}
      </div>

      <form onSubmit={submit} className="relative border-t border-[#eee8dd] bg-[#fefcf8] p-3 sm:p-4">
        <div className="flex items-end gap-2 rounded-2xl border border-[#ded7c9] bg-white p-2 shadow-sm focus-within:border-[#c9a34d] focus-within:ring-4 focus-within:ring-[#fff3c6]">
          <button
            type="button"
            onClick={onOpenUploadModal}
            disabled={!caseId}
            className="grid size-10 shrink-0 place-items-center rounded-xl border border-[#e9e3d7] text-[#514b40] transition hover:border-[#dec88e] hover:bg-[#fff5d6] disabled:opacity-45"
            aria-label={caseId ? 'Add evidence' : 'Start a case before adding evidence'}
            title={caseId ? 'Add evidence' : 'Start a case before adding evidence'}
          >
            <Plus className="size-5" aria-hidden="true" />
          </button>
          <textarea
            ref={inputRef}
            rows={1}
            value={inputText}
            onChange={(event) => setInputText(event.target.value)}
            onKeyDown={handleKeyDown}
            readOnly={voiceBusy}
            placeholder="Describe what happened…"
            aria-label="Describe your legal problem"
            className="max-h-28 min-h-11 flex-1 resize-none bg-transparent px-2.5 py-2 text-sm leading-6 text-[#222222] placeholder:text-[#aaa299] focus:outline-none"
          />
          <VoiceChatOrb
            key={voiceSessionKey}
            isOpen={voiceOpen}
            isSending={isSending}
            caseId={caseId}
            draftText={inputText}
            onOpenChange={setVoiceModeOpen}
            onTranscriptChange={setInputText}
            onTranscriptReady={(text, autoSend) => {
              if (autoSend) void handleSend(text);
              else inputRef.current?.focus();
            }}
            onBusyChange={setVoiceRecordingBusy}
            responseToSpeak={responseToSpeak}
            playbackCommand={playbackCommand}
            onPlaybackChange={handlePlaybackChange}
          />
          <button type="submit" disabled={!inputText.trim() || isSending || voiceBusy} className="grid size-10 shrink-0 place-items-center rounded-full bg-[#ffdd5d] text-[#181818] transition hover:bg-[#ffd23a] disabled:bg-[#e9e2d2]" aria-label="Send message">
            <ArrowUp className="size-[18px]" aria-hidden="true" />
          </button>
        </div>
        <div className="mt-2 flex items-center justify-between px-1 text-[10px] text-[#999187]">
          <span>Enter to send · Shift + Enter for a new line</span>
          {sendError ? <span role="alert" className="text-[#a14d42]">{sendError}</span> : <span>Information, not representation</span>}
        </div>
      </form>
    </section>
  );
}
