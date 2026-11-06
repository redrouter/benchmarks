(() => {
  const model = document.querySelector('#model-selector');
  const region = document.querySelector('#region-selector');
  const params = new URLSearchParams(location.search);
  const validOption = (select, value) => select && [...select.options].some(option => option.value === value);
  if (validOption(region, params.get('region'))) region.value = params.get('region');
  if (model?.dataset.navigate === 'false' && validOption(model, params.get('model'))) model.value = params.get('model');
  function select() {
    document.querySelectorAll('.selection').forEach(section => {
      section.hidden = (model && section.dataset.model !== model.value) || section.dataset.region !== region.value;
    });
    if (region) params.set('region', region.value);
    if (model && model.dataset.navigate === 'false') params.set('model', model.value);
    history.replaceState(null, '', `${location.pathname}?${params}`);
  }
  model?.addEventListener('change', () => {
    if (model.dataset.navigate === 'true') location.href = `/models/${model.value}/?region=${region.value}`;
    else select();
  });
  region?.addEventListener('change', select);
  if (region) select();
  document.querySelectorAll('.period-selector').forEach(selector => selector.addEventListener('change', () => {
    selector.closest('.selection').querySelectorAll('.history-period').forEach(period => { period.hidden = period.dataset.days !== selector.value; });
  }));
  document.querySelectorAll('[data-sort]').forEach(button => button.addEventListener('click', () => {
    const table = button.closest('table');
    const th = button.closest('th');
    const index = Number(button.dataset.sort);
    const direction = th.getAttribute('aria-sort') === 'ascending' ? -1 : 1;
    table.querySelectorAll('th').forEach(header => header.setAttribute('aria-sort', 'none'));
    th.setAttribute('aria-sort', direction === 1 ? 'ascending' : 'descending');
    const rows = [...table.tBodies[0].rows];
    rows.sort((a, b) => {
      const av = a.cells[index]?.dataset.value ?? a.cells[index]?.textContent.trim() ?? '';
      const bv = b.cells[index]?.dataset.value ?? b.cells[index]?.textContent.trim() ?? '';
      if (!av || av === '—') return !bv || bv === '—' ? 0 : 1;
      if (!bv || bv === '—') return -1;
      return direction * (Number.isFinite(Number(av)) && Number.isFinite(Number(bv)) ? Number(av) - Number(bv) : av.localeCompare(bv));
    });
    table.tBodies[0].append(...rows);
  }));
  const tooltip = document.querySelector('#tooltip');
  function show(target, x, y) {
    tooltip.textContent = target.dataset.tooltip;
    tooltip.hidden = false;
    tooltip.style.left = `${Math.max(8, Math.min(x, innerWidth - tooltip.offsetWidth - 8))}px`;
    tooltip.style.top = `${Math.max(8, Math.min(y + 12, innerHeight - tooltip.offsetHeight - 8))}px`;
  }
  document.addEventListener('pointerover', event => { if (event.target.dataset.tooltip) show(event.target, event.clientX, event.clientY); });
  document.addEventListener('pointerout', event => { if (event.target.dataset.tooltip) tooltip.hidden = true; });
  document.addEventListener('focusin', event => { if (event.target.dataset.tooltip) { const box = event.target.getBoundingClientRect(); show(event.target, box.x, box.y); } });
  document.addEventListener('focusout', () => { tooltip.hidden = true; });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') tooltip.hidden = true; });
})();
