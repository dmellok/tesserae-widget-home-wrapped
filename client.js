/* Home Wrapped: a story slide painted whole by GPT Image, shown edge to edge, with the figures it
   was painted from along its foot. Before the first painting (or without Home Assistant or an AI
   key) a bundled sample slide shows, with a note saying so. render() waits for the picture and
   the face, so a panel's screenshot never catches either half loaded. */

const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

const FACE = 'Home Wrapped Inter Tight';
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

const CSS = `
:host { display: block; width: 100%; height: 100%; }
.hw { position: relative; width: 100%; height: 100%; overflow: hidden; background: #111; container-type: size; font-family: '${FACE}', 'Inter Tight', sans-serif; }
.pic { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
.strip { position: absolute; left: 0; right: 0; bottom: 0; background: #FFFBF0; color: #111; font-size: min(2.2cqw, 2.8cqh); font-weight: 600; line-height: 1.3; padding: 1cqmin 3cqmin; text-align: center; }
.strip b { font-weight: 800; }
.note { position: absolute; left: 3cqmin; right: 3cqmin; bottom: 3cqmin; background: #FFFBF0; color: #111; border: .4cqmin solid #111; font-size: min(2.6cqw, 3.2cqh); font-weight: 700; line-height: 1.3; padding: 1.2cqmin 2cqmin; }
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

function day(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || '');
  return m ? `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]}` : '';
}

export default async function render(shadow, ctx) {
  const d = (ctx && ctx.data) || {};
  let body;
  if (d.painted && d.painted.url) {
    const figs = (d.painted.figures || []).map(esc).join(' · ');
    const when = day(d.painted.when);
    const strip = d.strip && figs ? `<div class="strip">${when ? `<b>Painted ${esc(when)}:</b> ` : ''}${figs}</div>` : '';
    body = `<img class="pic" alt="" src="${esc(d.painted.url)}">${strip}`;
  } else {
    const src = new URL(`./static/samples/${d.sample || 'summary-wrapped-port.jpg'}`, import.meta.url).href;
    const note = d.error || d.note || 'A sample.';
    body = `<img class="pic" alt="" src="${esc(src)}"><div class="note">${esc(note)}</div>`;
  }
  shadow.innerHTML = `<style>${CSS}</style><div class="hw">${body}</div>`;
  await Promise.all([useFace(), loaded(shadow.querySelector('img.pic'), 8000)]);
}
