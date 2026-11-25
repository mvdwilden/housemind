for (const b of document.querySelectorAll('.views button')) b.addEventListener('click', () => {
  for (const o of document.querySelectorAll('.views button')) o.setAttribute('aria-pressed', String(o === b));
  for (const v of document.querySelectorAll('[data-view]')) v.hidden = v.dataset.view !== b.dataset.show;
});
