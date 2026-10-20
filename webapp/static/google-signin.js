/* Sign in with Google (Google Identity Services), shared by the app's sign-in box and the money-niche quiz.
   Shows nothing unless the server has GOOGLE_CLIENT_ID set (/api/config). Google hands us a signed ID token;
   /api/auth/google checks it and signs in the account with that verified email (the same account an email code
   would reach). Scopes: openid email profile only. */
(function () {
    let config = null, loading = null, onCredential = null;

    async function clientId() {
        if (config === null) {
            try { config = await (await fetch('/api/config')).json(); } catch (_) { config = {}; }
        }
        return (config && config.google_client_id) || '';
    }

    function loadScript() {
        if (window.google?.accounts?.id) return Promise.resolve();
        if (!loading) {
            loading = new Promise((resolve, reject) => {
                const s = document.createElement('script');
                s.src = 'https://accounts.google.com/gsi/client';
                s.async = true; s.defer = true;
                s.onload = resolve; s.onerror = () => { loading = null; reject(new Error('Google sign-in could not load')); };
                document.head.appendChild(s);
            });
        }
        return loading;
    }

    async function signIn(credential) {
        let attribution = null;
        try { attribution = window.CRTelemetry?.attributionProps?.() || null; } catch (_) {}
        const res = await fetch('/api/auth/google', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({credential, attribution}),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || 'Google sign-in didn’t work. Try an email code.');
        return data;
    }

    /** Render the Google button into `el` (hidden until it works). opts: {onSignedIn(data), onError(message), width} */
    async function mount(el, opts = {}) {
        if (!el) return false;
        const id = await clientId();
        if (!id) { el.hidden = true; el.style.display = 'none'; return false; }
        try { await loadScript(); } catch (e) { el.hidden = true; el.style.display = 'none'; return false; }
        onCredential = async ({credential}) => {
            try { opts.onSignedIn && await opts.onSignedIn(await signIn(credential)); }
            catch (e) { opts.onError && opts.onError(e.message); }
        };
        window.google.accounts.id.initialize({
            client_id: id,
            callback: (response) => onCredential && onCredential(response),
            ux_mode: 'popup',
            auto_select: false,
            cancel_on_tap_outside: true,
            context: 'signin',
        });
        el.innerHTML = '';
        window.google.accounts.id.renderButton(el, {
            type: 'standard', theme: opts.theme || 'outline', size: 'large', text: 'continue_with',
            shape: 'pill', logo_alignment: 'center', width: Math.min(opts.width || 320, 400),
        });
        el.hidden = false;
        el.style.display = 'flex';
        opts.onShown && opts.onShown();
        return true;
    }

    window.CRGoogle = {mount, clientId};
})();
