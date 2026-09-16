(() => {
  'use strict';

  // GALKA CLASSIC: Fibonacci/V2 interception is disabled.
  // The base terminal now uses the classic 8-level lower ladder.
  const status = document.getElementById('campaignStatus');
  if (!status) return;

  const normalizeCounter = () => {
    const text = String(status.textContent || '');
    const fixed = text.replace(/(\d+)\/4\b/g, '$1/8');
    if (fixed !== text) status.textContent = fixed;
  };

  new MutationObserver(normalizeCounter).observe(status, {
    childList: true,
    characterData: true,
    subtree: true,
  });
  normalizeCounter();
})();
