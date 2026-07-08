import { useEffect, useRef, useCallback } from "react";
import type { SSEEvent } from "./types";

/**
 * Subscribe to the backend SSE event stream.
 * Calls onEvent for each parsed event.
 */
export function useSSE(onEvent: (ev: SSEEvent) => void) {
  const savedCb = useRef(onEvent);
  savedCb.current = onEvent;

  const handleEvent = useCallback((ev: SSEEvent) => {
    savedCb.current(ev);
  }, []);

  useEffect(() => {
    const es = new EventSource("/api/events");
    es.onopen = () => {
      const dot = document.querySelector(".wsdot");
      if (dot) dot.classList.add("on");
    };
    es.onerror = () => {
      const dot = document.querySelector(".wsdot");
      if (dot) dot.classList.remove("on");
    };
    es.onmessage = (ev) => {
      try {
        const m = JSON.parse(ev.data) as SSEEvent;
        handleEvent(m);
      } catch {
        // skip unparseable events
      }
    };
    return () => es.close();
  }, [handleEvent]);
}
