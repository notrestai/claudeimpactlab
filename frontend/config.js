// Where the API lives.
// - Opened from the FastAPI app itself (laptop, tunnel-only launch): same origin, leave empty.
// - Opened from Cloudflare Pages (relayhealth.dev or *.pages.dev): the backend runs behind the tunnel at api.relayhealth.dev.
(function () {
  var h = location.hostname;
  var onPages = /(^|\.)relayhealth\.dev$/.test(h) || /\.pages\.dev$/.test(h);
  window.SST_API_BASE = onPages && h !== 'api.relayhealth.dev' ? 'https://api.relayhealth.dev' : '';
})();
