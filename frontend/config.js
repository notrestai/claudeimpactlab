// Where the API lives.
// - Opened from the FastAPI app itself (laptop, tunnel-only launch): same origin, leave empty.
// - Opened from Cloudflare (relayhealth.dev, *.workers.dev or *.pages.dev): the backend runs behind the tunnel at api.relayhealth.dev.
(function () {
  var h = location.hostname;
  var onCloudflare = /(^|\.)relayhealth\.dev$/.test(h) || /\.pages\.dev$/.test(h) || /\.workers\.dev$/.test(h);
  window.SST_API_BASE = onCloudflare && h !== 'api.relayhealth.dev' ? 'https://api.relayhealth.dev' : '';
})();
