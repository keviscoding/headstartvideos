/* ChannelRecipe marketing landing page — light cream canvas, single column.
   Composes DS primitives + the hero DemoLoop. Global (no import/export). */
(function () {
  const { useState } = React;
  const CR = window.ChannelRecipeDesignSystem_51526c;
  const { Logo, Button, PillBadge, RecipeCard, BrowserFrame, StatusChip } = CR;
  const { Icon, RECIPES, Section, Eyebrow } = window;

  /* ---------------- Nav ---------------- */
  function Nav() {
    return (
      <header style={{ position: "sticky", top: 0, zIndex: 20, background: "color-mix(in srgb, var(--canvas) 88%, transparent)", backdropFilter: "blur(10px)", borderBottom: "1px solid var(--hairline)" }}>
        <nav style={{ maxWidth: "var(--container-max)", margin: "0 auto", padding: "14px var(--gutter)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <Logo variant="horizontal" size={28} />
          <div style={{ display: "flex", alignItems: "center", gap: 22 }}>
            <a href="#pricing" style={{ fontFamily: "var(--font-body)", fontSize: 15, fontWeight: 500 }}>Pricing</a>
            <a href="#faq" style={{ fontFamily: "var(--font-body)", fontSize: 15, fontWeight: 500 }}>FAQ</a>
            <Button variant="primary" size="sm">Start free</Button>
          </div>
        </nav>
      </header>
    );
  }

  /* ---------------- Hero ---------------- */
  function Hero() {
    return (
      <Section style={{ paddingTop: 0 }}>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", textAlign: "center", paddingTop: 64 }}>
          <PillBadge>New: this week's recipe just dropped</PillBadge>
          <h1 style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: "clamp(38px, 6.4vw, 68px)", lineHeight: 1.02, letterSpacing: "-.025em", color: "var(--ink)", margin: "22px 0 0", maxWidth: 900, textWrap: "balance" }}>
            Turn a proven niche into a finished YouTube video in <span style={{ color: "var(--accent)" }}>15 minutes</span>
          </h1>
          <p style={{ fontFamily: "var(--font-body)", fontSize: "clamp(17px, 2.2vw, 20px)", lineHeight: 1.5, color: "var(--ink-2)", margin: "20px 0 0", maxWidth: 620 }}>
            No editing, no camera, no experience. You pick the recipe — we write the script, record the voice, make the thumbnail, and render the video.
          </p>
          <div style={{ marginTop: 30, display: "flex", flexDirection: "column", alignItems: "center", gap: 12 }}>
            <Button variant="primary" size="lg" leftIcon={<Icon name="play" size={17} />}>Make your first video free</Button>
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 13, color: "var(--ink-3)", letterSpacing: ".01em" }}>3 free videos · no card · 2-minute setup</span>
          </div>
        </div>
        <div style={{ maxWidth: 760, margin: "48px auto 0" }}>
          <BrowserFrame url="app.channelrecipe.com/new">
            <window.DemoLoop />
          </BrowserFrame>
          <p style={{ textAlign: "center", fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--ink-3)", marginTop: 14 }}>
            A real cook, start to finish — no talking, no fluff.
          </p>
        </div>
      </Section>
    );
  }

  /* ---------------- How it works ---------------- */
  function HowItWorks() {
    const steps = [
      { n: "01", title: "Pick a recipe", body: "Choose a proven niche card. That's step one done — you've already started.", crop: <MiniRecipe /> },
      { n: "02", title: "We cook it", body: "Script, voiceover, thumbnail, and a rendered video — about 15 minutes.", crop: <MiniCook /> },
      { n: "03", title: "Download your Upload Kit", body: "Video file, title, description, and tags. Post it and you're live.", crop: <MiniKit /> },
    ];
    return (
      <Section tone="band" id="how">
        <Eyebrow>How it works</Eyebrow>
        <h2 style={h2Style}>Three steps. No guesswork.</h2>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 20, marginTop: 40 }}>
          {steps.map((s) => (
            <div key={s.n} style={{ background: "var(--white)", border: "1px solid var(--hairline)", borderRadius: "var(--radius-card)", padding: 22, boxShadow: "var(--shadow-sm)", display: "flex", flexDirection: "column", gap: 14 }}>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 13, color: "var(--accent)", letterSpacing: ".08em" }}>{s.n}</span>
              <div style={{ borderRadius: "var(--radius-md)", overflow: "hidden", border: "1px solid var(--hairline)" }}>{s.crop}</div>
              <div style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 21, letterSpacing: "-.01em", color: "var(--ink)" }}>{s.title}</div>
              <p style={{ fontFamily: "var(--font-body)", fontSize: 15, lineHeight: 1.55, color: "var(--ink-2)", margin: 0 }}>{s.body}</p>
            </div>
          ))}
        </div>
      </Section>
    );
  }

  const cropWrap = { background: "var(--app-bg)", padding: 14, minHeight: 92 };
  function MiniRecipe() {
    return (
      <div style={cropWrap}>
        <div style={{ background: "var(--app-surface)", border: "1px solid var(--app-border)", borderRadius: 10, padding: 12, position: "relative" }}>
          <svg width="26" height="26" viewBox="0 0 40 40" style={{ position: "absolute", top: 0, right: 0 }}><path d="M40 0 V22 L18 0 Z" fill="var(--accent)" opacity="0.16" /><path d="M15 11 L15 27 L29 19 Z" fill="var(--accent)" /></svg>
          <div style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 14, color: "var(--app-ink)" }}>Reddit story timers</div>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--app-ink-3)", marginTop: 8 }}>~15 min · 1 credit · RPM $4–8</div>
        </div>
      </div>
    );
  }
  function MiniCook() {
    return (
      <div style={cropWrap}>
        <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--accent)", letterSpacing: ".06em", textTransform: "uppercase", marginBottom: 8 }}>Cooking your video</div>
        <div style={{ height: 7, borderRadius: 999, background: "var(--app-surface-2)", overflow: "hidden" }}><div style={{ width: "68%", height: "100%", background: "var(--accent)", borderRadius: 999 }} /></div>
        <div style={{ display: "flex", justifyContent: "space-between", marginTop: 7, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--app-ink-3)" }}><span>68%</span><span>about 2 min</span></div>
      </div>
    );
  }
  function MiniKit() {
    return (
      <div style={{ ...cropWrap, display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={{ aspectRatio: "16/9", background: "linear-gradient(135deg,#1b2140,#0F1222)", borderRadius: 6, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <svg width="16" height="16" viewBox="0 0 14 14"><path d="M4 2.5 L4 11.5 L11 7 Z" fill="#fff" /></svg>
        </div>
        <div style={{ display: "flex", gap: 6, fontFamily: "var(--font-mono)", fontSize: 9, color: "var(--app-ink-3)" }}><span style={pillMini}>title</span><span style={pillMini}>description</span><span style={pillMini}>tags</span></div>
      </div>
    );
  }
  const pillMini = { border: "1px solid var(--app-border)", borderRadius: 999, padding: "2px 7px" };

  /* ---------------- Proof strip ---------------- */
  function ProofStrip() {
    const vids = [
      { title: "3 Reddit stories that broke 1M", views: "1,240,900", recipe: "Reddit story timers" },
      { title: "The scariest text you'll read today", views: "612,400", recipe: "Scary text stories" },
      { title: "10 facts that sound fake", views: "388,120", recipe: "Fun facts countdown" },
    ];
    return (
      <Section>
        <Eyebrow>Receipts</Eyebrow>
        <h2 style={h2Style}>Real videos, made with the recipes.</h2>
        <p style={{ ...subStyle }}>Every recipe is run on a real channel before it ships. These are sample results from the founder's dashboard.</p>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 18, marginTop: 36 }}>
          {vids.map((v) => (
            <div key={v.title} style={{ background: "var(--white)", border: "1px solid var(--hairline)", borderRadius: "var(--radius-card)", overflow: "hidden", boxShadow: "var(--shadow-sm)" }}>
              <div style={{ aspectRatio: "16/9", background: "linear-gradient(135deg,#20264a,#0F1222)", display: "flex", alignItems: "center", justifyContent: "center", position: "relative" }}>
                <span style={{ width: 44, height: 44, borderRadius: "50%", background: "rgba(255,255,255,.16)", display: "flex", alignItems: "center", justifyContent: "center" }}><svg width="16" height="16" viewBox="0 0 14 14"><path d="M4 2.5 L4 11.5 L11 7 Z" fill="#fff" /></svg></span>
              </div>
              <div style={{ padding: 16 }}>
                <div style={{ fontFamily: "var(--font-body)", fontWeight: 600, fontSize: 15, color: "var(--ink)", lineHeight: 1.35 }}>{v.title}</div>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 10 }}>
                  <span style={{ fontFamily: "var(--font-mono)", fontSize: 15, fontWeight: 600, color: "var(--ink)" }}>{v.views}</span>
                  <span style={{ fontFamily: "var(--font-body)", fontSize: 13, color: "var(--ink-3)" }}>views</span>
                </div>
                <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--ink-3)", marginTop: 6 }}>via {v.recipe}</div>
              </div>
            </div>
          ))}
        </div>
      </Section>
    );
  }

  /* ---------------- Recipe wall ---------------- */
  function RecipeWall() {
    return (
      <Section tone="band" id="recipes">
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "flex-end", justifyContent: "space-between", gap: 16 }}>
          <div>
            <Eyebrow>The Cookbook</Eyebrow>
            <h2 style={{ ...h2Style, margin: 0 }}>A new proven recipe every week.</h2>
          </div>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 8, fontFamily: "var(--font-body)", fontSize: 15, color: "var(--ink-2)" }}>
            <Icon name="calendar-check" size={17} style={{ color: "var(--accent)" }} /> This week's drop is live
          </span>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 18, marginTop: 36, alignItems: "stretch" }}>
          {RECIPES.map((r) => (
            <RecipeCard key={r.name} {...r} />
          ))}
        </div>
      </Section>
    );
  }

  /* ---------------- Pricing ---------------- */
  function Pricing() {
    const feats = ["~15 videos a month", "Everything included — no API keys", "Every recipe + the weekly drop", "Full Upload Kit for each video", "Failed render? Credit auto-refunded"];
    return (
      <Section id="pricing">
        <Eyebrow>Pricing</Eyebrow>
        <h2 style={h2Style}>One plan. That's it.</h2>
        <div style={{ maxWidth: 440, margin: "40px auto 0", position: "relative", background: "var(--white)", border: "1px solid var(--hairline)", borderRadius: "var(--radius-card)", boxShadow: "var(--shadow-card)", padding: 30, overflow: "hidden" }}>
          <svg width="48" height="48" viewBox="0 0 40 40" style={{ position: "absolute", top: 0, right: 0 }}><path d="M40 0 V24 L16 0 Z" fill="var(--accent)" opacity="0.14" /><path d="M13 12 L13 30 L30 21 Z" fill="var(--accent)" /></svg>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
            <span style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 54, letterSpacing: "-.02em", color: "var(--ink)" }}>$27</span>
            <span style={{ fontFamily: "var(--font-body)", fontSize: 17, color: "var(--ink-3)" }}>/ month</span>
          </div>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 13, color: "var(--ink-3)", marginTop: 4 }}>≈ 15 videos · that's under $2 a video</div>
          <div style={{ height: 1, background: "var(--hairline)", margin: "22px 0" }} />
          <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: 12 }}>
            {feats.map((f) => (
              <li key={f} style={{ display: "flex", alignItems: "center", gap: 11, fontFamily: "var(--font-body)", fontSize: 15, color: "var(--ink-2)" }}>
                <Icon name="check" size={17} style={{ color: "var(--accent)", flex: "none" }} /> {f}
              </li>
            ))}
          </ul>
          <div style={{ marginTop: 26 }}>
            <Button variant="primary" size="lg" full>Make your first video free</Button>
          </div>
          <p style={{ textAlign: "center", fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--ink-3)", margin: "14px 0 0" }}>Start with 3 free videos · no card · cancel anytime</p>
        </div>
      </Section>
    );
  }

  /* ---------------- FAQ ---------------- */
  function FAQ() {
    const items = [
      { q: "Do I need my own API keys?", a: "No. Everything's included — the script model, the voices, the thumbnails, and the rendering. You just pick a recipe." },
      { q: "Are the videos safe to upload to YouTube?", a: "Yes. Every recipe is built to follow YouTube's rules for original, faceless content, and you own what you make." },
      { q: "What if a render fails?", a: "Your credit is refunded automatically — instantly, no support ticket. You'll see it back in your account with a note." },
      { q: "Can I use my own script?", a: "Yes. Defaults are pre-filled so you can move fast, but you can edit the script, swap the voice, or change the thumbnail at any step." },
      { q: "What happens after the free trial?", a: "After 3 free videos (or 7 days), it's $27/month for about 15 videos. No card is needed to start, and you can cancel anytime." },
    ];
    const [open, setOpen] = useState(0);
    return (
      <Section tone="band" id="faq">
        <Eyebrow>FAQ</Eyebrow>
        <h2 style={h2Style}>Straight answers.</h2>
        <div style={{ maxWidth: 720, marginTop: 34, display: "flex", flexDirection: "column", gap: 12 }}>
          {items.map((it, k) => {
            const isOpen = open === k;
            return (
              <div key={k} style={{ background: "var(--white)", border: `1px solid ${isOpen ? "var(--accent)" : "var(--hairline)"}`, borderRadius: "var(--radius-md)", overflow: "hidden", transition: "border-color var(--dur-base)" }}>
                <button type="button" onClick={() => setOpen(isOpen ? -1 : k)} style={{ width: "100%", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16, padding: "18px 20px", background: "none", border: "none", cursor: "pointer", textAlign: "left", fontFamily: "var(--font-body)", fontWeight: 600, fontSize: 17, color: "var(--ink)" }}>
                  {it.q}
                  <span style={{ transform: isOpen ? "rotate(180deg)" : "none", transition: "transform var(--dur-base)", color: "var(--accent)", flex: "none" }}><Icon name="chevron-down" size={20} /></span>
                </button>
                {isOpen && (
                  <div style={{ padding: "0 20px 20px", fontFamily: "var(--font-body)", fontSize: 15, lineHeight: 1.6, color: "var(--ink-2)" }}>{it.a}</div>
                )}
              </div>
            );
          })}
        </div>
      </Section>
    );
  }

  /* ---------------- Final CTA ---------------- */
  function FinalCTA() {
    return (
      <section style={{ background: "var(--app-bg)" }}>
        <div style={{ maxWidth: "var(--container-max)", margin: "0 auto", padding: "var(--section-y) var(--gutter)", textAlign: "center", display: "flex", flexDirection: "column", alignItems: "center" }}>
          <img src="../../assets/glyph.svg" width="44" height="44" alt="" />
          <h2 style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: "clamp(30px,4.5vw,46px)", letterSpacing: "-.02em", color: "var(--app-ink)", margin: "20px 0 0", maxWidth: 620, textWrap: "balance" }}>
            Pick a recipe. We handle the <span style={{ color: "var(--accent)" }}>cooking</span>.
          </h2>
          <p style={{ fontFamily: "var(--font-body)", fontSize: 18, color: "var(--app-ink-2)", margin: "16px 0 0" }}>Your first video is free. You could be uploading tonight.</p>
          <div style={{ marginTop: 28, display: "flex", flexDirection: "column", alignItems: "center", gap: 12 }}>
            <Button variant="primary" size="lg" theme="dark" leftIcon={<Icon name="play" size={17} />}>Make your first video free</Button>
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 13, color: "var(--app-ink-3)" }}>3 free videos · no card · 2-minute setup</span>
          </div>
        </div>
      </section>
    );
  }

  /* ---------------- Footer ---------------- */
  function Footer() {
    return (
      <footer style={{ background: "var(--canvas)", borderTop: "1px solid var(--hairline)" }}>
        <div style={{ maxWidth: "var(--container-max)", margin: "0 auto", padding: "40px var(--gutter)", display: "flex", flexWrap: "wrap", gap: 20, alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <Logo variant="horizontal" size={24} />
            <span style={{ fontFamily: "var(--font-body)", fontSize: 13, color: "var(--ink-3)" }}>From the team behind HeadStart Channels.</span>
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 20, alignItems: "center", fontFamily: "var(--font-body)", fontSize: 14 }}>
            <a href="#terms">Terms</a>
            <a href="#privacy">Privacy</a>
            <a href="#refund">Refund policy</a>
            <a href="mailto:hello@channelrecipe.com" style={{ fontFamily: "var(--font-mono)", fontSize: 13 }}>hello@channelrecipe.com</a>
          </div>
        </div>
      </footer>
    );
  }

  const h2Style = { fontFamily: "var(--font-display)", fontWeight: 800, fontSize: "clamp(28px,4vw,42px)", letterSpacing: "-.02em", color: "var(--ink)", margin: "0 0 0", textWrap: "balance" };
  const subStyle = { fontFamily: "var(--font-body)", fontSize: 17, lineHeight: 1.55, color: "var(--ink-2)", margin: "14px 0 0", maxWidth: 620 };

  function MarketingPage() {
    return (
      <div>
        <Nav />
        <main>
          <Hero />
          <HowItWorks />
          <ProofStrip />
          <RecipeWall />
          <Pricing />
          <FAQ />
          <FinalCTA />
        </main>
        <Footer />
      </div>
    );
  }

  window.MarketingPage = MarketingPage;
})();
