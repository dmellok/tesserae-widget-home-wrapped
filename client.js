/* Home Wrapped: a story slide painted whole by GPT Image, shown edge to edge. The painter leaves
   the bottom eighth plain; a band fills it with one of the year's events ("In 2026, you flicked
   the Kitchen light on 8,678 times!") and, under it, the figures the slide was painted from. Before the first painting (or without Home Assistant or an AI
   key) a bundled sample slide shows, with a note saying so. render() waits for the picture and
   the face, so a panel's screenshot never catches either half loaded. */

const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

const FACE = 'Home Wrapped Inter Tight';
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

const CSS = `
:host { display: block; width: 100%; height: 100%; }
.hw { position: relative; width: 100%; height: 100%; overflow: hidden; background: #111; container-type: size; font-family: '${FACE}', 'Inter Tight', sans-serif; }
.pic { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
.band { position: absolute; left: 0; right: 0; bottom: 0; height: 12.5cqh; box-sizing: border-box; display: flex; flex-direction: column; justify-content: center; gap: .6cqh; padding: 1cqh 4cqmin; text-align: center; background: var(--bg); color: var(--fg); border-top: .5cqmin solid var(--hi); }
.ev { font-size: min(3.9cqw, 3.4cqh); font-weight: 800; line-height: 1.12; text-wrap: balance; letter-spacing: -.01em; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.ev .n { color: var(--hi); }
.figs { font-size: min(2.1cqw, 1.8cqh); font-weight: 600; line-height: 1.25; opacity: .78; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.figs b { font-weight: 800; }
.band.solo .figs { font-size: min(2.8cqw, 2.4cqh); white-space: normal; opacity: 1; }
.note { position: absolute; left: 3cqmin; right: 3cqmin; bottom: calc(var(--lift, 0px) + 3cqmin); background: #FFFBF0; color: #111; border: .4cqmin solid #111; font-size: min(2.6cqw, 3.2cqh); font-weight: 700; line-height: 1.3; padding: 1.2cqmin 2cqmin; }
`;

async function useFace() {
  if (typeof document === 'undefined') return;
  if (!document.getElementById('home-wrapped-face')) {
    const url = new URL('./static/fonts/inter_tight-normal-400_900.woff2', import.meta.url).href;
    const el = document.createElement('style');
    el.id = 'home-wrapped-face';
    el.textContent = `@font-face { font-family: '${FACE}'; src: url('${url}') format('woff2'); font-weight: 400 900; }`;
    document.head.appendChild(el);
  }
  try {
    await Promise.race([
      Promise.all([`600 20px '${FACE}'`, `800 20px '${FACE}'`].map((f) => document.fonts.load(f, 'Painted 0123456789'))),
      new Promise((res) => setTimeout(res, 3000)),
    ]);
  } catch (e) { /* the fallback face still reads */ }
}

function loaded(img, ms) {
  if (!img) return Promise.resolve();
  const wait = new Promise((res) => setTimeout(res, ms));
  const ready = typeof img.decode === 'function'
    ? img.decode().catch(() => {})
    : new Promise((res) => { img.onload = res; img.onerror = res; });
  return Promise.race([ready, wait]);
}

// The band's inks, per palette: a ground near the painter's own, and one bright ink for the count.
const INKS = {
  wrapped: { bg: '#0D0716', fg: '#FFFFFF', hi: '#C6F432' },
  spectra: { bg: '#000000', fg: '#FFFFFF', hi: '#FFE600' },
  gray: { bg: '#000000', fg: '#FFFFFF', hi: '#FFFFFF' },
};

function eventLine(ev) {
  if (!ev || !ev.text) return '';
  const text = String(ev.text);
  const n = String(ev.n || '');
  const at = n ? text.lastIndexOf(n) : -1;
  if (at < 0) return `<div class="ev">${esc(text)}</div>`;
  return `<div class="ev">${esc(text.slice(0, at))}<span class="n">${esc(n)}</span>${esc(text.slice(at + n.length))}</div>`;
}

function band(ev, figs) {
  const line = eventLine(ev);
  if (!line && !figs) return '';
  return `<div class="band${line ? '' : ' solo'}">${line}${figs ? `<div class="figs">${figs}</div>` : ''}</div>`;
}

function day(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || '');
  return m ? `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]}` : '';
}

export default async function render(shadow, ctx) {
  const d = (ctx && ctx.data) || {};
  const ink = INKS[d.palette] || INKS.wrapped;
  const foot = (ev, figs) => (d.band === false ? '' : band(ev, figs));
  let body;
  if (d.painted && d.painted.url) {
    const list = (d.painted.figures || []).map(esc).join(' · ');
    const when = day(d.painted.when);
    const figs = d.strip && list ? `${when ? `<b>Painted ${esc(when)}:</b> ` : ''}${list}` : '';
    body = `<img class="pic" alt="" src="${esc(d.painted.url)}">${foot(d.event, figs)}`;
  } else {
    const src = new URL(`./static/samples/${d.sample || 'summary-wrapped-port.jpg'}`, import.meta.url).href;
    const note = d.error || d.note || 'A sample.';
    const low = foot(d.event, '');
    body = `<img class="pic" alt="" src="${esc(src)}">${low}<div class="note"${low ? ' style="--lift: 12.5cqh"' : ''}>${esc(note)}</div>`;
  }
  shadow.innerHTML = `<style>${CSS}</style><div class="hw" style="--bg: ${ink.bg}; --fg: ${ink.fg}; --hi: ${ink.hi}">${body}</div>`;
  await Promise.all([useFace(), loaded(shadow.querySelector('img.pic'), 8000)]);
}
