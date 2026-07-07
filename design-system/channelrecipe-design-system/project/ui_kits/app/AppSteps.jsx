/* Body content for each pipeline step. Presentational — driven by AppShell
   state via props. Global (no import/export). */
(function () {
  const { useState } = React;
  const CR = window.ChannelRecipeDesignSystem_51526c;
  const { Button, VoiceOption, Input } = CR;
  const { Icon } = window;

  const label = { fontFamily: "var(--font-body)", fontWeight: 600, fontSize: 15, color: "var(--app-text-strong)", marginBottom: 10 };
  const hint = { fontFamily: "var(--font-body)", fontSize: 14, color: "var(--app-text-muted)", marginTop: 6 };
  const surface = { background: "var(--app-surface-2)", border: "1px solid var(--app-border)", borderRadius: "var(--radius-md)" };

  function StepHead({ title, sub }) {
    return (
      <div style={{ marginBottom: 22 }}>
        <h2 style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 28, letterSpacing: "-.02em", color: "var(--app-text-strong)", margin: 0 }}>{title}</h2>
        <p style={{ fontFamily: "var(--font-body)", fontSize: 15, color: "var(--app-text-body)", margin: "6px 0 0" }}>{sub}</p>
      </div>
    );
  }

  function Advanced({ children }) {
    const [open, setOpen] = useState(false);
    return (
      <div style={{ marginTop: 22, borderTop: "1px solid var(--app-border)", paddingTop: 16 }}>
        <button type="button" onClick={() => setOpen(!open)} style={{ display: "flex", alignItems: "center", gap: 8, background: "none", border: "none", cursor: "pointer", color: "var(--app-text-body)", fontFamily: "var(--font-body)", fontSize: 14, fontWeight: 500, padding: 0 }}>
          <span style={{ transform: open ? "rotate(90deg)" : "none", transition: "transform var(--dur-base)", display: "inline-flex" }}><Icon name="chevron-right" size={16} /></span>
          Advanced options
        </button>
        {open && <div style={{ marginTop: 14 }}>{children}</div>}
      </div>
    );
  }

  const chip = (active) => ({
    display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, padding: "13px 15px",
    borderRadius: "var(--radius-md)", cursor: "pointer",
    background: active ? "var(--accent-soft-dark)" : "var(--app-surface-2)",
    border: `1.5px solid ${active ? "var(--accent)" : "var(--app-border)"}`,
    fontFamily: "var(--font-body)", fontSize: 15, color: "var(--app-text-strong)",
  });

  function TitleStep({ recipe }) {
    const titles = [
      "3 Reddit stories that broke 1M views",
      "The Reddit story that had everyone counting down",
      "You won't guess how this Reddit story ends",
    ];
    const [sel, setSel] = useState(0);
    return (
      <div>
        <StepHead title="Pick a title" sub="We drafted a few from proven patterns. Pick one or write your own." />
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {titles.map((t, i) => (
            <div key={i} style={chip(sel === i)} onClick={() => setSel(i)}>
              <span>{t}</span>
              <span style={{ width: 20, height: 20, flex: "none", borderRadius: "50%", border: `2px solid ${sel === i ? "var(--accent)" : "var(--app-border)"}`, display: "flex", alignItems: "center", justifyContent: "center" }}>{sel === i && <span style={{ width: 10, height: 10, borderRadius: "50%", background: "var(--accent)" }} />}</span>
            </div>
          ))}
        </div>
        <Advanced>
          <Input theme="dark" label="Write your own title" placeholder="Your title" />
        </Advanced>
      </div>
    );
  }

  function ScriptStep() {
    const script = "Ever wonder why some Reddit stories keep you watching to the very end?\n\nHere are three that blew up last week — and the exact timer trick behind them.\n\nFirst up: a story about a text that changed everything. Watch the clock in the corner…";
    return (
      <div>
        <StepHead title="Review the script" sub="Written from the recipe. Edit anything, or keep it as is." />
        <div style={{ ...surface, padding: 16 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--accent)" }}>Script · 142 words · ~48s</span>
            <button type="button" style={{ display: "flex", alignItems: "center", gap: 6, background: "none", border: "none", color: "var(--app-text-body)", cursor: "pointer", fontFamily: "var(--font-body)", fontSize: 13 }}><Icon name="rotate-ccw" size={14} /> Regenerate</button>
          </div>
          <textarea defaultValue={script} style={{ width: "100%", minHeight: 150, resize: "vertical", background: "transparent", border: "none", outline: "none", color: "var(--app-text-body)", fontFamily: "var(--font-body)", fontSize: 15, lineHeight: 1.6 }} />
        </div>
      </div>
    );
  }

  const VOICES = [
    { name: "Ava", descriptor: "Warm US female", recommended: true },
    { name: "Marcus", descriptor: "Calm US male" },
    { name: "Priya", descriptor: "Bright Indian English" },
    { name: "Sofia", descriptor: "Soft UK female" },
    { name: "Deo", descriptor: "Deep US male" },
    { name: "Lena", descriptor: "Energetic US female" },
  ];
  function VoiceStep() {
    const [sel, setSel] = useState(0);
    const [playing, setPlaying] = useState(-1);
    return (
      <div>
        <StepHead title="Choose a voice" sub="Six voices, tuned for this recipe. The best pick is ready to go." />
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
          {VOICES.map((v, i) => (
            <VoiceOption key={v.name} name={v.name} descriptor={v.descriptor} recommended={v.recommended} selected={sel === i} playing={playing === i} onSelect={() => setSel(i)} onPlay={() => setPlaying(playing === i ? -1 : i)} />
          ))}
        </div>
      </div>
    );
  }

  function ThumbStep() {
    const [sel, setSel] = useState(0);
    const thumbs = [
      { grad: "linear-gradient(135deg,#241a52,#0F1222)", text: "1,000,000 VIEWS?", tag: "Bold headline" },
      { grad: "linear-gradient(135deg,#0f2f2a,#0F1222)", text: "THE TIMER TRICK", tag: "Curiosity" },
    ];
    return (
      <div>
        <StepHead title="Pick a thumbnail" sub="Two options in the recipe's proven style." />
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
          {thumbs.map((t, i) => (
            <div key={i} onClick={() => setSel(i)} style={{ cursor: "pointer", borderRadius: "var(--radius-md)", overflow: "hidden", border: `2px solid ${sel === i ? "var(--accent)" : "var(--app-border)"}` }}>
              <div style={{ aspectRatio: "16/9", background: t.grad, display: "flex", alignItems: "center", justifyContent: "center", padding: 12 }}>
                <span style={{ fontFamily: "var(--font-display)", fontWeight: 900, fontSize: 26, color: "#fff", textAlign: "center", letterSpacing: "-.01em", textShadow: "0 2px 12px rgba(0,0,0,.5)" }}>{t.text}</span>
              </div>
              <div style={{ padding: "9px 12px", display: "flex", justifyContent: "space-between", alignItems: "center", background: "var(--app-surface-2)" }}>
                <span style={{ fontFamily: "var(--font-body)", fontSize: 13, color: "var(--app-text-body)" }}>{t.tag}</span>
                {sel === i && <span style={{ color: "var(--accent)", display: "inline-flex" }}><Icon name="check" size={16} /></span>}
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  function CookStep({ recipe }) {
    const rows = [
      ["Recipe", recipe],
      ["Title", "3 Reddit stories that broke 1M views"],
      ["Voice", "Ava · Warm US female"],
      ["Length", "~48s · vertical 9:16"],
      ["Cost", "1 credit"],
    ];
    return (
      <div>
        <StepHead title="Ready to cook" sub="Here's your video. This uses 1 of your 11 credits." />
        <div style={{ ...surface, padding: 4 }}>
          {rows.map(([k, v], i) => (
            <div key={k} style={{ display: "flex", justifyContent: "space-between", padding: "13px 15px", borderBottom: i < rows.length - 1 ? "1px solid var(--app-border)" : "none" }}>
              <span style={{ fontFamily: "var(--font-body)", fontSize: 14, color: "var(--app-text-muted)" }}>{k}</span>
              <span style={{ fontFamily: k === "Cost" ? "var(--font-mono)" : "var(--font-body)", fontSize: 14, color: "var(--app-text-strong)", fontWeight: 500 }}>{v}</span>
            </div>
          ))}
        </div>
      </div>
    );
  }

  window.AppSteps = { TitleStep, ScriptStep, VoiceStep, ThumbStep, CookStep, StepHead };
})();
