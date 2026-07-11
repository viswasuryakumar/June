// Trivial background service worker for the dummy test extension.
// Its only purpose is to exist so Playwright's launch_persistent_context
// has something to load and register, proving the extension-loading path works.
self.addEventListener("install", () => {
  // no-op
});
