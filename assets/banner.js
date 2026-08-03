// Dismissible beta banner, shared by all pages (no build step — plain include).
(function () {
    'use strict';
    var KEY = 'swisslaw_beta_dismissed';
    try {
        if (localStorage.getItem(KEY)) return;
    } catch (e) { /* private mode — show the banner, dismissal won't persist */ }

    function insert() {
        var bar = document.createElement('div');
        bar.setAttribute('role', 'status');
        bar.style.cssText = 'background:#fff8e1;border-bottom:1px solid #e6d9a8;' +
            'color:#6b5d1f;font-size:0.82rem;padding:0.45rem 2.5rem 0.45rem 1rem;' +
            'text-align:center;position:relative;line-height:1.4;' +
            'font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;';
        bar.innerHTML = '<b>Beta</b> — data and APIs may still change. ' +
            'Feedback welcome on <a href="https://github.com/benjamin-arfa/swiss-law" ' +
            'style="color:#6b5d1f">GitHub</a>.';
        var close = document.createElement('button');
        close.textContent = '✕';
        close.setAttribute('aria-label', 'Dismiss beta banner');
        close.style.cssText = 'position:absolute;right:0.6rem;top:50%;' +
            'transform:translateY(-50%);background:none;border:none;cursor:pointer;' +
            'color:#6b5d1f;font-size:0.9rem;padding:0.2rem;';
        close.addEventListener('click', function () {
            try { localStorage.setItem(KEY, '1'); } catch (e) {}
            bar.remove();
        });
        bar.appendChild(close);
        document.body.prepend(bar);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', insert);
    } else {
        insert();
    }
})();
