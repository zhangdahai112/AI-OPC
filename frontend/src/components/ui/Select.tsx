import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

export type SelectOption = {
  value: string;
  label?: ReactNode;
  /** short text used for the trigger + typeahead when label is a node */
  text?: string;
  disabled?: boolean;
};

type Opt = string | SelectOption;

function norm(o: Opt): SelectOption {
  return typeof o === "string" ? { value: o, label: o, text: o } : o;
}

function optText(o: SelectOption): string {
  if (o.text != null) return o.text;
  if (typeof o.label === "string") return o.label;
  return o.value;
}

/**
 * Flat, tech-styled custom dropdown replacing native <select>.
 * Fully keyboard accessible; menu is portaled to <body> so it never clips.
 */
export default function Select({
  value,
  onChange,
  options,
  placeholder = "选择…",
  disabled,
  className,
  style,
  size = "md",
  align = "left",
}: {
  value: string;
  onChange: (v: string) => void;
  options: Opt[];
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  style?: CSSProperties;
  size?: "sm" | "md";
  align?: "left" | "right";
}) {
  const opts = options.map(norm);
  const current = opts.find((o) => o.value === value);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const btnRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [menuStyle, setMenuStyle] = useState<CSSProperties>({});
  const listId = useId();
  const typeahead = useRef({ str: "", t: 0 });

  const place = () => {
    const b = btnRef.current?.getBoundingClientRect();
    if (!b) return;
    const gap = 6;
    const below = window.innerHeight - b.bottom;
    const openUp = below < 240 && b.top > below;
    setMenuStyle({
      position: "fixed",
      left: align === "right" ? undefined : b.left,
      right: align === "right" ? window.innerWidth - b.right : undefined,
      minWidth: b.width,
      ...(openUp
        ? { bottom: window.innerHeight - b.top + gap }
        : { top: b.bottom + gap }),
    });
  };

  useLayoutEffect(() => {
    if (open) place();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (
        !menuRef.current?.contains(e.target as Node) &&
        !btnRef.current?.contains(e.target as Node)
      )
        setOpen(false);
    };
    const onScroll = () => place();
    document.addEventListener("mousedown", onDoc);
    window.addEventListener("resize", onScroll);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      window.removeEventListener("resize", onScroll);
      window.removeEventListener("scroll", onScroll, true);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (open) {
      const i = opts.findIndex((o) => o.value === value);
      setActive(i < 0 ? 0 : i);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const commit = (i: number) => {
    const o = opts[i];
    if (!o || o.disabled) return;
    onChange(o.value);
    setOpen(false);
    btnRef.current?.focus();
  };

  const step = (dir: 1 | -1) => {
    setActive((a) => {
      let n = a;
      for (let k = 0; k < opts.length; k++) {
        n = (n + dir + opts.length) % opts.length;
        if (!opts[n]?.disabled) break;
      }
      return n;
    });
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (disabled) return;
    if (!open) {
      if (["Enter", " ", "ArrowDown", "ArrowUp"].includes(e.key)) {
        e.preventDefault();
        setOpen(true);
      }
      return;
    }
    switch (e.key) {
      case "ArrowDown": e.preventDefault(); step(1); break;
      case "ArrowUp": e.preventDefault(); step(-1); break;
      case "Home": e.preventDefault(); setActive(0); break;
      case "End": e.preventDefault(); setActive(opts.length - 1); break;
      case "Enter": case " ": e.preventDefault(); commit(active); break;
      case "Escape": e.preventDefault(); setOpen(false); btnRef.current?.focus(); break;
      case "Tab": setOpen(false); break;
      default:
        if (e.key.length === 1) {
          const ta = typeahead.current;
          const now = performance.now();
          ta.str = now - ta.t > 700 ? e.key : ta.str + e.key;
          ta.t = now;
          const q = ta.str.toLowerCase();
          const i = opts.findIndex((o) => optText(o).toLowerCase().startsWith(q));
          if (i >= 0) setActive(i);
        }
    }
  };

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        disabled={disabled}
        className={`ui-select ${size === "sm" ? "sm" : ""} ${open ? "open" : ""} ${className || ""}`}
        style={style}
        onClick={() => !disabled && setOpen((o) => !o)}
        onKeyDown={onKey}
      >
        <span className={`ui-select-val ${current ? "" : "ph"}`}>
          {current ? current.label ?? current.value : placeholder}
        </span>
        <svg className="ui-select-caret" width="10" height="10" viewBox="0 0 10 10" aria-hidden>
          <path d="M2 3.5L5 6.5L8 3.5" fill="none" stroke="currentColor" strokeWidth="1.4"
            strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open &&
        createPortal(
          <div
            ref={menuRef}
            id={listId}
            role="listbox"
            className="ui-select-menu"
            style={menuStyle}
            onKeyDown={onKey}
            tabIndex={-1}
          >
            {opts.map((o, i) => (
              <button
                key={o.value}
                type="button"
                role="option"
                aria-selected={o.value === value}
                disabled={o.disabled}
                className={`ui-select-opt ${i === active ? "active" : ""} ${o.value === value ? "on" : ""}`}
                onMouseEnter={() => setActive(i)}
                onClick={() => commit(i)}
              >
                <span className="ui-select-opt-lbl">{o.label ?? o.value}</span>
                {o.value === value && (
                  <svg className="ui-select-tick" width="12" height="12" viewBox="0 0 12 12" aria-hidden>
                    <path d="M2.5 6.5L5 9L9.5 3.5" fill="none" stroke="currentColor"
                      strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                )}
              </button>
            ))}
          </div>,
          document.body,
        )}
    </>
  );
}
