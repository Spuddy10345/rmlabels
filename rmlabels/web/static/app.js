// Feedback for everything htmx can't report by itself: network failures, timeouts, dismissing and fading the toast.
const toast = () => document.getElementById('toast');

function show(message) {
  const el = toast();
  el.replaceChildren();
  const box = el.appendChild(document.createElement('div'));
  box.className = 'flash error'; box.setAttribute('role', 'alert');
  const text = box.appendChild(document.createElement('span'));
  text.className = 'grow'; text.textContent = message;
  const x = box.appendChild(document.createElement('button'));
  x.type = 'button'; x.className = 'ghost small'; x.setAttribute('aria-label', 'Dismiss'); x.dataset.dismiss = '';
  x.textContent = '✕';
}

document.addEventListener('click', e => {
  if (e.target.closest('[data-dismiss]')) toast().replaceChildren();
});

document.body.addEventListener('htmx:sendError', () =>
  show("Can't reach the server. Check your connection; if you were printing, look at History and the printer before trying again."));
document.body.addEventListener('htmx:timeout', () =>
  show('The server took too long to answer. If you were printing, look at History and the printer before trying again.'));

// Successes fade after a few seconds; errors stay until dismissed.
document.body.addEventListener('htmx:afterSettle', () => {
  const flash = toast().querySelector('.flash:not(.error):not([data-timed])');
  // a toast offering Undo stays longer: nobody reaches for it within six seconds
  if (flash) { flash.dataset.timed = ''; setTimeout(() => flash.remove(), flash.querySelector('[hx-post]') ? 15000 : 6000); }
});

// Two-step buttons (Print, Clear): the first tap arms and shows exactly what will happen, the second tap sends.
// Nothing here uses the browser's confirm(), which browsers can silently suppress. If this script doesn't
// load, the buttons simply don't fire, so they fail safe.
let armed = null;

function disarm() {
  if (!armed) return;
  clearTimeout(armed.timer);
  if (armed.btn.isConnected) { armed.btn.textContent = armed.label; armed.btn.classList.remove('armed'); }
  armed = null;
}

document.addEventListener('click', e => {
  const btn = e.target.closest('[data-arm]');
  if (armed && btn !== armed.btn) disarm();
  if (!btn) return;
  if (!armed) {
    armed = {btn, label: btn.textContent, at: Date.now(), timer: setTimeout(disarm, 6000)};
    btn.textContent = btn.dataset.arm;
    btn.classList.add('armed');
  } else if (Date.now() - armed.at > 400) {   // a double-tap must not arm and send in one go
    disarm();
    htmx.trigger(btn, 'armed');
  }
});

document.addEventListener('keydown', e => { if (e.key === 'Escape') disarm(); });
// A swapped-in panel replaces the button (and its summary), so whatever was armed is stale.
document.body.addEventListener('htmx:afterSettle', () => { if (armed && !armed.btn.isConnected) disarm(); });

// The text box clears after a typed label is added (the server says so), but not when a preset button was used.
document.body.addEventListener('text-added', e => e.target.closest('form')?.reset());

// Click a sheet preview to look at it properly.
document.addEventListener('click', e => {
  const dialog = document.getElementById('zoom');
  if (!dialog) return;
  const open = e.target.closest('button.zoom');
  if (open) { dialog.querySelector('img').src = open.dataset.full; dialog.showModal(); }
  else if (e.target === dialog || e.target.closest('[data-close]')) dialog.close();  // backdrop or ✕; Esc works natively
});
