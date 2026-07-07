/* ChannelRecipe app — dark product. Interactive pipeline shell:
   signup -> recipe wall -> 6-step pipeline -> cooking -> Upload Kit -> account.
   Composes DS primitives. Global (no import/export). */
(function () {
  const { useState, useEffect, useRef } = React;
  const CR = window.ChannelRecipeDesignSystem_51526c;
  const { Logo, Button, RecipeCard, Stepper, ProgressStages, CreditsChip, Input, StatusChip } = CR;
  const { Icon, RECIPES } = window;
  const { TitleStep, ScriptStep, VoiceStep, ThumbStep, CookStep } = window.AppSteps;

  const STEPS = ["Recipe", "Title", "Script", "Voice", "Thumbnail", "Cook"];

  /* ---------------- Top bar ---------------- */
  function TopBar({ credits, onNav, view }) {
    const tab = (id, txt) => (
      <button type="button" onClick={() => onNav(id)} style={{ background: "none", border: "none", cursor: "pointer", fontFamily: "var(--font-body)", fontSize: 14, fontWeight: 500, color: view === id ? "var(--app-text-strong)" : "var(--app-text-muted)", padding: 0 }}>{txt}</button>
    );
    return (
      <header style={{ position: "sticky", top: 0, zIndex: 20, background: "color-mix(in srgb, var(--app-bg) 88%, transparent)", backdropFilter: "blur(10px)", borderBottom: "1px solid var(--app-border)" }}>
        <div style={{ maxWidth: 1080, margin: "0 auto", padding: "13px 24px", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 26 }}>
            <button type="button" onClick={() => onNav("wall")} style={{ background: "none", border: "none", cursor: "pointer", padding: 0 }}><Logo variant="horizontal" theme="dark" size={26} /></button>
            <nav style={{ display: "flex", gap: 20 }}>{tab("wall", "New video")}{tab("account", "Account")}</nav>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
            <CreditsChip credits={credits} resets="Jul 14" />
            <span style={{ width: 32, height: 32, borderRadius: "50%", background: "var(--accent)", color: "#fff", display: "flex", alignItems: "center", justifyContent: "center", fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 14 }}>A</span>
          </div>
        </div>
      </header>
    );
  }

  const page = { maxWidth: 1080, margin: "0 auto", padding: "40px 24px 80px" };
  const panel = { background: "var(--app-surface)", border: "1px solid var(--app-border)", borderRadius: "var(--radius-card)", padding: 26 };

  /* ---------------- Signup ---------------- */
  function Signup({ onDone }) {
    return (
      <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", padding: 24 }}>
        <div style={{ width: "100%", maxWidth: 400 }}>
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 18, marginBottom: 26 }}>
            <Logo variant="glyph" theme="dark" size={44} />
            <div style={{ textAlign: "center" }}>
              <h1 style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 30, letterSpacing: "-.02em", color: "var(--app-text-strong)", margin: 0 }}>Start free</h1>
              <p style={{ fontFamily: "var(--font-body)", fontSize: 15, color: "var(--app-text-body)", margin: "8px 0 0" }}>Pick a recipe. We handle the cooking.</p>
            </div>
          </div>
          <div style={{ ...panel, display: "flex", flexDirection: "column", gap: 14 }}>
            <Button variant="secondary" theme="dark" full leftIcon={<span style={{ width: 18, height: 18, borderRadius: "50%", background: "#fff", color: "#16161A", display: "flex", alignItems: "center", justifyContent: "center", fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 12 }}>G</span>}>Continue with Google</Button>
            <div style={{ display: "flex", alignItems: "center", gap: 12, color: "var(--app-text-muted)" }}>
              <span style={{ flex: 1, height: 1, background: "var(--app-border)" }} />
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, letterSpacing: ".06em" }}>OR</span>
              <span style={{ flex: 1, height: 1, background: "var(--app-border)" }} />
            </div>
            <Input theme="dark" label="Email" type="email" placeholder="you@email.com" leftIcon={<span style={{ color: "var(--app-text-muted)", display: "inline-flex" }}><Icon name="mail" size={16} /></span>} />
            <Button variant="primary" theme="dark" full onClick={onDone}>Make your first video free</Button>
            <p style={{ textAlign: "center", fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--app-text-muted)", margin: 0 }}>3 free videos · no card · 2-minute setup</p>
          </div>
        </div>
      </div>
    );
  }

  /* ---------------- Recipe wall ---------------- */
  function Wall({ onPick, credits }) {
    return (
      <div style={page}>
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "flex-end", justifyContent: "space-between", gap: 12, marginBottom: 8 }}>
          <div>
            <h1 style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 32, letterSpacing: "-.02em", color: "var(--app-text-strong)", margin: 0 }}>Pick your first recipe</h1>
            <p style={{ fontFamily: "var(--font-body)", fontSize: 16, color: "var(--app-text-body)", margin: "8px 0 0" }}>Each one is proven on a real channel. A new recipe drops every week.</p>
          </div>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 8, fontFamily: "var(--font-body)", fontSize: 14, color: "var(--app-text-body)" }}><Icon name="calendar-check" size={16} style={{ color: "var(--accent)" }} /> This week's drop is live</span>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 18, marginTop: 28, alignItems: "stretch" }}>
          {RECIPES.map((r) => (
            <RecipeCard key={r.name} theme="dark" {...r} onClick={() => onPick(r.name)} />
          ))}
        </div>
      </div>
    );
  }

  /* ---------------- Pipeline ---------------- */
  function Pipeline({ step, setStep, recipe, onCook, onExit }) {
    const bodies = {
      1: <TitleStep recipe={recipe} />,
      2: <ScriptStep />,
      3: <VoiceStep />,
      4: <ThumbStep />,
      5: <CookStep recipe={recipe} />,
    };
    const back = () => (step <= 1 ? onExit() : setStep(step - 1));
    const next = () => (step >= 5 ? onCook() : setStep(step + 1));
    return (
      <div style={page}>
        <div style={{ ...panel, marginBottom: 22 }}>
          <Stepper current={step} />
        </div>
        <div style={{ ...panel }}>
          {bodies[step]}
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 28, paddingTop: 20, borderTop: "1px solid var(--app-border)" }}>
            <Button variant="ghost" theme="dark" onClick={back} leftIcon={<Icon name="arrow-left" size={16} />}>{step <= 1 ? "Recipes" : "Back"}</Button>
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--app-text-muted)" }}>Step {step + 1} of 6</span>
            {step >= 5
              ? <Button variant="primary" theme="dark" onClick={next} leftIcon={<Icon name="play" size={16} />}>Cook video</Button>
              : <Button variant="primary" theme="dark" onClick={next} rightIcon={<Icon name="arrow-right" size={16} />}>Next</Button>}
          </div>
        </div>
      </div>
    );
  }

  /* ---------------- Cooking ---------------- */
  function Cooking({ onDone }) {
    const [pct, setPct] = useState(6);
    const done = pct >= 100;
    useEffect(() => {
      if (done) return;
      const t = setInterval(() => setPct((p) => Math.min(100, p + 4)), 260);
      return () => clearInterval(t);
    }, [done]);
    const stageState = (lo, hi) => (pct >= hi ? "done" : pct >= lo ? "active" : "todo");
    const stages = [
      { label: "Writing script", state: stageState(0, 25) },
      { label: "Generating voiceover", state: stageState(25, 55) },
      { label: "Assembling b-roll", state: stageState(55, 82) },
      { label: "Rendering", state: stageState(82, 100) },
    ];
    return (
      <div style={page}>
        <div style={{ maxWidth: 560, margin: "0 auto", ...panel }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 20 }}>
            <img src="../../assets/glyph.svg" width="30" height="30" alt="" />
            <div>
              <h1 style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 24, letterSpacing: "-.01em", color: "var(--app-text-strong)", margin: 0 }}>{done ? "Recipe followed. Video ready." : "Cooking your video…"}</h1>
              <p style={{ fontFamily: "var(--font-body)", fontSize: 14, color: "var(--app-text-body)", margin: "4px 0 0" }}>{done ? "Your Upload Kit is packed and ready." : "You can leave this page — we'll keep cooking."}</p>
            </div>
          </div>
          {done ? (
            <Button variant="primary" theme="dark" full onClick={onDone} rightIcon={<Icon name="arrow-right" size={16} />}>View your Upload Kit</Button>
          ) : (
            <ProgressStages percent={pct} eta={pct < 60 ? "about 3 minutes" : "about 1 minute"} stages={stages} />
          )}
        </div>
      </div>
    );
  }

  /* ---------------- Upload Kit ---------------- */
  function UploadKit({ onAgain }) {
    const fields = [
      { k: "Title", v: "3 Reddit stories that broke 1M views" },
      { k: "Description", v: "Three proven Reddit stories with the on-screen timer trick behind them. Made with ChannelRecipe." },
      { k: "Tags", v: "reddit stories, faceless youtube, story time, timer" },
    ];
    const [copied, setCopied] = useState(-1);
    const copy = (i, text) => { try { navigator.clipboard.writeText(text); } catch (e) {} setCopied(i); setTimeout(() => setCopied(-1), 1400); };
    return (
      <div style={page}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 20 }}>
          <StatusChip kind="proven">Video ready</StatusChip>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 13, color: "var(--app-text-muted)" }}>This video: 1 credit</span>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1.15fr 1fr", gap: 22, alignItems: "start" }}>
          <div style={{ ...panel, padding: 0, overflow: "hidden" }}>
            <div style={{ position: "relative", aspectRatio: "16/9", background: "linear-gradient(135deg,#241a52,#0F1222)", display: "flex", alignItems: "center", justifyContent: "center" }}>
              <span style={{ width: 60, height: 60, borderRadius: "50%", background: "rgba(255,255,255,.16)", display: "flex", alignItems: "center", justifyContent: "center" }}><svg width="22" height="22" viewBox="0 0 14 14"><path d="M4 2.5 L4 11.5 L11 7 Z" fill="#fff" /></svg></span>
              <span style={{ position: "absolute", left: 14, bottom: 12, fontFamily: "var(--font-display)", fontWeight: 900, fontSize: 22, color: "#fff", letterSpacing: "-.01em", textShadow: "0 2px 12px rgba(0,0,0,.55)" }}>3 Reddit stories<br />that broke 1M</span>
            </div>
            <div style={{ padding: 18, display: "flex", gap: 10, flexWrap: "wrap" }}>
              <Button variant="primary" theme="dark" leftIcon={<Icon name="download" size={16} />}>Download Upload Kit</Button>
              <Button variant="secondary" theme="dark" leftIcon={<Icon name="play" size={16} />}>Preview</Button>
            </div>
            <div style={{ padding: "0 18px 18px", fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--app-text-muted)" }}>MP4 · 1080×1920 · 00:48 · 24 MB</div>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            {fields.map((f, i) => (
              <div key={f.k} style={{ ...panel, padding: 16 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                  <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--accent)" }}>{f.k}</span>
                  <button type="button" onClick={() => copy(i, f.v)} style={{ display: "flex", alignItems: "center", gap: 5, background: "none", border: "none", cursor: "pointer", color: copied === i ? "var(--success)" : "var(--app-text-body)", fontFamily: "var(--font-body)", fontSize: 13 }}>
                    <Icon name={copied === i ? "check" : "copy"} size={14} /> {copied === i ? "Copied" : "Copy"}
                  </button>
                </div>
                <div style={{ fontFamily: "var(--font-body)", fontSize: 14, lineHeight: 1.5, color: "var(--app-text-body)" }}>{f.v}</div>
              </div>
            ))}
            <Button variant="subtle" theme="dark" full onClick={onAgain} leftIcon={<Icon name="plus" size={16} />}>Make another</Button>
          </div>
        </div>
      </div>
    );
  }

  /* ---------------- Account ---------------- */
  function Account({ credits, onSignOut }) {
    const vids = [
      { t: "3 Reddit stories that broke 1M views", d: "Jul 5", views: "1,240,900", status: "monetized" },
      { t: "The scariest text you'll read today", d: "Jul 2", views: "612,400", status: "monetized" },
      { t: "10 facts that sound fake", d: "Jun 28", views: "388,120", status: "proven" },
    ];
    return (
      <div style={page}>
        <h1 style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 32, letterSpacing: "-.02em", color: "var(--app-text-strong)", margin: "0 0 24px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          Account
          <Button variant="ghost" theme="dark" onClick={onSignOut} leftIcon={<Icon name="log-out" size={16} />}>Sign out</Button>
        </h1>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18, marginBottom: 22 }}>
          <div style={panel}>
            <div style={{ fontFamily: "var(--font-body)", fontSize: 14, color: "var(--app-text-muted)" }}>Credits</div>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 40, fontWeight: 600, color: "var(--app-text-strong)", margin: "6px 0 2px" }}>{credits}</div>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 13, color: "var(--app-text-muted)" }}>resets Jul 14 · 1 credit per video</div>
          </div>
          <div style={panel}>
            <div style={{ fontFamily: "var(--font-body)", fontSize: 14, color: "var(--app-text-muted)" }}>Plan</div>
            <div style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 26, color: "var(--app-text-strong)", margin: "6px 0 2px" }}>$27 <span style={{ fontFamily: "var(--font-body)", fontWeight: 400, fontSize: 15, color: "var(--app-text-muted)" }}>/ month</span></div>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 13, color: "var(--app-text-muted)" }}>≈ 15 videos · under $2 each</div>
          </div>
        </div>
        <div style={{ ...panel, padding: 4 }}>
          <div style={{ padding: "14px 16px", fontFamily: "var(--font-mono)", fontSize: 11, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--app-text-muted)" }}>Your videos</div>
          {vids.map((v, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 14, padding: "13px 16px", borderTop: "1px solid var(--app-border)" }}>
              <div style={{ width: 64, height: 36, borderRadius: 6, background: "linear-gradient(135deg,#20264a,#0F1222)", flex: "none" }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontFamily: "var(--font-body)", fontSize: 15, fontWeight: 500, color: "var(--app-text-strong)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{v.t}</div>
                <div style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--app-text-muted)" }}>{v.d} · {v.views} views</div>
              </div>
              <StatusChip kind={v.status}>{v.status === "monetized" ? "Monetized" : "Proven"}</StatusChip>
            </div>
          ))}
        </div>
      </div>
    );
  }

  /* ---------------- Shell / router ---------------- */
  function ChannelRecipeApp() {
    const [view, setView] = useState("wall");
    const [step, setStep] = useState(1);
    const [recipe, setRecipe] = useState(RECIPES[0].name);
    const [credits, setCredits] = useState(11);

    const pick = (name) => { setRecipe(name); setStep(1); setView("pipeline"); };
    const cook = () => { setView("cooking"); };
    const kitDone = () => { setCredits((c) => Math.max(0, c - 1)); setView("kit"); };

    return (
      <div style={{ minHeight: "100vh", background: "var(--app-bg)" }}>
        {view !== "signup" && <TopBar credits={credits} view={view} onNav={(v) => setView(v)} />}
        {view === "signup" && <Signup onDone={() => setView("wall")} />}
        {view === "wall" && <Wall onPick={pick} credits={credits} />}
        {view === "pipeline" && <Pipeline step={step} setStep={setStep} recipe={recipe} onCook={cook} onExit={() => setView("wall")} />}
        {view === "cooking" && <Cooking onDone={kitDone} />}
        {view === "kit" && <UploadKit onAgain={() => setView("wall")} />}
        {view === "account" && <Account credits={credits} onSignOut={() => setView("signup")} />}
      </div>
    );
  }

  window.ChannelRecipeApp = ChannelRecipeApp;
})();
