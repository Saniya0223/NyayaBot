'use client';

import { useEffect, useRef, useState } from 'react';
import Image from 'next/image';
import { Mic, Square, VolumeX, X } from 'lucide-react';
import { ApiError, fetchSaritaSpeech, transcribeVoiceRecording } from '@/lib/api';
import styles from './VoiceChatOrb.module.css';

type RecognitionResult = { isFinal: boolean; 0: { transcript: string } };
type RecognitionEvent = { resultIndex: number; results: ArrayLike<RecognitionResult> };
type RecognitionError = { error: string };
type BrowserRecognition = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: RecognitionEvent) => void) | null;
  onerror: ((event: RecognitionError) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
};
type RecognitionConstructor = new () => BrowserRecognition;

interface VoiceChatOrbProps {
  isOpen: boolean;
  isSending: boolean;
  caseId?: string;
  draftText: string;
  onOpenChange: (open: boolean) => void;
  onTranscriptChange: (text: string) => void;
  onTranscriptReady: (text: string, autoSend: boolean) => void;
  onBusyChange: (busy: boolean) => void;
  responseToSpeak: { id: string; messageId: string; text: string } | null;
  playbackCommand: { id: number; action: 'pause' | 'resume' | 'stop' } | null;
  onPlaybackChange: (messageId: string | null, status: 'idle' | 'preparing' | 'playing' | 'paused', error?: string, provider?: 'sarita' | 'browser') => void;
}

function recognitionConstructor(): RecognitionConstructor | undefined {
  const browser = window as Window & {
    SpeechRecognition?: RecognitionConstructor;
    webkitSpeechRecognition?: RecognitionConstructor;
  };
  return browser.SpeechRecognition ?? browser.webkitSpeechRecognition;
}

