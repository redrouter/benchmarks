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
  const pickers = [...document.querySelectorAll('.picker')];
  const closeAll = except => pickers.forEach(picker => { if (picker !== except) toggle(picker, false); });
  function toggle(picker, open) {
    const button = picker.querySelector('.picker-button');
    const menu = picker.querySelector('.picker-menu');
    button.setAttribute('aria-expanded', String(open));
    menu.hidden = !open;
    if (open) (menu.querySelector('[aria-selected="true"]') || menu.querySelector('[role="option"]')).focus();
  }
  function sync(picker) {
    const select = picker.querySelector('select');
    picker.querySelectorAll('[role="option"]').forEach(option => {
      const chosen = option.dataset.value === select.value;
      option.setAttribute('aria-selected', String(chosen));
      if (chosen) picker.querySelector('.picker-button').innerHTML = option.innerHTML;
    });
  }
  function choose(picker, option) {
    const select = picker.querySelector('select');
    toggle(picker, false);
    picker.querySelector('.picker-button').focus();
    if (select.value === option.dataset.value) return;
    select.value = option.dataset.value;
    sync(picker);
    select.dispatchEvent(new Event('change'));
  }
  pickers.forEach(picker => {
    sync(picker);
    const button = picker.querySelector('.picker-button');
    const options = () => [...picker.querySelectorAll('[role="option"]')];
    button.addEventListener('click', () => {
      const open = button.getAttribute('aria-expanded') !== 'true';
      closeAll(picker);
      toggle(picker, open);
    });
    button.addEventListener('keydown', event => {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); closeAll(picker); toggle(picker, true); }
    });
    picker.querySelector('.picker-menu').addEventListener('click', event => {
      const option = event.target.closest('[role="option"]');
      if (option) choose(picker, option);
    });
    picker.querySelector('.picker-menu').addEventListener('keydown', event => {
      const list = options();
      const index = list.indexOf(document.activeElement);
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        list[(index + (event.key === 'ArrowDown' ? 1 : list.length - 1)) % list.length].focus();
      } else if (event.key === 'Home' || event.key === 'End') {
        event.preventDefault();
        list[event.key === 'Home' ? 0 : list.length - 1].focus();
      } else if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        if (index >= 0) choose(picker, list[index]);
      } else if (event.key === 'Escape' || event.key === 'Tab') {
        toggle(picker, false);
        if (event.key === 'Escape') button.focus();
      }
    });
  });
  document.addEventListener('click', event => { if (!event.target.closest('.picker')) closeAll(); });
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
    tooltip.textContent = target.getAttribute('aria-label');
    tooltip.hidden = false;
    tooltip.style.left = `${Math.max(8, Math.min(x, innerWidth - tooltip.offsetWidth - 8))}px`;
    tooltip.style.top = `${Math.max(8, Math.min(y + 12, innerHeight - tooltip.offsetHeight - 8))}px`;
  }
  document.addEventListener('pointerover', event => { if (event.target.matches('svg [aria-label]')) show(event.target, event.clientX, event.clientY); });
  document.addEventListener('pointerout', event => { if (event.target.matches('svg [aria-label]')) tooltip.hidden = true; });
  document.addEventListener('focusin', event => { if (event.target.matches('svg [aria-label]')) { const box = event.target.getBoundingClientRect(); show(event.target, box.x, box.y); } });
  document.addEventListener('focusout', () => { tooltip.hidden = true; });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') tooltip.hidden = true; });
})();
