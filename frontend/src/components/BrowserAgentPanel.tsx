"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE_URL } from "@/lib/api";

// ── Types ─────────────────────────────────────────────────────────────────────

type SessionStatus =
  | "pending"
  | "running"
  | "paused"
  | "approved"
  | "cancelled"
  | "done"
  | "error";

interface BrowserSession {
  session_id: string;
  case_id: string;
  category: string;
  portal_label: string;
  portal_url: string;
  status: SessionStatus;
  message: string;
  screenshot_b64: string | null;
  error: string | null;
  error_code?: string | null;
  pause_kind?: "review" | "sensitive" | null;
  review_fields?: { label: string; status: "filled" | "missing" | "manual" }[];
  steps?: string[];
  cdp_port?: number | null;
  cdp_ready?: boolean;
}

interface Props {
  caseId: string;
  onClose: () => void;
}

// ── Constants ─────────────────────────────────────────────────────────────────

const POLL_INTERVAL_MS = 2000;

const STATUS_LABELS: Record<SessionStatus, string> = {
  pending:   "⏳ Starting…",
  running:   "🤖 Agent is filling the form…",
  paused:    "👀 Your turn — review & interact",
  approved:  "Review approved — submit manually",
  cancelled: "❌ Cancelled",
  done:      "✅ Done!",
  error:     "⚠️ Error",
};

const STATUS_COLORS: Record<SessionStatus, string> = {
  pending:   "#6b7280",
  running:   "#2563eb",
  paused:    "#d97706",
  approved:  "#16a34a",
  cancelled: "#dc2626",
  done:      "#16a34a",
  error:     "#dc2626",
};