function readableText(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, ' Code example omitted. ')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '$1')
    .replace(/https?:\/\/\S+/g, ' link ')
    .replace(/^[|#>*\-\s]+/gm, '')
    .replace(/\*\*?|__?|`/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

function speechChunks(text: string): string[] {
  const chunks: string[] = [];
  let current = '';
  for (const word of text.split(/\s+/)) {
    if (current && `${current} ${word}`.length > 180) {
      chunks.push(current);
      current = word;
    } else {
      current = current ? `${current} ${word}` : word;
    }
  }
  if (current) chunks.push(current);
  return chunks;
}

export default function VoiceChatOrb({ isOpen, isSending, caseId, draftText, onOpenChange, onTranscriptChange, onTranscriptReady, onBusyChange, responseToSpeak, playbackCommand, onPlaybackChange }: VoiceChatOrbProps) {
  const [supported] = useState(() => typeof window !== 'undefined' && typeof navigator.mediaDevices?.getUserMedia === 'function' && typeof window.MediaRecorder === 'function');
  const [canSpeak] = useState(() => typeof window !== 'undefined' && (typeof Audio !== 'undefined' || 'speechSynthesis' in window));
  const [language, setLanguage] = useState<'auto' | 'hi'>('auto');
  const [status, setStatus] = useState<'idle' | 'requesting' | 'listening' | 'transcribing' | 'preparing' | 'speaking' | 'paused'>('idle');
  const [error, setError] = useState<string | null>(null);
  const [microphoneLevel, setMicrophoneLevel] = useState(0);
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const [conversationTranscript, setConversationTranscript] = useState('');
  const recognitionRef = useRef<BrowserRecognition | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const meterTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const recordingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const sessionRef = useRef(0);
  const transcriptionRef = useRef<AbortController | null>(null);
  const speechRef = useRef<SpeechSynthesisUtterance | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const audioUrlRef = useRef<string | null>(null);
  const speechRequestRef = useRef<AbortController | null>(null);
  const saritaUnavailableRef = useRef(false);
  const spokenResponseRef = useRef<string | null>(null);
  const handledCommandRef = useRef<number | null>(null);
  const playbackSessionRef = useRef(0);
  const activeMessageRef = useRef<string | null>(null);
  const playbackProviderRef = useRef<'sarita' | 'browser' | null>(null);
  const statusRef = useRef<'idle' | 'requesting' | 'listening' | 'transcribing' | 'preparing' | 'speaking' | 'paused'>('idle');
  const transcriptRef = useRef('');
  const interimRef = useRef('');
  const draftAtStartRef = useRef('');
  const conversationRef = useRef(false);
  const recordingModeRef = useRef<'dictation' | 'conversation'>('dictation');
  const heardVoiceRef = useRef(false);
  const lastVoiceAtRef = useRef(0);

  function publishTranscript() {
    const spoken = `${transcriptRef.current} ${interimRef.current}`.trim();
    if (recordingModeRef.current === 'conversation') setConversationTranscript(spoken);
    else onTranscriptChange([draftAtStartRef.current, spoken].filter(Boolean).join(' '));
  }

  function completeTranscript() {
    const spoken = transcriptRef.current.trim();
    updateStatus('idle');
    if (!spoken) return;
    if (recordingModeRef.current === 'conversation') {
      setConversationTranscript('');
      onTranscriptReady(spoken, true);
    } else {
      onTranscriptReady([draftAtStartRef.current, spoken].filter(Boolean).join(' '), false);
    }
  }

  function updateStatus(next: 'idle' | 'requesting' | 'listening' | 'transcribing' | 'preparing' | 'speaking' | 'paused') {
    statusRef.current = next;
    setStatus(next);
    onBusyChange(next === 'requesting' || next === 'listening' || next === 'transcribing');
  }

  function stopSpeaking() {
    playbackSessionRef.current += 1;
    speechRequestRef.current?.abort();
    speechRequestRef.current = null;
    if (audioRef.current) {
      audioRef.current.onended = null;
      audioRef.current.onerror = null;
      audioRef.current.pause();
      audioRef.current = null;
    }
    if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current);
    audioUrlRef.current = null;
    if (typeof window !== 'undefined' && 'speechSynthesis' in window) window.speechSynthesis.cancel();
    speechRef.current = null;
    activeMessageRef.current = null;
    playbackProviderRef.current = null;
    if (statusRef.current === 'preparing' || statusRef.current === 'speaking' || statusRef.current === 'paused') updateStatus('idle');
    onPlaybackChange(null, 'idle');
  }

  function finishSpeaking() {
    stopSpeaking();
    if (conversationRef.current) {
      window.setTimeout(() => {
        if (conversationRef.current && statusRef.current === 'idle') void startListening('conversation');
      }, 300);
    }
  }

  function pauseSpeaking() {
    if (statusRef.current !== 'speaking' || !activeMessageRef.current) return;
    if (audioRef.current) audioRef.current.pause();
    else window.speechSynthesis.pause();
    updateStatus('paused');
    onPlaybackChange(activeMessageRef.current, 'paused', undefined, playbackProviderRef.current ?? undefined);
  }

  function resumeSpeaking() {
    if (statusRef.current !== 'paused' || !activeMessageRef.current) return;
    if (audioRef.current) {
      void audioRef.current.play().catch(() => {
        const error = 'Playback was blocked. Tap Resume to try again, or read the text reply.';
        setError(error);
        updateStatus('paused');
        onPlaybackChange(activeMessageRef.current, 'paused', error, 'sarita');
      });
    } else window.speechSynthesis.resume();
    updateStatus('speaking');
    onPlaybackChange(activeMessageRef.current, 'playing', undefined, playbackProviderRef.current ?? undefined);
  }

  function releaseMicrophone() {
    if (meterTimerRef.current) clearInterval(meterTimerRef.current);
    if (recordingTimerRef.current) clearInterval(recordingTimerRef.current);
    meterTimerRef.current = null;
    recordingTimerRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (audioContextRef.current) void audioContextRef.current.close();
    audioContextRef.current = null;
    setMicrophoneLevel(0);
  }

  function cancelRecording() {
    sessionRef.current += 1;
    transcriptionRef.current?.abort();
    transcriptionRef.current = null;
    recognitionRef.current?.abort();
    recognitionRef.current = null;
    if (recorderRef.current?.state === 'recording') {
      recorderRef.current.onstop = null;
      recorderRef.current.stop();
    }
    recorderRef.current = null;
    releaseMicrophone();
    updateStatus('idle');
  }

  function stopListening() {
    if (recorderRef.current?.state !== 'recording') return;
    updateStatus('transcribing');
    recognitionRef.current?.stop();
    recorderRef.current.stop();
  }

  function speakWithBrowser(text: string, messageId: string, session: number) {
    if (!('speechSynthesis' in window)) {
      const error = 'Sarita is unavailable, and this browser has no voice playback. The text reply remains in chat.';
      setError(error);
      activeMessageRef.current = null;
      updateStatus('idle');
      onPlaybackChange(messageId, 'idle', error);
      return;
    }
    playbackProviderRef.current = 'browser';
    const spokenLanguage = /[\u0900-\u097f]/.test(text) ? 'hi-IN' : 'en-IN';
    const chunks = speechChunks(text);
    let index = 0;
    const playNext = () => {
      if (playbackSessionRef.current !== session) return;
      if (index >= chunks.length) {
        finishSpeaking();
        return;
      }
      const utterance = new SpeechSynthesisUtterance(chunks[index++]);
      utterance.lang = spokenLanguage;
      utterance.rate = 0.95;
      // Browsers expose no gender field; prefer known female voices by name.
      // Refresh for each chunk because some browsers load their voices lazily.
      const voices = window.speechSynthesis.getVoices();
      const language = spokenLanguage.toLowerCase();
      const sameLanguage = voices.filter((voice) => voice.lang.toLowerCase().split('-')[0] === language.split('-')[0]);
      const isFemale = (voice: SpeechSynthesisVoice) =>
        /\b(female|heera|kalpana|swara|neerja|zira|hazel|susan|samantha|victoria|karen|moira|tessa|veena)\b/i.test(voice.name);
      const matchingVoice = sameLanguage.find((voice) => voice.lang.toLowerCase() === language && isFemale(voice))
        ?? sameLanguage.find(isFemale)
        ?? sameLanguage.find((voice) => voice.lang.toLowerCase() === language)
        ?? sameLanguage[0];
      if (matchingVoice) utterance.voice = matchingVoice;
      utterance.onend = () => { if (playbackSessionRef.current === session && speechRef.current === utterance) playNext(); };
      utterance.onerror = () => {
        if (playbackSessionRef.current !== session || speechRef.current !== utterance) return;
        speechRef.current = null;
        activeMessageRef.current = null;
        updateStatus('idle');
        const error = 'Voice playback stopped. The text reply is still available in chat.';
        setError(error);
        playbackProviderRef.current = null;
        onPlaybackChange(messageId, 'idle', error);
      };
      speechRef.current = utterance;
      try {
        window.speechSynthesis.speak(utterance);
      } catch {
        utterance.onerror?.(new Event('error') as SpeechSynthesisErrorEvent);
      }
    };
    setError(null);
    updateStatus('speaking');
    onPlaybackChange(messageId, 'playing', undefined, 'browser');
    playNext();
  }

  async function speak(text: string, messageId: string) {
    stopSpeaking();
    const clean = readableText(text);
    if (!clean) return;
    const session = ++playbackSessionRef.current;
    activeMessageRef.current = messageId;
    if (!caseId || saritaUnavailableRef.current) {
      speakWithBrowser(clean, messageId, session);
      return;
    }
    const controller = new AbortController();
    speechRequestRef.current = controller;
    updateStatus('preparing');
    onPlaybackChange(messageId, 'preparing');
    try {
      const blob = await fetchSaritaSpeech(caseId, messageId, controller.signal);
      if (playbackSessionRef.current !== session) return;
      if (!blob.size) throw new Error('Empty speech audio');
      const url = URL.createObjectURL(blob);
      audioUrlRef.current = url;
      const audio = new Audio(url);
      audioRef.current = audio;
      playbackProviderRef.current = 'sarita';
      audio.onended = () => { if (playbackSessionRef.current === session) finishSpeaking(); };
      audio.onerror = () => {
        if (playbackSessionRef.current !== session) return;
        audioRef.current = null;
        URL.revokeObjectURL(url);
        audioUrlRef.current = null;
        speakWithBrowser(clean, messageId, session);
      };
      try {
        await audio.play();
        if (playbackSessionRef.current !== session) return;
        setError(null);
        updateStatus('speaking');
        onPlaybackChange(messageId, 'playing', undefined, 'sarita');
      } catch {
        if (playbackSessionRef.current !== session) return;
        const error = 'Your browser blocked automatic audio. Tap Resume to play Sarita.';
        setError(error);
        updateStatus('paused');
        onPlaybackChange(messageId, 'paused', error, 'sarita');
      }
    } catch (error) {
      if (playbackSessionRef.current !== session || controller.signal.aborted) return;
      if (error instanceof ApiError && error.status === 503 && /not configured/i.test(error.message)) {
        saritaUnavailableRef.current = true;
      }
      if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current);
      audioUrlRef.current = null;
      audioRef.current = null;
      speakWithBrowser(clean, messageId, session);
    } finally {
      if (speechRequestRef.current === controller) speechRequestRef.current = null;
    }
  }

  useEffect(() => {
    return () => {
      recognitionRef.current?.abort();
      recognitionRef.current = null;
      transcriptionRef.current?.abort();
      speechRequestRef.current?.abort();
      if (audioRef.current) {
        audioRef.current.onended = null;
        audioRef.current.onerror = null;
        audioRef.current.pause();
      }
      if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current);
      if (recorderRef.current?.state === 'recording') {
        recorderRef.current.onstop = null;
        recorderRef.current.stop();
      }
      streamRef.current?.getTracks().forEach((track) => track.stop());
      if (meterTimerRef.current) clearInterval(meterTimerRef.current);
      if (recordingTimerRef.current) clearInterval(recordingTimerRef.current);
      if (audioContextRef.current) void audioContextRef.current.close();
      window.speechSynthesis?.cancel();
    };
  }, []);

  useEffect(() => {
    if (!responseToSpeak || responseToSpeak.id === spokenResponseRef.current) return;
    spokenResponseRef.current = responseToSpeak.id;
    // The response has already been rendered as text by ChatInterface.
    void speak(responseToSpeak.text, responseToSpeak.messageId);
    // Speech is triggered only by a new response, not a language-picker change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [responseToSpeak]);

  useEffect(() => {
    if (!playbackCommand || playbackCommand.id === handledCommandRef.current) return;
    handledCommandRef.current = playbackCommand.id;
    if (playbackCommand.action === 'pause') pauseSpeaking();
    else if (playbackCommand.action === 'resume') resumeSpeaking();
    else stopSpeaking();
    // Playback commands are one-shot controls from the chat message toolbar.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playbackCommand]);

  async function startListening(mode: 'dictation' | 'conversation') {
    if (!supported || statusRef.current !== 'idle') {
      if (!supported) setError('Voice recording needs microphone access in a supported browser. Please type your message instead.');
      return;
    }
    stopSpeaking();
    setError(null);
    recordingModeRef.current = mode;
    draftAtStartRef.current = mode === 'dictation' ? draftText.trim() : '';
    transcriptRef.current = '';
    interimRef.current = '';
    heardVoiceRef.current = false;
    lastVoiceAtRef.current = 0;
    setConversationTranscript('');
    setRecordingSeconds(0);
    const session = ++sessionRef.current;
    updateStatus('requesting');
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch (error) {
      if (sessionRef.current !== session) return;
      const name = error instanceof DOMException ? error.name : '';
      setError(name === 'NotAllowedError' || name === 'PermissionDeniedError'
        ? 'Microphone access was blocked. Allow it for this site in your browser settings.'
        : name === 'NotFoundError'
          ? 'No microphone was found. Connect or select a microphone and try again.'
          : 'Could not open the microphone. Use localhost or HTTPS, then check browser permissions.');
      updateStatus('idle');
      return;
    }
    if (sessionRef.current !== session) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }
    streamRef.current = stream;
    const mimeType = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4']
      .find((type) => MediaRecorder.isTypeSupported(type));
    if (!mimeType) {
      releaseMicrophone();
      setError('This browser cannot record audio in a supported format. Please type your message.');
      updateStatus('idle');
      return;
    }
    try {
      const recorder = new MediaRecorder(stream, { mimeType });
      const chunks: Blob[] = [];
      recorderRef.current = recorder;
      recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
      recorder.onerror = () => {
        if (sessionRef.current !== session) return;
        cancelRecording();
        setError('The microphone recording stopped unexpectedly. Please try again.');
      };
      recorder.onstop = () => {
        recorderRef.current = null;
        releaseMicrophone();
        if (sessionRef.current !== session) return;
        // Browser recognition may send its final result just after Stop.
        window.setTimeout(async () => {
          if (sessionRef.current !== session) return;
          if (interimRef.current) {
            transcriptRef.current = `${transcriptRef.current} ${interimRef.current}`.trim();
            interimRef.current = '';
            publishTranscript();
          }
          if (transcriptRef.current.trim()) {
            completeTranscript();
            return;
          }
          const audio = new Blob(chunks, { type: recorder.mimeType });
          if (!audio.size) {
            setError('No audio was captured. Check your microphone and try again.');
            updateStatus('idle');
            return;
          }
          const controller = new AbortController();
          transcriptionRef.current = controller;
          try {
            const text = await transcribeVoiceRecording(audio, language, controller.signal);
            if (sessionRef.current === session) {
              transcriptRef.current = `${transcriptRef.current} ${text}`.trim();
              publishTranscript();
              setError(null);
              completeTranscript();
            }
          } catch (error) {
            if (sessionRef.current === session) {
              setError(error instanceof Error ? error.message : 'Could not transcribe the recording. Please try again.');
            }
          } finally {
            if (sessionRef.current === session) {
              transcriptionRef.current = null;
              updateStatus('idle');
            }
          }
        }, 450);
      };
      recorder.start(250);
      const Recognition = recognitionConstructor();
      if (Recognition) {
        const recognition = new Recognition();
        recognition.lang = language === 'hi' ? 'hi-IN' : 'en-IN';
        recognition.continuous = true;
        recognition.interimResults = true;
        recognition.onresult = (event) => {
          if (recognitionRef.current !== recognition) return;
          let finalText = '';
          let interimText = '';
          for (let i = event.resultIndex; i < event.results.length; i += 1) {
            const result = event.results[i];
            if (result.isFinal) finalText += result[0].transcript;
            else interimText += result[0].transcript;
          }
          if (finalText.trim()) {
            transcriptRef.current = `${transcriptRef.current} ${finalText.trim()}`.trim();
          }
          interimRef.current = interimText.trim();
          if (finalText.trim() || interimText.trim()) {
            heardVoiceRef.current = true;
            lastVoiceAtRef.current = Date.now();
          }
          publishTranscript();
        };
        recognition.onerror = () => { if (recognitionRef.current === recognition) recognitionRef.current = null; };
        recognition.onend = () => {
          if (recognitionRef.current !== recognition) return;
          if (interimRef.current) {
            transcriptRef.current = `${transcriptRef.current} ${interimRef.current}`.trim();
            interimRef.current = '';
            publishTranscript();
          }
          recognitionRef.current = null;
        };
        recognitionRef.current = recognition;
        try { recognition.start(); } catch { recognitionRef.current = null; }
      }
      try {
        const context = new AudioContext();
        const source = context.createMediaStreamSource(stream);
        const analyser = context.createAnalyser();
        analyser.fftSize = 512;
        source.connect(analyser);
        audioContextRef.current = context;
        const samples = new Uint8Array(analyser.fftSize);
        meterTimerRef.current = setInterval(() => {
          analyser.getByteTimeDomainData(samples);
          const energy = samples.reduce((sum, value) => sum + ((value - 128) / 128) ** 2, 0) / samples.length;
          const level = Math.min(100, Math.round(Math.sqrt(energy) * 450));
          setMicrophoneLevel(level);
          if (mode === 'conversation' && level > 9) {
            heardVoiceRef.current = true;
            lastVoiceAtRef.current = Date.now();
          }
        }, 120);
      } catch { /* Recording still works if the visual meter is unavailable. */ }
      const startedAt = Date.now();
      recordingTimerRef.current = setInterval(() => {
        const elapsed = Math.floor((Date.now() - startedAt) / 1000);
        setRecordingSeconds(elapsed);
        if (elapsed >= 60 || (mode === 'conversation' && elapsed >= 2 && heardVoiceRef.current && Date.now() - lastVoiceAtRef.current > 1800)) stopListening();
      }, 1000);
      updateStatus('listening');
    } catch {
      cancelRecording();
      setError('Could not start recording. Check your microphone and try again.');
    }
  }

  function close() {
    conversationRef.current = false;
    cancelRecording();
    stopSpeaking();
    setConversationTranscript('');
    onOpenChange(false);
  }

  function toggleConversation() {
    if (isOpen) {
      close();
      return;
    }
    conversationRef.current = true;
    onOpenChange(true);
    if (supported) void startListening('conversation');
    else setError('Voice conversation needs microphone access in a supported browser. Text chat still works.');
  }

  const dictating = !isOpen && recordingModeRef.current === 'dictation'
    && (status === 'requesting' || status === 'listening' || status === 'transcribing');

  return (
    <div className={styles.root}>
      {isOpen && (
        <div className={styles.panel} role="group" aria-label="Voice chat with NyayaBot">
          <button type="button" className={styles.iconButton} onClick={close} aria-label="Close voice chat"><X size={17} /></button>
          <h3 className={styles.panelTitle}>Voice conversation</h3>
          <div className={styles.center}>
            <button
              type="button"
              className={`${styles.orb} ${status === 'listening' ? styles.listening : ''} ${status === 'speaking' ? styles.speaking : ''} ${isSending || status === 'transcribing' || status === 'preparing' ? styles.processing : ''}`}
              onClick={status === 'listening' ? stopListening : status === 'speaking' ? pauseSpeaking : status === 'paused' ? resumeSpeaking : () => { void startListening('conversation'); }}
              disabled={status === 'requesting' || status === 'transcribing' || status === 'preparing' || ((supported === false || isSending) && status !== 'speaking' && status !== 'paused')}
              aria-label={status === 'listening' ? 'Stop listening' : status === 'speaking' ? 'Pause reading' : status === 'paused' ? 'Resume reading' : 'Start speaking'}
              title={status === 'listening' ? 'Stop listening' : status === 'speaking' ? 'Pause reading' : status === 'paused' ? 'Resume reading' : 'Tap to speak'}
            >
              <span className={styles.orbHalo} aria-hidden="true" />
              <Image src="/nyayabot-voice-orb.png" width={240} height={240} alt="" className={styles.mascot} priority={false} />
              {status === 'speaking' && <span className={styles.speechWave} aria-hidden="true"><i /><i /><i /><i /><i /></span>}
            </button>
            <span className={styles.state} role="status">{status === 'requesting' ? 'Waiting for microphone permission…' : status === 'listening' ? `Recording ${recordingSeconds}s · tap to stop` : status === 'transcribing' ? 'Turning your voice into text…' : status === 'preparing' ? 'Preparing Sarita voice…' : status === 'speaking' ? 'Reading aloud · tap to pause' : status === 'paused' ? 'Paused · tap to resume' : isSending ? 'Preparing your reply…' : 'Tap the orb to speak'}</span>
            {conversationTranscript && <p className={styles.transcript} aria-live="polite">{conversationTranscript}</p>}
            {status === 'listening' && <p className={styles.hint}>Pause after speaking to send automatically, or tap the orb to finish.</p>}
            {status === 'listening' && (
              <div className={styles.meterWrap} aria-label={`Microphone input level ${microphoneLevel} percent`}>
                <span className={styles.meterTrack}><span className={styles.meterFill} style={{ width: `${Math.max(2, microphoneLevel)}%` }} /></span>
                <span className={styles.meterCaption}>{microphoneLevel > 5 ? 'Microphone is picking up sound' : 'Input level is low — check your microphone'}</span>
              </div>
            )}
          </div>
          {(status === 'preparing' || status === 'speaking' || status === 'paused') && <button type="button" className={styles.stopAudio} onClick={stopSpeaking}><VolumeX size={14} /> Stop reading</button>}
          <label className={styles.languageLabel}>Speech language
            <select value={language} onChange={(event) => setLanguage(event.target.value as 'auto' | 'hi')} disabled={status === 'listening' || status === 'requesting' || status === 'transcribing'}>
              <option value="auto">English / Hinglish</option>
              <option value="hi">Hindi</option>
            </select>
          </label>
          {error && <p className={styles.error} role="alert">{error}</p>}
          {supported === false && <p className={styles.error}>Voice recording needs a supported browser on localhost or HTTPS. Text chat still works.</p>}
          {!canSpeak && <p className={styles.error}>This browser does not support spoken replies. Text replies still work.</p>}
          <p className={styles.privacy}>Voice conversation sends your completed spoken turn after a pause, then reads NyayaBot&apos;s reply. For Sarita playback, reply text is sent to Shunya Labs; if unavailable, browser voice is used. Audio is not saved to your case.</p>
        </div>
      )}
      {!isOpen && dictating && <span className={styles.dictationStatus} role="status">{status === 'listening' ? `Recording ${recordingSeconds}s - tap mic to stop` : status === 'transcribing' ? 'Transcribing...' : 'Opening microphone...'}</span>}
      {!isOpen && error && <span className={styles.dictationError} role="alert">{error}</span>}
      <div className={styles.controls}>
        <button type="button" className={`${styles.launcher} ${dictating ? styles.launcherOpen : ''}`} onClick={() => status === 'listening' ? stopListening() : void startListening('dictation')} disabled={isOpen || isSending || (status !== 'idle' && status !== 'listening')} aria-label={dictating ? 'Stop voice recording' : 'Record voice message'} title={dictating ? 'Stop recording; review text before sending' : 'Record voice message; review text before sending'}>
          {dictating ? <Square size={16} /> : <Mic size={19} />}
        </button>
        <button type="button" className={`${styles.orbLauncher} ${isOpen ? styles.orbLauncherOpen : ''}`} onClick={toggleConversation} disabled={!isOpen && (isSending || status === 'requesting' || status === 'listening' || status === 'transcribing' || status === 'preparing')} aria-label={isOpen ? 'End voice conversation' : 'Start hands-free voice conversation'} aria-expanded={isOpen} title={isOpen ? 'End voice conversation' : 'Talk back and forth with NyayaBot'}>
          <Image src="/nyayabot-voice-orb.png" width={40} height={40} alt="" />
        </button>
      </div>
    </div>
  );
}
