/* Shared helpers for the UI kits: a Lucide-backed <Icon>, recipe data, and a
   section shell. Global (no import/export). Load before the section files. */
(function () {
  const { useRef, useEffect } = React;

  // Lucide-backed icon. The <i data-lucide> is swapped for an <svg> on mount.
  function Icon({ name, size = 18, strokeWidth = 1.75, style, className }) {
    const ref = useRef(null);
    useEffect(() => {
      const host = ref.current;
      if (!host || !window.lucide) return;
      host.innerHTML = "";
      const el = document.createElement("i");
      el.setAttribute("data-lucide", name);
      host.appendChild(el);
      window.lucide.createIcons({ attrs: { width: size, height: size, "stroke-width": strokeWidth }, nameAttr: "data-lucide" });
    }, [name, size, strokeWidth]);
    return <span ref={ref} className={className} style={{ display: "inline-flex", width: size, height: size, lineHeight: 0, ...style }} aria-hidden="true" />;
  }

  const RECIPES = [
    { name: "Reddit story timers", promise: "Faceless story videos with on-screen text and a countdown.", cookTime: "~15 min", credits: "1 credit", rpm: "RPM $4–8", status: "new" },
    { name: "Scary text stories", promise: "Two-voice horror chats over slow gradient b-roll.", cookTime: "~15 min", credits: "1 credit", rpm: "RPM $6–11", status: "proven" },
    { name: "Fun facts countdown", promise: "Top-10 facts with a ticking number bug.", cookTime: "~15 min", credits: "1 credit", rpm: "RPM $5–9", status: "proven" },
    { name: "Motivation shorts", promise: "Punchy quotes over cinematic loops.", cookTime: "~12 min", credits: "1 credit", rpm: "RPM $3–7", status: "proven" },
    { name: "This day in history", promise: "One dated event, one clean map, one voice.", cookTime: "~15 min", credits: "1 credit", rpm: "RPM $5–10", status: "proven" },
    { name: "Would you rather", promise: "Two options, a countdown, a reveal.", cookTime: "~13 min", credits: "1 credit", rpm: "RPM $4–8", status: "proven" },
  ];

  // Full-bleed section band. tone: "canvas" | "band"
  function Section({ tone = "canvas", children, style, id }) {
    return (
      <section id={id} style={{ background: tone === "band" ? "var(--canvas-2)" : "var(--canvas)", ...style }}>
        <div style={{ maxWidth: "var(--container-max)", margin: "0 auto", padding: "var(--section-y) var(--gutter)" }}>{children}</div>
      </section>
    );
  }

  const Eyebrow = ({ children }) => (
    <div style={{ fontFamily: "var(--font-mono)", fontSize: 12, letterSpacing: ".08em", textTransform: "uppercase", color: "var(--accent)", marginBottom: 14 }}>{children}</div>
  );

  Object.assign(window, { Icon, RECIPES, Section, Eyebrow });
})();
