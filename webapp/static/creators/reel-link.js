/* Reads a pasted TikTok / Instagram Reel / YouTube Short link, or a creator's TikTok / Instagram profile (we learn from
   their most watched recent Reels). Shared by the landing page and the app.
   Returns {platform, kind, handle, id, url, thumb, profile?} or null. Mirrors what the edit machines can open. */
(function () {
  function parseReelLink(raw) {
    var s = String(raw || "").trim();
    if (!s) return null;
    if (!/^https?:\/\//i.test(s)) s = "https://" + s;
    var u;
    try { u = new URL(s); } catch (e) { return null; }
    var host = u.hostname.toLowerCase().replace(/^(www|m|mobile)\./, "");
    var path = u.pathname;
    var m;
    if (host === "tiktok.com") {
      m = path.match(/^\/@([\w.]{2,32})\/(video|photo)\/(\d{6,})/);
      if (m) return { platform: "TikTok", kind: "tiktok", handle: "@" + m[1], id: m[3], url: u.href, thumb: null };
      m = path.match(/^\/t\/([\w]+)/);
      if (m) return { platform: "TikTok", kind: "tiktok", handle: null, id: m[1], url: u.href, thumb: null };
      m = path.match(/^\/@([\w.]{2,40})\/?$/);
      if (m) return { platform: "TikTok profile", kind: "tiktok", handle: "@" + m[1], id: "@" + m[1], profile: true,
                      url: "https://www.tiktok.com/@" + m[1], thumb: null };
      return null;
    }
    if (host === "vm.tiktok.com" || host === "vt.tiktok.com") {
      m = path.match(/^\/([\w]{5,})/);
      return m ? { platform: "TikTok", kind: "tiktok", handle: null, id: m[1], url: u.href, thumb: null } : null;
    }
    if (host === "instagram.com") {
      m = path.match(/^\/(?:[\w.]+\/)?(reel|reels|p)\/([\w-]{5,})/);
      if (m) return { platform: "Instagram Reel", kind: "instagram", handle: null, id: m[2], url: u.href, thumb: null };
      m = path.match(/^\/([\w.]{1,30})\/?$/);
      var reserved = /^(reels?|p|tv|stories|explore|accounts|direct|about|developer|legal|web)$/i;
      return m && !reserved.test(m[1]) ? { platform: "Instagram profile", kind: "instagram", handle: "@" + m[1],
                                            id: "@" + m[1], profile: true, url: "https://www.instagram.com/" + m[1], thumb: null } : null;
    }
    var yt = null;
    if (host === "youtube.com") {
      m = path.match(/^\/shorts\/([\w-]{6,})/);
      if (m) yt = { id: m[1], short: true };
      else if (path === "/watch" && /^[\w-]{6,}$/.test(u.searchParams.get("v") || "")) yt = { id: u.searchParams.get("v"), short: false };
    } else if (host === "youtu.be") {
      m = path.match(/^\/([\w-]{6,})/);
      if (m) yt = { id: m[1], short: false };
    }
    if (yt) {
      return { platform: yt.short ? "YouTube Short" : "YouTube", kind: "youtube", handle: null, id: yt.id, url: u.href,
               thumb: "https://i.ytimg.com/vi/" + yt.id + "/hqdefault.jpg" };
    }
    return null;
  }
  window.CRReel = { parse: parseReelLink };
})();
