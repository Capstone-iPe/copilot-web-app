// Opening a health URL in the browser is what makes Render start a sleeping free service.
// A call from the website server does not. Keep each URL open, the same as a tab, for up to 90 seconds.

const HOLD_MS = 90000;

function holdHealth(urls) {
  const list = (urls || []).filter(Boolean);
  if (!list.length) return Promise.resolve();
  return Promise.all(list.map((url) => new Promise((resolve) => {
    const frame = document.createElement("iframe");
    frame.className = "wake-poke";
    frame.setAttribute("aria-hidden", "true");
    frame.tabIndex = -1;
    const timer = window.setTimeout(done, HOLD_MS);
    function done() {
      window.clearTimeout(timer);
      frame.onload = null;
      frame.onerror = null;
      frame.remove();
      resolve();
    }
    frame.onload = done;
    frame.onerror = done;
    frame.src = url;
    document.body.appendChild(frame);
  })));
}
