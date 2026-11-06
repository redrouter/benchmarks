(() => {
  const canvas = document.getElementById("aurora");
  if (!canvas) return;
  const container = canvas.parentElement;
  const panel = canvas.dataset.aurora === "panel";
  const density = Number(canvas.dataset.density) || 72;
  const gain = Number(canvas.dataset.intensity) || 1;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const still = matchMedia("(prefers-reduced-motion: reduce)");
  const wide = matchMedia("(min-width: 900px)");
  const css = getComputedStyle(canvas);
  const colors = Array.from({ length: 12 }, (_, i) => css.getPropertyValue(`--aurora-${i}`).trim() || "#b0161c");
  const word = "redrouter";
  const glyphs = "%:·";
  const sheets = [
    { x: 0.10, width: 0.090, depth: 0.72, phase: 0.2 },
    { x: 0.40, width: 0.115, depth: 0.92, phase: 1.6 },
    { x: 0.72, width: 0.100, depth: 1.00, phase: 2.8 },
    { x: 1.04, width: 0.125, depth: 0.80, phase: 4.0 },
  ];
  const smooth = (x) => {
    const t = Math.min(1, Math.max(0, x));
    return t * t * (3 - 2 * t);
  };
  let width = 0, height = 0, cols = 0, rows = 0, cw = 0, ch = 0;
  let last = -Infinity, raf = 0;
  let pageHeight = 0, heroHeight = 0, sheetWidth = 0, sheetLeft = 0;
  let viewTop = 0, firstRow = 0, rowShift = 0, gridLeft = 0, columnOrigin = 0;
  let overscan = 0, placedTop = 0;
  let ready = false;
  const hero = panel ? container : document.querySelector('.landing-hero') || container;
  const levels = colors.map(() => []);
  const counts = new Uint32Array(colors.length);
  let sheetRows = new Float64Array(0), rowLetter = new Uint8Array(0), rowOffset = new Float32Array(0);
  let glyph = new Uint8Array(0), field = new Float32Array(0);
  let settled = new Float32Array(0), flowVelocity = new Float32Array(0);
  let fieldTime = 0;
  let pointerX = 0, pointerY = 0, pointerActive = false;
  let orbX = 0, orbY = 0, velocityX = 0, velocityY = 0;
  let opacity = 0, physicsTime = 0, seeded = false;
  let sampledX = 0, sampledY = 0, speed = 0, visibility = 1;
  let directionX = 0, directionY = 0, curl = 0, ribbon = 0;
  const trail = new Float64Array(40 * 5);
  const wake = new Float64Array(16 * 7);
  let nextTrail = 0, trailCount = 0, trailTime = -Infinity;
  let nextWake = 0, wakeTime = -Infinity;
  wake.fill(-Infinity);

  function point(event) {
    const box = canvas.getBoundingClientRect();
    const x = event.clientX - box.left, y = event.clientY - box.top;
    if (!box.width || !box.height || x < 0 || y < 0 || x >= box.width || y >= box.height) return false;
    const px = x * width / box.width, py = y * height / box.height + placedTop;
    if (pointerActive && px === pointerX && py === pointerY) return true;
    if (!seeded || (!pointerActive && opacity < 0.01)) {
      orbX = px;
      orbY = py;
      sampledX = px;
      sampledY = py;
      velocityX = velocityY = speed = 0;
      trailCount = nextTrail = 0;
      curl = ribbon = 0;
      directionX = directionY = 0;
      seeded = true;
    }
    pointerX = px;
    pointerY = py;
    pointerActive = true;
    return true;
  }

  window.addEventListener("pointermove", (event) => {
    if (document.hidden || still.matches || !wide.matches || event.pointerType === "touch") return;
    if (!point(event)) pointerActive = false;
  }, { passive: true });
  window.addEventListener("pointerleave", () => { pointerActive = false; });
  window.addEventListener("pointercancel", () => { pointerActive = false; });
  window.addEventListener("blur", () => { pointerActive = false; });

  function integrate(t) {
    const dt = Math.min(0.1, Math.max(0, (t - physicsTime) / 1000));
    physicsTime = t;
    if (!seeded) return;
    const dx = pointerX - sampledX, dy = pointerY - sampledY;
    const distance = Math.hypot(dx, dy);
    if (dt > 0) {
      speed += (distance / (sheetWidth * dt) - speed) * (1 - Math.exp(-dt / 0.085));
      sampledX = pointerX;
      sampledY = pointerY;
    }
    curl *= Math.exp(-dt / 0.48);
    if (distance > ch * 0.08) {
      const nx = dx / distance, ny = dy / distance;
      const turn = Math.atan2(directionX * ny - directionY * nx, directionX * nx + directionY * ny);
      curl = Math.max(-1.5, Math.min(1.5, curl + turn * 2 * smooth(speed / 0.35)));
      directionX = nx;
      directionY = ny;
    }
    const show = 1 - smooth((speed - 8) / 4);
    visibility += (show - visibility) * (1 - Math.exp(-dt / (show < visibility ? 0.055 : 0.24)));
    ribbon += (smooth((speed - 0.035) / 0.65) - ribbon) * (1 - Math.exp(-dt / 0.16));
    opacity += ((pointerActive ? 1 : 0) - opacity) * (1 - Math.exp(-dt / (pointerActive ? 0.22 : 0.26)));
    const stiffness = 52 + ribbon * 500, damping = 11 + ribbon * 22;
    const curiosity = (1 - ribbon) * ch * 0.24;
    const targetX = pointerX + Math.sin(t / 830) * curiosity;
    const targetY = pointerY + Math.cos(t / 1070) * curiosity;
    const steps = Math.max(1, Math.ceil(dt * 240)), step = dt / steps;
    for (let i = 0; i < steps; i++) {
      velocityX += ((targetX - orbX) * stiffness - velocityX * damping) * step;
      velocityY += ((targetY - orbY) * stiffness - velocityY * damping) * step;
      orbX += velocityX * step;
      orbY += velocityY * step;
    }
    const previous = (nextTrail + trail.length - 5) % trail.length;
    if (t - trailTime >= 24 && opacity * visibility > 0.02 &&
        (!trailCount || Math.hypot(orbX - trail[previous], orbY - trail[previous + 1]) > ch * 0.4)) {
      trail[nextTrail] = orbX;
      trail[nextTrail + 1] = orbY;
      trail[nextTrail + 2] = t;
      trail[nextTrail + 3] = opacity * visibility;
      trail[nextTrail + 4] = ribbon;
      nextTrail = (nextTrail + 5) % trail.length;
      trailCount = Math.min(trailCount + 1, trail.length / 5);
      trailTime = t;
    }
    if (Math.abs(curl) > 0.12 && opacity * visibility > 0.05 && t - wakeTime >= 95) {
      wake[nextWake] = orbX;
      wake[nextWake + 1] = orbY;
      wake[nextWake + 2] = t;
      wake[nextWake + 3] = opacity * visibility * ribbon;
      const drift = ch * 5 / Math.max(ch * 5, Math.hypot(velocityX, velocityY));
      wake[nextWake + 4] = velocityX * drift;
      wake[nextWake + 5] = velocityY * drift;
      wake[nextWake + 6] = curl;
      nextWake = (nextWake + 7) % wake.length;
      wakeTime = t;
    }
  }

  function splat(px, py, radius, dragX, dragY, spin, strength, light) {
    py -= firstRow * ch;
    const reach = radius * 2.5;
    const x0 = Math.max(0, Math.floor((px - reach - gridLeft) / cw));
    const x1 = Math.min(cols - 1, Math.ceil((px + reach - gridLeft) / cw));
    const y0 = Math.max(-1, Math.floor((py - reach) / ch));
    const y1 = Math.min(rows - 1, Math.ceil((py + reach) / ch));
    for (let y = y0; y <= y1; y++) {
      for (let x = x0; x <= x1; x++) {
        const dx = ((x + 0.5) * cw + gridLeft - px) / radius;
        const dy = ((y + 0.5) * ch - py) / radius;
        const weight = Math.exp(-(dx * dx + dy * dy)) * strength;
        const cell = ((y + 1) * cols + x) * 3;
        field[cell] += weight * (dragX - dy * spin);
        field[cell + 1] += weight * (dragY + dx * spin);
        field[cell + 2] += weight * light;
      }
    }
  }

  function spline(a, b, c, d, u) {
    return b + 0.5 * u * (c - a + u * (2 * a - 5 * b + 4 * c - d + u * (3 * (b - c) + d - a)));
  }

  function orbField(t) {
    field.fill(0);
    if (still.matches || !wide.matches) return;
    for (let n = 0; n < trailCount - 1; n++) {
      const b = (nextTrail + trail.length - (trailCount - n) * 5) % trail.length;
      const a = n ? (b + trail.length - 5) % trail.length : b;
      const c = (b + 5) % trail.length;
      const d = n + 2 < trailCount ? (c + 5) % trail.length : c;
      const age = (t - trail[c + 2]) / 850;
      if (age >= 1) continue;
      const length = Math.hypot(trail[c] - trail[b], trail[c + 1] - trail[b + 1]);
      const samples = Math.min(8, Math.max(1, Math.ceil(length / ch)));
      for (let j = 0; j < samples; j++) {
        const u = (j + 0.5) / samples;
        const life = Math.max(0, (t - (trail[b + 2] + (trail[c + 2] - trail[b + 2]) * u)) / 850);
        const strength = trail[b + 3] * trail[b + 4] * (1 - smooth(life)) *
          Math.min(1, length / (samples * ch * 2.3));
        splat(spline(trail[a], trail[b], trail[c], trail[d], u),
          spline(trail[a + 1], trail[b + 1], trail[c + 1], trail[d + 1], u),
          ch * (1.25 + life * 0.65),
          (trail[c] - trail[b]) / Math.max(1, length) * ch * 1.3,
          (trail[c + 1] - trail[b + 1]) / Math.max(1, length) * ch * 1.3,
          0, strength, 0.19);
      }
    }
    for (let i = 0; i < wake.length; i += 7) {
      const age = (t - wake[i + 2]) / 1250;
      if (age < 0 || age >= 1) continue;
      const drift = 0.42 * (1 - Math.exp(-age * 2));
      const turn = age * 2.5 * wake[i + 6];
      const px = wake[i] + wake[i + 4] * drift - Math.sin(turn) * ch * age;
      const py = wake[i + 1] + wake[i + 5] * drift + (Math.cos(turn) - 1) * ch * age;
      splat(px, py, ch * (1.6 + age * 1.2),
        wake[i + 4] * 0.025, wake[i + 5] * 0.025,
        ch * wake[i + 6] * 1.6, wake[i + 3] * (1 - smooth(age)) * 0.32, 0.065);
    }
    if (opacity > 0.001) {
      const drag = ch * 22 / Math.max(ch * 22, Math.hypot(velocityX, velocityY));
      const breath = Math.sin(t / 740);
      splat(orbX, orbY, ch * (1.9 - ribbon * 0.35 + breath * 0.13),
        velocityX * drag * 0.055, velocityY * drag * 0.055,
        ch * (0.32 * Math.sin(t / 960) + curl * 1.2), opacity * visibility,
        0.22 + breath * 0.025);
    }
    const dt = Math.min(0.1, Math.max(0, (t - fieldTime) / 1000));
    fieldTime = t;
    const decay = Math.exp(-9 * dt), c = Math.cos(12 * dt), s = Math.sin(12 * dt) / 12;
    const lightFollow = 1 - Math.exp(-dt / 0.16);
    for (let i = 0; i < field.length; i += 3) {
      for (let axis = 0; axis < 2; axis++) {
        const k = i + axis, offset = settled[k] - field[k], velocity = flowVelocity[k];
        settled[k] = field[k] + decay * (offset * c + (velocity + 9 * offset) * s);
        flowVelocity[k] = decay * (velocity * c - (9 * velocity + 225 * offset) * s);
      }
      settled[i + 2] += (field[i + 2] - settled[i + 2]) * lightFollow;
    }
  }

  function syncScroll() {
    if (panel) return;
    const top = Math.max(0, Math.min(pageHeight - height, window.scrollY - overscan));
    if (pointerActive) {
      pointerY += top - viewTop;
      sampledY += top - viewTop;
    }
    viewTop = top;
    const next = Math.floor(viewTop / ch);
    const cells = (next - firstRow) * cols * 3;
    if (cells) {
      if (Math.abs(cells) >= settled.length) {
        settled.fill(0);
        flowVelocity.fill(0);
      } else if (cells > 0) {
        settled.copyWithin(0, cells);
        flowVelocity.copyWithin(0, cells);
        settled.fill(0, settled.length - cells);
        flowVelocity.fill(0, flowVelocity.length - cells);
      } else {
        settled.copyWithin(-cells, 0);
        flowVelocity.copyWithin(-cells, 0);
        settled.fill(0, 0, -cells);
        flowVelocity.fill(0, 0, -cells);
      }
    }
    firstRow = next;
    rowShift = viewTop - firstRow * ch;
  }

  function resize() {
    if (document.hidden) return;
    if (panel && !wide.matches) {
      cancelAnimationFrame(raf);
      raf = 0;
      ready = false;
      return;
    }
    const box = canvas.getBoundingClientRect();
    if (!box.width || !box.height) return;
    pageHeight = container.getBoundingClientRect().height;
    heroHeight = hero.getBoundingClientRect().height;
    overscan = parseFloat(css.getPropertyValue("--aurora-overscan")) || 0;
    const dpr = Math.min(devicePixelRatio || 1, 3);
    if (ready && width === box.width && height === box.height && canvas.width === Math.round(width * dpr)) {
      syncScroll();
      requestFrame();
      return;
    }
    width = box.width;
    height = box.height;
    sheetWidth = panel ? width : width / 2;
    sheetLeft = panel ? 0 : width - sheetWidth;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    const size = Math.min((wide.matches ? sheetWidth : width) / density, height / 64);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.font = `${size}px "Geist Mono", ui-monospace, Menlo, monospace`;
    ctx.textBaseline = "top";
    cw = ctx.measureText("M").width;
    ch = size * 1.3;
    columnOrigin = Math.floor(sheetLeft / cw);
    gridLeft = sheetLeft - columnOrigin * cw;
    cols = Math.ceil(width / cw);
    rows = Math.ceil(height / ch) + 1;
    const capacity = cols * (rows + 1);
    for (let i = 0; i < levels.length; i++) levels[i] = new Uint32Array(capacity);
    glyph = new Uint8Array(capacity);
    field = new Float32Array(capacity * 3);
    settled = new Float32Array(capacity * 3);
    flowVelocity = new Float32Array(capacity * 3);
    sheetRows = new Float64Array((rows + 1) * sheets.length * 3);
    rowLetter = new Uint8Array(rows + 1);
    rowOffset = new Float32Array(rows + 1);
    wakeTime = -Infinity;
    pointerActive = seeded = false;
    opacity = speed = 0;
    visibility = 1;
    trailCount = nextTrail = 0;
    trailTime = -Infinity;
    curl = ribbon = 0;
    directionX = directionY = 0;
    wake.fill(-Infinity);
    fieldTime = physicsTime = performance.now();
    ready = true;
    syncScroll();
    last = -Infinity;
    requestFrame();
  }

  function draw(t) {
    if (!panel && placedTop !== viewTop) {
      placedTop = viewTop;
      canvas.style.transform = `translateY(${viewTop}px)`;
    }
    ctx.clearRect(0, 0, width, height);
    counts.fill(0);
    const time = t / 1000;
    orbField(t);
    const tick = Math.floor(t / 80);
    const active = seeded && wide.matches && !still.matches;
    const middle = Math.max(ch, pageHeight - 2 * heroHeight);
    const join = sheetLeft + sheetWidth * (0.26 + 0.045 * Math.sin(8 - time * 0.18));
    const tangent = sheetWidth / heroHeight * (-0.46 + 0.36 * Math.cos(8 - time * 0.18));
    for (let y = -1; y < rows; y++) {
      const documentY = (firstRow + y) * ch;
      const bottom = !panel && pageHeight > heroHeight && documentY > pageHeight - heroHeight;
      const v = panel ? 1 - documentY / height : (bottom ? pageHeight - documentY : documentY) / heroHeight;
      const merge = smooth((v - 0.22) / 0.64);
      const spread = 1 - merge;
      const trunk = 0.72 - 0.46 * v + 0.045 * Math.sin(v * 8 - time * 0.18);
      const progress = Math.min(1, Math.max(0, (documentY - heroHeight) / middle));
      const ease = smooth(progress);
      const thread = join + (width - 2 * join) * ease +
        tangent * middle * progress * (1 - progress) * (1 - 2 * progress);
      for (let s = 0; s < sheets.length; s++) {
        const sheet = sheets[s];
        const phase = v * 12 + sheet.phase + time * 0.22;
        const wave = 0.065 * Math.sin(phase) + 0.018 * Math.sin(phase * 2.1 - time * 0.12);
        const row = ((y + 1) * sheets.length + s) * 3;
        if (!panel && documentY >= heroHeight && !bottom) {
          sheetRows[row] = thread / width;
          sheetRows[row + 1] = 0.085 * sheetWidth / width;
          sheetRows[row + 2] = 1.12;
        } else {
          const center = sheetLeft + ((sheet.x - 0.32 * v + wave) * spread + trunk * merge) * sheetWidth;
          sheetRows[row] = (bottom ? width - center : center) / width;
          sheetRows[row + 1] = (sheet.width * (0.78 + 0.22 * Math.cos(phase)) * spread + 0.085 * merge) * sheetWidth / width;
          sheetRows[row + 2] = sheet.depth * (1.08 + 0.18 * Math.sin(phase * 0.7)) * spread + 1.12 * merge;
        }
      }
    }
    for (let y = -1; y < rows; y++) {
      const documentY = (firstRow + y) * ch;
      const mirror = panel ? 0 : smooth((documentY - heroHeight) / middle);
      for (let x = 0; x < cols; x++) {
        const u = (x * cw + gridLeft) / width;
        const cell = (y + 1) * cols + x;
        const edge = panel ? smooth(u / 0.08) * smooth((1 - u) / 0.12) :
          documentY <= heroHeight ? smooth((u - 0.5) / 0.11) :
          documentY >= pageHeight - heroHeight ? smooth((0.5 - u) / 0.11) : 1;
        const fade = active ? edge : 0;
        const sampleU = u - Math.tanh(settled[cell * 3] / ch) * ch * fade / width;
        const sampleY = Math.max(0, Math.min(rows, y + 1 - Math.tanh(settled[cell * 3 + 1] / ch) * fade));
        const row = Math.floor(sampleY), blend = sampleY - row;
        const a = row * sheets.length * 3, b = Math.min(rows, row + 1) * sheets.length * 3;
        let intensity = 0;
        for (let s = 0; s < sheets.length * 3; s += 3) {
          const center = sheetRows[a + s] + (sheetRows[b + s] - sheetRows[a + s]) * blend;
          const breadth = sheetRows[a + s + 1] + (sheetRows[b + s + 1] - sheetRows[a + s + 1]) * blend;
          const depth = sheetRows[a + s + 2] + (sheetRows[b + s + 2] - sheetRows[a + s + 2]) * blend;
          const d = (sampleU - center) / breadth;
          const face = d < 0 ? Math.exp(-((-d / (0.22 + 0.78 * mirror)) ** (2 - 0.5 * mirror))) :
            Math.exp(-((d / (1 - 0.78 * mirror)) ** (1.5 + 0.5 * mirror)));
          intensity = Math.max(intensity, face * depth);
        }
        intensity *= edge;
        intensity *= 0.94 + 0.06 * Math.sin((x - columnOrigin) * 2.4 + (firstRow + y) * 1.7 + time * 0.6);
        glyph[cell] = 0;
        if (active) {
          const hash = (Math.imul((firstRow + y) * cols + x + 1, 374761393) ^ Math.imul(tick, 668265263)) >>> 0;
          const influence = Math.min(0.32, settled[cell * 3 + 2]) * fade;
          intensity += (1 - Math.min(1, intensity)) * influence;
          if (influence > 0.08 && hash % 11 < 3) glyph[cell] = 2 + hash % 2;
          else if (influence > 0.24 && hash % 23 === 0) glyph[cell] = 1;
        }
        const level = Math.round(Math.min(1, intensity * gain) * 11);
        if (level) levels[level][counts[level]++] = cell;
      }
    }
    for (let y = 0; y <= rows; y++) {
      const hash = Math.imul(firstRow + y + 1, 2654435761) >>> 0;
      const shift = time * (1.5 + (hash >>> 8) % 25 / 10) * (hash & 1 ? 1 : -1);
      const whole = Math.floor(shift);
      rowLetter[y] = ((whole % word.length) + word.length) % word.length;
      rowOffset[y] = (shift - whole) * cw;
    }
    for (let level = 1; level < levels.length; level++) {
      ctx.fillStyle = colors[level];
      const cells = levels[level];
      for (let n = 0; n < counts[level]; n++) {
        const cell = cells[n], x = cell % cols, y = Math.floor(cell / cols) - 1;
        if (glyph[cell]) ctx.fillText(glyphs[glyph[cell] - 1], x * cw + gridLeft, y * ch - rowShift);
        else ctx.fillText(word[((x - columnOrigin - rowLetter[y + 1]) % word.length + word.length) % word.length], x * cw + gridLeft + rowOffset[y + 1], y * ch - rowShift);
      }
    }
  }

  function requestFrame() {
    if (ready && !document.hidden && !(panel && !wide.matches) && !raf) raf = requestAnimationFrame(frame);
  }

  function frame(t) {
    raf = 0;
    if (document.hidden || (panel && !wide.matches)) return;
    syncScroll();
    if (still.matches) { draw(12000); return; }
    if (t - last >= 1000 / (wide.matches && !panel ? 60 : 30) - 0.5) {
      last = t;
      if (wide.matches) integrate(t);
      draw(t);
    }
    requestFrame();
  }

  if (!panel) window.addEventListener("scroll", requestFrame, { passive: true });
  if (panel) {
    const palette = () => {
      colors.forEach((_, i) => { colors[i] = css.getPropertyValue(`--aurora-${i}`).trim() || "#b0161c"; });
      requestFrame();
    };
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", palette);
    new MutationObserver(palette).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  }
  document.addEventListener("visibilitychange", () => {
    cancelAnimationFrame(raf);
    raf = 0;
    pointerActive = false;
    if (!document.hidden) {
      physicsTime = fieldTime = performance.now();
      last = -Infinity;
      resize();
    }
  });
  document.fonts.ready.then(() => {
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);
    observer.observe(container);
    observer.observe(hero);
    still.addEventListener("change", () => {
      seeded = pointerActive = false;
      opacity = speed = 0;
      trailCount = nextTrail = 0;
      wake.fill(-Infinity);
      settled.fill(0);
      flowVelocity.fill(0);
      requestFrame();
    });
    wide.addEventListener("change", () => { ready = false; resize(); });
    window.addEventListener("resize", resize, { passive: true });
    resize();
  });
})();