function wsBase(): string {
  return API_BASE_URL.replace(/^http/, "ws");
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function BrowserAgentPanel({ caseId, onClose }: Props) {
  const [session, setSession]             = useState<BrowserSession | null>(null);
  const sessionRef = useRef<BrowserSession | null>(null);
  useEffect(() => { sessionRef.current = session; }, [session]);
  const [loading, setLoading]             = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError]                 = useState<string | null>(null);
  const [cdpConnected, setCdpConnected]   = useState(false);
  const [cdpError, setCdpError]           = useState<string | null>(null);
  const [liveMode, setLiveMode]           = useState(true);
  const [hasFrame, setHasFrame]           = useState(false);
  const [liveTimedOut, setLiveTimedOut]   = useState(false);
  const frameSeenRef = useRef(false);

  const pollRef   = useRef<ReturnType<typeof setInterval> | null>(null);
  const wsRef     = useRef<WebSocket | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const cmdIdRef  = useRef(1);
  const dimRef    = useRef({ w: 1280, h: 900 });

  // ── CDP helpers ─────────────────────────────────────────────────────────────

  const nextId = () => cmdIdRef.current++;

  const sendCDP = useCallback((method: string, params?: object) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ id: nextId(), method, params: params ?? {} }));
  }, []);

  const connectCDP = useCallback((sessionId: string) => {
    if (wsRef.current) return;
    const url = `${wsBase()}/browser/cdp/${sessionId}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setCdpConnected(true);
      setCdpError(null);
      ws.send(JSON.stringify({
        id: nextId(),
        method: "Page.startScreencast",
        params: { format: "jpeg", quality: 75, maxWidth: 1280, maxHeight: 900, everyNthFrame: 1 },
      }));
    };

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data as string);
        if (msg.method === "Page.screencastFrame") {
          const { data, metadata, sessionId: frameSessionId } = msg.params;
          if (!frameSeenRef.current && data) {
            frameSeenRef.current = true;
            setHasFrame(true);
            setLiveTimedOut(false);
          }
          const w = metadata?.deviceWidth ?? 1280;
          const h = metadata?.deviceHeight ?? 900;
          const canvas = canvasRef.current;
          if (canvas) {
            if (canvas.width !== w || canvas.height !== h) {
              canvas.width  = w;
              canvas.height = h;
              dimRef.current = { w, h };
            }
            const ctx = canvas.getContext("2d");
            if (ctx) {
              const img = new Image();
              img.onload = () => ctx.drawImage(img, 0, 0);
              img.src = `data:image/jpeg;base64,${data}`;
            }
          }
          ws.send(JSON.stringify({
            id: nextId(),
            method: "Page.screencastFrameAck",
            params: { sessionId: frameSessionId },
          }));
        }
      } catch { /* ignore non-JSON */ }
    };

    ws.onerror = () => {
      setCdpConnected(false);
      setCdpError("Live view unavailable — showing screenshot instead.");
    };

    ws.onclose = () => {
      setCdpConnected(false);
      frameSeenRef.current = false;
      setHasFrame(false);
      wsRef.current = null;
    };
  }, []);

  // ── Mouse & keyboard forwarding ─────────────────────────────────────────────

  const forwardMouse = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>, type: "mousePressed" | "mouseReleased" | "mouseMoved") => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const rect   = canvas.getBoundingClientRect();
      const scaleX = dimRef.current.w / rect.width;
      const scaleY = dimRef.current.h / rect.height;
      sendCDP("Input.dispatchMouseEvent", {
        type,
        x:          Math.round((e.clientX - rect.left) * scaleX),
        y:          Math.round((e.clientY - rect.top)  * scaleY),
        button:     type === "mouseMoved" ? "none" : "left",
        clickCount: type === "mousePressed" ? 1 : 0,
        modifiers:  0,
      });
    },
    [sendCDP]
  );

  const forwardKey = useCallback(
    (e: React.KeyboardEvent<HTMLCanvasElement>) => {
      e.preventDefault();
      const mods =
        (e.shiftKey ? 8 : 0) |
        (e.ctrlKey  ? 2 : 0) |
        (e.altKey   ? 1 : 0) |
        (e.metaKey  ? 4 : 0);
      sendCDP("Input.dispatchKeyEvent", {
        type: "keyDown", key: e.key, code: e.code, text: "", modifiers: mods,
      });
      if (e.key.length === 1) {
        sendCDP("Input.dispatchKeyEvent", {
          type: "char", key: e.key, text: e.key, modifiers: mods,
        });
      }
      sendCDP("Input.dispatchKeyEvent", {
        type: "keyUp", key: e.key, code: e.code, modifiers: mods,
      });
    },
    [sendCDP]
  );

  const handleScroll = useCallback((direction: "up" | "down") => {
    sendCDP("Input.dispatchMouseEvent", {
      type: "mouseWheel",
      x:    Math.round(dimRef.current.w / 2),
      y:    Math.round(dimRef.current.h / 2),
      deltaX: 0,
      deltaY: direction === "down" ? 400 : -400,
    });
  }, [sendCDP]);

  // ── API helpers ─────────────────────────────────────────────────────────────

  const apiFetch = useCallback(
    async (path: string, method = "GET"): Promise<BrowserSession | null> => {
      const res = await fetch(`${API_BASE_URL}/browser${path}`, {
        method, credentials: "include",
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        const detail = body.detail;
        throw new Error(
          typeof detail === "string" ? detail
          : typeof detail?.message === "string" ? detail.message
          : `Auto-Fill request failed (HTTP ${res.status}).`
        );
      }
      return res.json();
    },
    []
  );

  // ── Polling ─────────────────────────────────────────────────────────────────

  const startPolling = useCallback(
    (sessionId: string) => {
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        try {
          const data = await apiFetch(`/status/${sessionId}`);
          if (data) {
            setSession(data);
            if (data.cdp_ready && !wsRef.current) connectCDP(sessionId);
            if (["done", "cancelled", "error"].includes(data.status)) {
              clearInterval(pollRef.current!);
              pollRef.current = null;
            }
          }
        } catch (err) {
          setError(err instanceof Error ? err.message : "Could not check the browser session.");
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
        }
      }, POLL_INTERVAL_MS);
    },
    [apiFetch, connectCDP]
  );

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      if (wsRef.current)   wsRef.current.close();
      const active = sessionRef.current;
      if (active && !["done", "cancelled", "error"].includes(active.status)) {
        void fetch(`${API_BASE_URL}/browser/cancel/${active.session_id}`, {
          method: "POST", credentials: "include", keepalive: true,
        }).catch(() => {});
      }
    };
  }, []);

  // ── Session actions ─────────────────────────────────────────────────────────

  const handleStart = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiFetch(`/start/${caseId}`, "POST");
      if (data) { setSession(data); startPolling(data.session_id); }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to start session");
    } finally { setLoading(false); }
  }, [caseId, apiFetch, startPolling]);

  const handleApprove = useCallback(async () => {
    if (!session) return;
    setActionLoading(true);
    try { await apiFetch(`/approve/${session.session_id}`, "POST"); }
    catch (e: unknown) { setError(e instanceof Error ? e.message : "Failed to approve"); }
    finally { setActionLoading(false); }
  }, [session, apiFetch]);

  const handleCancel = useCallback(async () => {
    if (!session) return;
    setActionLoading(true);
    try { await apiFetch(`/cancel/${session.session_id}`, "POST"); }
    catch (e: unknown) { setError(e instanceof Error ? e.message : "Failed to cancel"); }
    finally { setActionLoading(false); }
  }, [session, apiFetch]);

  const handleClose = useCallback(() => {
    const active = sessionRef.current;
    if (active && !["done", "cancelled", "error"].includes(active.status)) {
      void fetch(`${API_BASE_URL}/browser/cancel/${active.session_id}`, {
        method: "POST", credentials: "include", keepalive: true,
      }).catch(() => {});
    }
    onClose();
  }, [onClose]);

  // ── Derived state ───────────────────────────────────────────────────────────

  const isTerminal  = session && ["done", "cancelled", "error"].includes(session.status);
  const isPaused    = session?.status === "paused";
  const isRunning   = session?.status === "running";
  const isActive    = session && !isTerminal;
  const canInteract = (isPaused || session?.status === "approved") && cdpConnected;
  const liveShowing = cdpConnected && hasFrame;

  // No frame for a while: stop implying a live view and point to the new-tab link.
  useEffect(() => {
    if (!isActive || hasFrame) return;
    const timer = setTimeout(() => setLiveTimedOut(true), 20000);
    return () => clearTimeout(timer);
  }, [isActive, hasFrame]);

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <div style={styles.overlay}>
      <div style={styles.panel}>

        {/* Header */}
        <div style={styles.header}>
          <div style={styles.headerLeft}>
            <span style={styles.headerIcon}>🌐</span>
            <div>
              <div style={styles.headerTitle}>Auto-Fill Portal</div>
              {session && <div style={styles.headerSubtitle}>{session.portal_label}</div>}
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
            {session && (
              <button
                style={{
                  ...styles.toggleBtn,
                  background:  liveMode ? "rgba(59,130,246,0.2)" : "rgba(255,255,255,0.06)",
                  borderColor: liveMode ? "#3b82f6" : "#334155",
                  color:       liveMode ? "#60a5fa" : "#94a3b8",
                }}
                onClick={() => setLiveMode(m => !m)}
              >
                {liveMode ? "🎥 Live" : "📸 Screenshot"}
              </button>
            )}
            <button style={styles.closeBtn} onClick={handleClose} id="browser-panel-close">✕</button>
          </div>
        </div>

        {/* Body */}
        <div style={styles.body}>

          {/* Not started */}
          {!session && (
            <div style={styles.startScreen}>
              <div style={styles.startIcon}>🤖</div>
              <div style={styles.startTitle}>Fill the govt form automatically</div>
              <div style={styles.startDesc}>
                NyayaBot opens the correct government portal, pre-fills your case details,
                then <strong style={{ color: "#f1f5f9" }}>hands the live browser over to you</strong> — right
                here inside this panel. Click, type, scroll. Sensitive fields (Aadhaar, OTP)
                are always yours to fill directly.
              </div>
              <button
                id="browser-agent-start-btn"
                style={styles.startBtn}
                onClick={handleStart}
                disabled={loading}
              >
                {loading ? "Starting…" : "🚀 Start Auto-Fill"}
              </button>
              {error && <div style={styles.errorMsg}>{error}</div>}
            </div>
          )}

          {/* Active / terminal session */}
          {session && (
            <>
              {/* Status bar */}
              <div
                style={{
                  ...styles.statusBar,
                  borderColor: STATUS_COLORS[session.status],
                  color:       STATUS_COLORS[session.status],
                }}
              >
                <span style={styles.statusDot}>●</span>
                {STATUS_LABELS[session.status]}
                {liveShowing && (
                  <span style={{ marginLeft: "auto", fontSize: "11px", color: "#22d3ee", fontWeight: 700 }}>
                    ⚡ Live
                  </span>
                )}
              </div>

              {/* Message */}
              <div style={styles.message}>{session.message}</div>

              {/* ── Browser view ── */}
              <div style={styles.browserWrap}>

                {/* Live CDP canvas */}
                {liveMode && (
                  <div style={{ position: "relative" }}>
                    {canInteract && (
                      <div style={styles.interactBanner}>
                        🖱 Click &nbsp;·&nbsp; ⌨ Type &nbsp;·&nbsp; Scroll ↕ — <strong>you are in full control</strong>
                      </div>
                    )}
                    {isRunning && cdpConnected && (
                      <div style={{ ...styles.interactBanner, background: "rgba(37,99,235,0.85)" }}>
                        🤖 Agent is filling the form — live view only (read-only)
                      </div>
                    )}
                    <canvas
                      ref={canvasRef}
                      style={{
                        ...styles.canvas,
                        cursor:  canInteract ? "crosshair" : "default",
                        opacity: liveShowing ? 1 : 0.35,
                      }}
                      tabIndex={canInteract ? 0 : -1}
                      onMouseDown={canInteract ? (e) => forwardMouse(e, "mousePressed")  : undefined}
                      onMouseUp  ={canInteract ? (e) => forwardMouse(e, "mouseReleased") : undefined}
                      onMouseMove={canInteract ? (e) => forwardMouse(e, "mouseMoved")    : undefined}
                      onKeyDown  ={canInteract ? forwardKey : undefined}
                    />
                    {!liveShowing && isActive && (
                      <div style={styles.canvasPlaceholder}>
                        {cdpError || liveTimedOut
                          ? <span style={{ color: "#fca5a5" }}>
                              Live preview unavailable. Open the government portal in a new tab to continue.
                            </span>
                          : <><span style={{ fontSize: "2rem" }}>⏳</span><span>Loading live browser view…</span></>
                        }
                      </div>
                    )}
                  </div>
                )}

                {/* Screenshot fallback */}
                {!liveMode && session.screenshot_b64 && (
                  <div style={styles.screenshotWrap}>
                    <div style={styles.screenshotLabel}>
                      📸 Last screenshot
                      {isPaused && (
                        <span style={styles.screenshotHint}> — switch to 🎥 Live to interact</span>
                      )}
                    </div>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={`data:image/png;base64,${session.screenshot_b64}`}
                      alt="Browser screenshot"
                      style={styles.screenshot}
                    />
                  </div>
                )}

                {/* No view yet */}
                {isActive && !session.screenshot_b64 && !cdpConnected && !liveMode && (
                  <div style={styles.canvasPlaceholder}>
                    {session.cdp_ready ? "Browser ready." : "Starting browser…"}
                    <span style={{ color: "#64748b" }}>Switch to Live to see the browser when ready.</span>
                  </div>
                )}
              </div>

              {/* Scroll controls */}
              {canInteract && (
                <div style={styles.scrollControls}>
                  <button style={styles.scrollBtn} onClick={() => handleScroll("up")}>↑ Scroll Up</button>
                  <button style={styles.scrollBtn} onClick={() => handleScroll("down")}>↓ Scroll Down</button>
                </div>
              )}

              {/* Steps log */}
              {session.review_fields && session.review_fields.length > 0 && (
                <div style={styles.stepsLog}>
                  <div style={styles.stepsTitle}>Form review (values stay in the browser)</div>
                  {session.review_fields.map((field, index) => (
                    <div key={`${field.label}-${index}`} style={{ padding: "2px 0" }}>
                      {field.label}: {field.status === "manual" ? "manual input required" : field.status}
                    </div>
                  ))}
                </div>
              )}
              {session.steps && session.steps.length > 0 && (
                <div style={styles.stepsLog}>
                  <div style={styles.stepsTitle}>📋 Agent Activity Log</div>
                  {session.steps.map((st, i) => (
                    <div key={i} style={{ padding: "2px 0" }}>• {st}</div>
                  ))}
                </div>
              )}

              {/* Portal link */}
              <div style={{ textAlign: "right" }}>
                <a href={session.portal_url} target="_blank" rel="noopener noreferrer" style={styles.portalLink}>
                  🔗 Open {session.portal_label} in new tab ↗
                </a>
              </div>

              {session.error && <div style={styles.errorMsg}>{session.error}</div>}

              {/* Action buttons */}
              <div style={styles.actions}>
                {isPaused && (
                  <>
                    <button
                      id="browser-agent-approve-btn"
                      style={styles.approveBtn}
                      onClick={handleApprove}
                      disabled={actionLoading}
                    >
                      {actionLoading ? "…" : session.pause_kind === "sensitive" ? "Continue after manual step" : "Approve review"}
                    </button>
                    <button
                      id="browser-agent-cancel-btn"
                      style={styles.cancelBtn}
                      onClick={handleCancel}
                      disabled={actionLoading}
                    >
                      ❌ Cancel
                    </button>
                  </>
                )}
                {isActive && isRunning && (
                  <button
                    id="browser-agent-stop-btn"
                    style={styles.cancelBtn}
                    onClick={handleCancel}
                    disabled={actionLoading}
                  >
                    ⛔ Stop Agent
                  </button>
                )}
                {isTerminal && (
                  <button id="browser-panel-done-btn" style={styles.startBtn} onClick={handleClose}>
                    Close
                  </button>
                )}
              </div>

              {error && <div style={styles.errorMsg}>{error}</div>}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const styles: Record<string, React.CSSProperties> = {
  overlay: {
    position: "fixed", inset: 0, background: "rgba(0,0,0,0.65)",
    backdropFilter: "blur(6px)", zIndex: 1000,
    display: "flex", alignItems: "center", justifyContent: "center", padding: "1rem",
  },
  panel: {
    background: "#0f172a", border: "1px solid #1e293b", borderRadius: "1rem",
    width: "100%", maxWidth: "920px", maxHeight: "92vh",
    display: "flex", flexDirection: "column", overflow: "hidden",
    boxShadow: "0 30px 70px rgba(0,0,0,0.75)",
  },
  header: {
    display: "flex", alignItems: "center", justifyContent: "space-between",
    padding: "0.875rem 1.25rem", borderBottom: "1px solid #1e293b", background: "#0d1526",
  },
  headerLeft: { display: "flex", alignItems: "center", gap: "0.75rem" },
  headerIcon: { fontSize: "1.4rem" },
  headerTitle: { fontWeight: 700, fontSize: "1rem", color: "#f1f5f9", fontFamily: "Inter, sans-serif" },
  headerSubtitle: { fontSize: "0.72rem", color: "#64748b", marginTop: "2px" },
  closeBtn: {
    background: "none", border: "none", color: "#64748b",
    fontSize: "1.1rem", cursor: "pointer", padding: "0.25rem 0.5rem", borderRadius: "0.25rem",
  },
  toggleBtn: {
    border: "1px solid", borderRadius: "0.375rem",
    padding: "0.3rem 0.7rem", fontSize: "0.75rem", fontWeight: 600, cursor: "pointer",
  },
  body: {
    flex: 1, overflowY: "auto", padding: "1rem 1.25rem",
    display: "flex", flexDirection: "column", gap: "0.75rem",
  },
  startScreen: {
    display: "flex", flexDirection: "column", alignItems: "center",
    gap: "1rem", padding: "2rem 1rem", textAlign: "center",
  },
  startIcon: { fontSize: "3rem" },
  startTitle: { fontSize: "1.2rem", fontWeight: 700, color: "#f1f5f9", fontFamily: "Inter, sans-serif" },
  startDesc: { fontSize: "0.875rem", color: "#94a3b8", lineHeight: 1.65, maxWidth: "500px" },
  startBtn: {
    background: "linear-gradient(135deg, #3b82f6, #6366f1)",
    color: "#fff", border: "none", borderRadius: "0.5rem",
    padding: "0.75rem 2rem", fontSize: "0.95rem", fontWeight: 600, cursor: "pointer",
  },
  statusBar: {
    display: "flex", alignItems: "center", gap: "0.5rem",
    border: "1px solid", borderRadius: "0.5rem",
    padding: "0.45rem 0.875rem", fontSize: "0.875rem", fontWeight: 600,
    background: "rgba(255,255,255,0.03)",
  },
  statusDot: { fontSize: "0.55rem" },
  message: {
    fontSize: "0.85rem", color: "#94a3b8", lineHeight: 1.5,
    background: "#1e293b", borderRadius: "0.5rem", padding: "0.65rem 0.875rem",
  },
  browserWrap: {
    borderRadius: "0.75rem", overflow: "hidden",
    border: "1px solid #1e293b", background: "#0a0f1e",
    minHeight: "220px", position: "relative",
  },
  canvas: {
    width: "100%", display: "block", aspectRatio: "16/10",
    objectFit: "contain", outline: "none",
  },
  canvasPlaceholder: {
    position: "absolute", inset: 0,
    display: "flex", alignItems: "center", justifyContent: "center",
    flexDirection: "column", gap: "0.5rem",
    color: "#475569", fontSize: "0.85rem", textAlign: "center", padding: "1rem",
  },
  interactBanner: {
    position: "absolute", top: 0, left: 0, right: 0, zIndex: 10,
    background: "rgba(217,119,6,0.88)", color: "#fff",
    fontSize: "0.75rem", fontWeight: 600, padding: "0.35rem 0.75rem",
    textAlign: "center", backdropFilter: "blur(4px)",
  },
  screenshotWrap: { display: "flex", flexDirection: "column", gap: "0.4rem" },
  screenshotLabel: { fontSize: "0.75rem", color: "#64748b", fontWeight: 600 },
  screenshotHint: { fontWeight: 400, color: "#d97706" },
  screenshot: { width: "100%", borderRadius: "0.5rem", border: "1px solid #1e293b", objectFit: "contain" },
  scrollControls: { display: "flex", gap: "0.5rem" },
  scrollBtn: {
    flex: 1, background: "rgba(255,255,255,0.05)", border: "1px solid #334155",
    borderRadius: "0.4rem", color: "#94a3b8", fontSize: "0.8rem", fontWeight: 600,
    padding: "0.4rem", cursor: "pointer",
  },
  stepsLog: {
    background: "#1e293b", border: "1px solid #334155", borderRadius: "0.5rem",
    padding: "0.65rem 0.875rem", fontSize: "0.7rem", lineHeight: 1.6,
    color: "#94a3b8", maxHeight: "110px", overflowY: "auto",
  },
  stepsTitle: { fontWeight: 700, color: "#f1f5f9", marginBottom: "4px", fontSize: "0.75rem" },
  portalLink: {
    display: "inline-flex", alignItems: "center", gap: "4px",
    fontSize: "0.75rem", color: "#059669", textDecoration: "underline", fontWeight: 600,
  },
  actions: { display: "flex", gap: "0.75rem", flexWrap: "wrap" },
  approveBtn: {
    background: "linear-gradient(135deg, #16a34a, #15803d)",
    color: "#fff", border: "none", borderRadius: "0.5rem",
    padding: "0.625rem 1.5rem", fontSize: "0.9rem", fontWeight: 700, cursor: "pointer", flex: 1,
  },
  cancelBtn: {
    background: "rgba(220,38,38,0.15)", color: "#f87171",
    border: "1px solid rgba(220,38,38,0.3)", borderRadius: "0.5rem",
    padding: "0.625rem 1.25rem", fontSize: "0.9rem", fontWeight: 600, cursor: "pointer",
  },
  errorMsg: {
    background: "rgba(220,38,38,0.1)", border: "1px solid rgba(220,38,38,0.3)",
    borderRadius: "0.5rem", padding: "0.6rem 0.875rem", fontSize: "0.78rem", color: "#fca5a5",
  },
};
