import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, setDemoSessionId } from "./api.js";
import { replayStateAt } from "./demo-replay-state.js";

function clockFromSession(session) {
  const clock = session?.master_clock;
  if (!clock) return { position: 0, epoch: 0, absolute: 0, status: session?.status || "ready" };
  return {
    position: Number(clock.position_s || 0), epoch: Number(clock.epoch || 0),
    absolute: Number(clock.absolute_s || 0), status: clock.status,
  };
}

export function useDemoReplay(demoId) {
  const [session, setSession] = useState(null);
  const [cache, setCache] = useState(null);
  const [clock, setClock] = useState({ position: 0, epoch: 0, absolute: 0, status: "ready" });
  const [error, setError] = useState(null);
  const anchor = useRef({ performanceMs: performance.now(), clock });

  const synchronize = useCallback((nextSession) => {
    if (!nextSession) return;
    const nextClock = clockFromSession(nextSession);
    anchor.current = { performanceMs: performance.now(), clock: nextClock };
    setSession(nextSession);
    setClock(nextClock);
  }, []);

  useEffect(() => {
    if (!demoId) { setSession(null); setCache(null); setError(null); return undefined; }
    let cancelled = false;
    Promise.all([
      api.get(`/demo/sessions/${demoId}`),
      api.get(`/demo/sessions/${demoId}/replay-cache`),
    ]).then(([nextSession, nextCache]) => {
      if (cancelled) return;
      setCache(nextCache);
      synchronize(nextSession);
      setError(null);
    }).catch((nextError) => {
      if (cancelled) return;
      const message = String(nextError?.message || "");
      if (/not found|not active|obsolete|discarded/i.test(message)) {
        setDemoSessionId("");
        setSession(null); setCache(null); setError(null);
      } else setError(nextError);
    });
    return () => { cancelled = true; };
  }, [demoId, synchronize]);

  useEffect(() => {
    if (!session) return undefined;
    let request;
    const tick = (now) => {
      const base = anchor.current.clock;
      let absolute = base.absolute;
      if (base.status === "running") absolute += Math.max(0, now - anchor.current.performanceMs) / 1000;
      const duration = Number(session.duration_s || 1);
      const epoch = Math.floor(absolute / duration);
      setClock({ absolute, epoch, position: absolute - epoch * duration, status: base.status });
      request = requestAnimationFrame(tick);
    };
    request = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(request);
  }, [session?.id, session?.duration_s]);

  const replay = useMemo(() => replayStateAt(cache, clock.position, clock.epoch), [cache, clock.position, clock.epoch]);
  return { session, cache, clock, replay, error, synchronize };
}
