/* Hero demo loop — a silent, auto-advancing mock of the product pipeline,
   shown inside a BrowserFrame. Cycles: pick recipe -> script -> voice ->
   render -> finished video, then loops. Global (no import/export). */
(function () {
  const { useState, useEffect, useRef } = React;
  const { RecipeCard, ProgressStages, Stepper } = window.ChannelRecipeDesignSystem_51526c;

  const STAGES = ["recipe", "script", "voice", "render", "done"];
  const DUR = 2600;

  function DemoLoop() {
    const [i, setI] = useState(0);
    const timer = useRef(null);
    useEffect(() => {
      timer.current = setInterval(() => setI((p) => (p + 1) % STAGES.length), DUR);
      return () => clearInterval(timer.current);
    }, []);
    const stage = STAGES[i];
    const stepIndex = { recipe: 0, script: 2, voice: 3, render: 5, done: 5 }[stage];

    return (
      <div style={{ padding: "18px 20px 22px", minHeight: 300, display: "flex", flexDirection: "column", gap: 16 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <img src="../../assets/glyph.svg" width="20" height="20" alt="" />
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--app-text-muted)", letterSpacing: ".02em" }}>
              new video
            </span>
          </div>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--app-text-muted)" }}>11 credits</span>
        </div>

        <Stepper current={stepIndex} />

        <div style={{ flex: 1, display: "flex", alignItems: "stretch" }}>
          {stage === "recipe" && <RecipeStage />}
          {stage === "script" && <ScriptStage />}
          {stage === "voice" && <VoiceStage />}
          {stage === "render" && <RenderStage />}
          {stage === "done" && <DoneStage />}
        </div>
      </div>
    );
  }

  function RecipeStage() {
    return (
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, width: "100%", animation: "cr-fade .4s var(--ease-standard)" }}>
        <RecipeCard theme="dark" selected status="proven" name="Reddit story timers" promise="On-screen text + a countdown." cookTime="~15 min" credits="1 credit" rpm="RPM $4–8" />
        <RecipeCard theme="dark" status="new" name="Scary text stories" promise="Two-voice horror chats." cookTime="~15 min" credits="1 credit" rpm="RPM $6–11" />
      </div>
    );
  }

  function Panel({ children, label }) {
    return (
      <div style={{ width: "100%", background: "var(--app-surface)", border: "1px solid var(--app-border)", borderRadius: "var(--radius-card)", padding: 18, animation: "cr-fade .4s var(--ease-standard)" }}>
        {label && (
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, letterSpacing: ".08em", textTransform: "uppercase", color: "var(--accent)", marginBottom: 12 }}>{label}</div>
        )}
        {children}
      </div>
    );
  }

  function ScriptStage() {
    const lines = ["Ever wonder why some Reddit stories", "keep you watching to the very end?", "Here are three that blew up last week —", "and the exact timer trick behind them."];
    return (
      <Panel label="Script">
        <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
          {lines.map((l, k) => (
            <div key={k} style={{ fontFamily: "var(--font-body)", fontSize: 14, lineHeight: 1.5, color: "var(--app-text-body)", opacity: 0, animation: `cr-line .4s var(--ease-standard) forwards`, animationDelay: `${k * 0.28}s` }}>{l}</div>
          ))}
        </div>
      </Panel>
    );
  }

  function VoiceStage() {
    return (
      <Panel label="Voice">
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <span style={{ width: 44, height: 44, borderRadius: "50%", background: "var(--accent)", display: "flex", alignItems: "center", justifyContent: "center", flex: "none" }}>
            <svg width="16" height="16" viewBox="0 0 14 14"><path d="M4 2.5 L4 11.5 L11 7 Z" fill="#fff" /></svg>
          </span>
          <div style={{ flex: 1 }}>
            <div style={{ fontFamily: "var(--font-body)", fontWeight: 600, fontSize: 15, color: "var(--app-text-strong)" }}>Ava · Warm US female</div>
            <div style={{ display: "flex", alignItems: "center", gap: 3, height: 26, marginTop: 6 }}>
              {Array.from({ length: 44 }).map((_, k) => (
                <span key={k} style={{ width: 3, borderRadius: 2, background: "var(--accent)", opacity: 0.35 + 0.65 * Math.abs(Math.sin(k * 0.9)), height: `${20 + 70 * Math.abs(Math.sin(k * 0.7))}%`, animation: "cr-eq 1s ease-in-out infinite", animationDelay: `${k * 0.03}s` }} />
              ))}
            </div>
          </div>
        </div>
      </Panel>
    );
  }

  function RenderStage() {
    return (
      <Panel label="Cooking your video">
        <ProgressStages
          percent={72}
          eta="about 2 minutes"
          stages={[
            { label: "Writing script", state: "done" },
            { label: "Generating voiceover", state: "done" },
            { label: "Assembling b-roll", state: "active" },
            { label: "Rendering", state: "todo" },
          ]}
        />
      </Panel>
    );
  }

  function DoneStage() {
    return (
      <div style={{ width: "100%", animation: "cr-fade .4s var(--ease-standard)", display: "flex", flexDirection: "column", gap: 12 }}>
        <div style={{ position: "relative", width: "100%", aspectRatio: "16 / 9", background: "linear-gradient(135deg,#1b2140,#0F1222)", border: "1px solid var(--app-border)", borderRadius: "var(--radius-md)", display: "flex", alignItems: "center", justifyContent: "center", overflow: "hidden" }}>
          <span style={{ width: 56, height: 56, borderRadius: "50%", background: "rgba(255,255,255,.14)", backdropFilter: "blur(4px)", display: "flex", alignItems: "center", justifyContent: "center" }}>
            <svg width="20" height="20" viewBox="0 0 14 14"><path d="M4 2.5 L4 11.5 L11 7 Z" fill="#fff" /></svg>
          </span>
          <span style={{ position: "absolute", left: 12, bottom: 10, fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 20, color: "#fff", letterSpacing: "-.01em", textShadow: "0 2px 10px rgba(0,0,0,.5)" }}>3 Reddit stories that broke 1M</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontFamily: "var(--font-mono)", fontSize: 12, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--success)", background: "var(--success-soft)", borderRadius: 999, padding: "3px 10px" }}>
            <svg width="11" height="11" viewBox="0 0 12 12"><path d="M1 9 L4 5 L6.5 7 L11 1" fill="none" stroke="var(--success)" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
            Video ready
          </span>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--app-text-muted)" }}>Upload Kit · MP4 + title + tags</span>
        </div>
      </div>
    );
  }

  window.DemoLoop = DemoLoop;
})();
