// The ChannelRecipe Discord is for members. Links go through /community/discord,
// which sends members to the invite; everyone else sees what joining unlocks.
(function () {
    var member = null;
    fetch('/api/community/access', {credentials: 'same-origin'})
        .then(function (r) { return r.json(); })
        .then(function (d) { member = !!d.member; })
        .catch(function () { member = false; });

    function seePlans() {
        close();
        if (typeof window.openUpgradeFlow === 'function') { window.openUpgradeFlow({reason: 'discord'}); return; }
        var pricing = document.querySelector('.pricing-section');
        if (pricing) { pricing.scrollIntoView({behavior: 'smooth'}); return; }
        location.href = '/#pricing';
    }
    function close() { var box = document.getElementById('cr-community'); if (box) box.remove(); }
    function show() {
        if (document.getElementById('cr-community')) return;
        var box = document.createElement('div');
        box.id = 'cr-community';
        box.setAttribute('role', 'dialog');
        box.setAttribute('aria-modal', 'true');
        box.setAttribute('aria-labelledby', 'cr-community-title');
        box.style.cssText = 'position:fixed;inset:0;z-index:1000;display:flex;align-items:center;justify-content:center;padding:16px;background:rgba(14,12,34,.55)';
        box.innerHTML =
            '<div style="width:100%;max-width:420px;background:#fff;color:#16161A;border-radius:18px;padding:28px 26px;box-shadow:0 24px 70px rgba(0,0,0,.25);font-family:inherit">' +
            '<h2 id="cr-community-title" style="font-size:22px;font-weight:800;margin:0 0 10px;letter-spacing:-.01em">The Discord is for members</h2>' +
            '<p style="margin:0 0 20px;color:#55534D;font-size:15px;line-height:1.6">Join ChannelRecipe to get help from the team, share your videos and take part in giveaways. You get the invite as soon as you subscribe.</p>' +
            '<div style="display:flex;gap:10px;flex-wrap:wrap">' +
            '<button type="button" data-plans style="flex:1;min-width:140px;border:0;border-radius:12px;padding:13px 16px;background:#6D5AE0;color:#fff;font:inherit;font-weight:700;cursor:pointer">See plans</button>' +
            '<button type="button" data-close style="flex:1;min-width:100px;border:1px solid #E5E1D8;border-radius:12px;padding:13px 16px;background:#fff;color:#16161A;font:inherit;font-weight:600;cursor:pointer">Not now</button>' +
            '</div></div>';
        box.addEventListener('click', function (e) {
            if (e.target === box || e.target.closest('[data-close]')) close();
            else if (e.target.closest('[data-plans]')) seePlans();
        });
        document.addEventListener('keydown', function onKey(e) { if (e.key === 'Escape') { close(); document.removeEventListener('keydown', onKey); } });
        document.body.appendChild(box);
        box.querySelector('[data-plans]').focus();
    }
    document.addEventListener('click', function (e) {
        var link = e.target.closest && e.target.closest('a[data-community="discord"]');
        if (!link || member === true) return;
        e.preventDefault();
        if (member === null) {
            fetch('/api/community/access', {credentials: 'same-origin'}).then(function (r) { return r.json(); })
                .then(function (d) { member = !!d.member; if (member) location.href = '/community/discord'; else show(); })
                .catch(show);
            return;
        }
        show();
    }, true);
})();
