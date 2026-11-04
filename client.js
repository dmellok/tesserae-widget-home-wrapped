/* Home Wrapped: year-in-review cards. Every word and number is set here, from the year-to-date
   numbers the server read; the only pictures are GPT Image paintings (bundled ones under
   static/art, or the painting the server sends as a data URI). Sizes are in container units, so
   a card holds together from a phone-sized cell to a 13.3-inch panel, portrait or landscape.
   render() waits for the faces and the picture, so a panel's screenshot never catches either
   half loaded. */

const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

const DISPLAY = 'Home Wrapped Anton';
const TEXT = 'Home Wrapped Inter Tight';

/* Close to a Spectra 6 panel's own inks, so the colour lands where it was drawn. */
const INK = '#111111';
const PAPER = '#FFFBF0';
const RED = '#E8352B';
const YELLOW = '#F5C400';
const BLUE = '#1F4FD8';
const GREEN = '#2E9E44';
const ORANGE = '#F07A1A';

/* frame: the card's ground; blocks: [fill, text] in the order the card uses them. */
const PALETTES = {
  primary: { paper: PAPER, frame: RED, frameInk: INK, blocks: [[YELLOW, INK], [BLUE, '#fff'], [GREEN, '#fff'], [ORANGE, INK], [RED, '#fff']], dot: GREEN },
  sunset: { paper: PAPER, frame: ORANGE, frameInk: INK, blocks: [[RED, '#fff'], [YELLOW, INK], [INK, '#fff'], [RED, '#fff'], [YELLOW, INK]], dot: RED },
  sea: { paper: PAPER, frame: BLUE, frameInk: '#fff', blocks: [[GREEN, '#fff'], [YELLOW, INK], [INK, '#fff'], [GREEN, '#fff'], [YELLOW, INK]], dot: BLUE },
  gray: { paper: '#FFFFFF', frame: '#C9C9C9', frameInk: INK, blocks: [[INK, '#fff'], ['#6B6B6B', '#fff'], ['#9A9A9A', INK], [INK, '#fff'], ['#6B6B6B', '#fff']], dot: INK },
};

const GRAIN = "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='240' height='240'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='2' stitchTiles='stitch'/%3E%3CfeColorMatrix values='0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 .55 0'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")";

const CSS = `
:host { display: block; width: 100%; height: 100%; }
* { box-sizing: border-box; margin: 0; }
.card { position: relative; width: 100%; height: 100%; overflow: hidden; container-type: size; font-family: '${TEXT}', 'Inter Tight', sans-serif; color: ${INK}; }
.grain::after { content: ''; position: absolute; inset: 0; background-image: ${GRAIN}; mix-blend-mode: multiply; opacity: .10; pointer-events: none; }
.d { font-family: '${DISPLAY}', 'Anton', 'Impact', sans-serif; font-weight: 400; line-height: .92; letter-spacing: -.005em; text-transform: none; }
.pic { display: block; width: 100%; height: 100%; object-fit: cover; }
.gray .pic.file { filter: grayscale(1) contrast(1.08); }
.chip { position: absolute; right: 2.6cqmin; bottom: 2.6cqmin; background: ${PAPER}; color: ${INK}; font-size: 2.2cqmin; font-weight: 700; padding: .7cqmin 1.4cqmin; border: .35cqmin solid ${INK}; }

/* Summary */
.sum { display: grid; padding: 3.2cqmin; gap: 2.4cqmin; grid-template-rows: auto minmax(0, 1fr) auto auto; }
.sum .title { font-size: min(11.5cqw, 9cqh); white-space: nowrap; }
.sum .art { position: relative; min-height: 0; border: .55cqmin solid ${INK}; overflow: hidden; }
.sum .lists { background: ${PAPER}; border: .55cqmin solid ${INK}; display: grid; grid-template-columns: 1fr 1fr; }
.sum .list { padding: 2cqmin 2.4cqmin; }
.sum .list + .list { border-left: .4cqmin solid ${INK}; }
.sum h3 { font-size: min(5.2cqw, 4cqh); margin-bottom: 1.2cqmin; }
.row { display: flex; align-items: center; gap: 1.4cqmin; font-weight: 700; font-size: min(3.3cqw, 2.5cqh); line-height: 1.5; }
.row .n { flex: 0 0 auto; width: 1.55em; height: 1.55em; border-radius: 50%; display: grid; place-items: center; color: #fff; font-size: .74em; font-weight: 800; line-height: 1; }
.row .nm { flex: 1 1 auto; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.row .v { flex: 0 0 auto; font-weight: 600; opacity: .8; }
.sum .blocks { display: grid; grid-template-columns: 1fr 1fr; border: .55cqmin solid ${INK}; }
.blk { padding: 1.8cqmin 2.4cqmin 2cqmin; min-width: 0; }
.blk + .blk { border-left: .4cqmin solid ${INK}; }
.blk .lab { font-weight: 800; font-size: min(3.4cqw, 2.6cqh); }
.blk .big { font-size: min(13cqw, 10cqh); white-space: nowrap; }
.blk .big small { font-size: .46em; margin-left: .12em; }
@container (min-aspect-ratio: 5/4) {
  .sum { grid-template-columns: 44% 1fr; grid-template-rows: auto minmax(0, 1fr) auto; }
  .sum .title { grid-column: 1 / -1; font-size: min(8cqw, 12cqh); }
  .sum .art { grid-row: 2 / 4; }
  .sum h3 { font-size: min(3.6cqw, 6cqh); }
  .row { font-size: min(2.3cqw, 3.9cqh); }
  .blk .lab { font-size: min(2.3cqw, 3.8cqh); }
  .blk .big { font-size: min(8cqw, 13cqh); }
}

/* Big number */
.num { background: ${PAPER}; padding: 4.5cqmin; display: grid; grid-template-rows: auto auto auto minmax(0, 1fr) auto; }
.num .kick { font-size: 2.6cqmin; font-weight: 800; letter-spacing: .02em; }
.num .lab { font-size: min(8.5cqw, 7cqh); margin-top: 3cqmin; }
.num .big { white-space: nowrap; margin-top: 1cqmin; }
.num .unit { font-size: min(8cqw, 6.5cqh); margin-top: 1cqmin; }
.num .spot { position: relative; min-height: 0; display: flex; align-items: flex-end; justify-content: flex-end; }
.num .disc { position: relative; width: min(62cqw, 100%); aspect-ratio: 1; max-height: 100%; border-radius: 50%; overflow: hidden; border: .7cqmin solid ${INK}; }
.num .burst { position: absolute; width: 30cqmin; height: 30cqmin; left: 4cqmin; bottom: 6cqmin; clip-path: polygon(50% 0, 61% 30%, 93% 21%, 72% 47%, 100% 64%, 67% 68%, 69% 100%, 50% 77%, 31% 100%, 33% 68%, 0 64%, 28% 47%, 7% 21%, 39% 30%); }
.num .rate { font-size: min(4.2cqw, 3.4cqh); font-weight: 800; margin-top: 2cqmin; }
@container (min-aspect-ratio: 5/4) {
  .num { grid-template-columns: 1fr 42%; grid-template-rows: auto auto auto auto 1fr; column-gap: 4cqmin; }
  .num > :not(.spot) { grid-column: 1; }
  .num .spot { grid-column: 2; grid-row: 1 / -1; align-items: center; justify-content: center; }
  .num .disc { width: min(40cqw, 80cqh); }
  .num .lab { font-size: min(5.5cqw, 9cqh); }
  .num .unit { font-size: min(5cqw, 8cqh); }
  .num .rate { font-size: min(2.6cqw, 4.6cqh); }
}

/* Top five */
.top { padding: 3.6cqmin; display: grid; grid-template-rows: auto minmax(0, 1fr); gap: 2.6cqmin; }
.top .head { font-size: min(14cqw, 10cqh); }
.top .rows { display: grid; gap: 1.6cqmin; grid-template-rows: 2.1fr repeat(4, 1fr); min-height: 0; }
.top .r { display: flex; align-items: center; gap: 3cqmin; padding: 0 3cqmin; border: .55cqmin solid ${INK}; min-height: 0; overflow: hidden; }
.top .r .i { font-size: min(11cqw, 8cqh); }
.top .r .nm { flex: 1 1 auto; font-size: min(7cqw, 5.5cqh); min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.top .r .v { font-size: min(7cqw, 5.5cqh); }
.top .r.first .i { font-size: min(26cqw, 19cqh); }
.top .r.first .stack { flex: 1 1 auto; min-width: 0; display: grid; gap: 1cqmin; }
.top .r.first .nm { font-size: min(10cqw, 7.4cqh); }
.top .r.first .v { font-size: min(12cqw, 9cqh); }
@container (min-aspect-ratio: 5/4) {
  .top { grid-template-columns: 34% 1fr; grid-template-rows: 1fr; }
  .top .head { font-size: min(10cqw, 16cqh); align-self: center; }
  .top .r .i, .top .r .nm, .top .r .v { font-size: min(4.6cqw, 7cqh); }
  .top .r.first .i { font-size: min(10cqw, 17cqh); }
  .top .r.first .nm, .top .r.first .v { font-size: min(6.5cqw, 11cqh); }
}

/* Leaderboard */
.lead { background: ${PAPER}; padding: 4.5cqmin; display: grid; grid-template-rows: auto auto minmax(0, 1fr); }
.lead .kick { font-size: 2.6cqmin; font-weight: 800; }
.lead .head { font-size: min(13cqw, 10cqh); margin: 2cqmin 0 4cqmin; }
.lead .bars { display: grid; gap: 3cqmin; align-content: space-evenly; min-height: 0; }
.lead .lr { display: grid; grid-template-columns: 1fr auto; align-items: end; column-gap: 2.4cqmin; row-gap: .8cqmin; }
.lead .lr .nm { grid-column: 1 / -1; font-size: min(4.6cqw, 3.4cqh); font-weight: 800; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.lead .lr .track { height: min(9cqw, 6.5cqh); }
.lead .lr .bar { height: 100%; border: .55cqmin solid ${INK}; }
.lead .lr .v { font-size: min(9cqw, 6.5cqh); line-height: .9; white-space: nowrap; }
.lead .lr .v small { font-size: .5em; margin-left: .15em; }
@container (min-aspect-ratio: 5/4) {
  .lead .head { font-size: min(7cqw, 12cqh); margin: 1cqmin 0 2cqmin; }
  .lead .lr .nm { font-size: min(2.6cqw, 4.4cqh); }
  .lead .lr .track { height: min(4.6cqw, 7.5cqh); }
  .lead .lr .v { font-size: min(4.6cqw, 7.5cqh); }
}

/* Trading card */
.trade { display: grid; place-items: center; padding: 4.5cqmin; }
.tc { width: min(100%, 72cqh); height: 100%; max-height: 133cqw; background: ${PAPER}; border: .9cqmin solid ${INK}; border-radius: 3.5cqmin; padding: 3cqmin; display: grid; grid-template-rows: auto minmax(0, 1fr) auto auto; gap: 2cqmin; }
.tc .name { font-size: min(11cqw, 8cqh); display: flex; justify-content: space-between; align-items: baseline; }
.tc .name small { font-family: '${TEXT}', sans-serif; font-size: .26em; font-weight: 800; letter-spacing: .06em; }
.tc .art { position: relative; border: .55cqmin solid ${INK}; border-radius: 1.6cqmin; overflow: hidden; min-height: 0; }
.tc .stats { display: grid; gap: .5cqmin; }
.tc .s { display: flex; align-items: baseline; gap: 1.2cqmin; font-size: min(3.5cqw, 2.6cqh); font-weight: 700; }
.tc .s .lead-dots { flex: 1 1 auto; border-bottom: .45cqmin dotted ${INK}; transform: translateY(-.3em); }
.tc .s b { font-family: '${DISPLAY}', 'Anton', sans-serif; font-weight: 400; font-size: 1.35em; }
.tc .foot { display: flex; justify-content: space-between; font-size: min(3cqw, 2.3cqh); font-weight: 800; border-top: .45cqmin solid ${INK}; padding-top: 1.2cqmin; }
@container (min-aspect-ratio: 5/4) {
  .tc { width: 100%; height: 100%; max-height: none; grid-template-columns: 46% 1fr; grid-template-rows: auto 1fr auto; }
  .tc .name { grid-column: 1 / -1; font-size: min(7cqw, 11cqh); }
  .tc .art { grid-row: 2 / 4; }
  .tc .stats { align-content: center; }
  .tc .s { font-size: min(2.4cqw, 4cqh); }
  .tc .foot { font-size: min(2cqw, 3.4cqh); }
}

/* Painted: GPT Image set the whole card; the figures it was given run along the foot. */
.painted { background: ${INK}; }
.painted .pic { position: absolute; inset: 0; }
.painted .strip { position: absolute; left: 0; right: 0; bottom: 0; background: ${PAPER}; color: ${INK}; font-size: min(2.2cqw, 2.6cqh); font-weight: 600; line-height: 1.3; padding: 1cqmin 3cqmin; text-align: center; }
.painted .strip b { font-weight: 800; }

.msg { display: grid; place-items: center; text-align: center; padding: 8%; font-size: clamp(12px, 3cqmin, 22px); }
`;

/* Faces go into the document once (a shadow root does not honour @font-face everywhere). */
async function useFaces() {
  if (typeof document === 'undefined') return;
  if (!document.getElementById('home-wrapped-faces')) {
    const u = (f) => new URL(`./static/fonts/${f}`, import.meta.url).href;
    const el = document.createElement('style');
    el.id = 'home-wrapped-faces';
    el.textContent = `@font-face { font-family: '${DISPLAY}'; src: url('${u('anton-normal-400.woff2')}') format('woff2'); font-weight: 400; }
@font-face { font-family: '${TEXT}'; src: url('${u('inter_tight-normal-400_900.woff2')}') format('woff2'); font-weight: 400 900; }`;
    document.head.appendChild(el);
  }
  try {
    await Promise.race([
      Promise.all([`400 20px '${DISPLAY}'`, `700 20px '${TEXT}'`, `800 20px '${TEXT}'`].map((f) => document.fonts.load(f, 'Home 0123456789'))),
      new Promise((res) => setTimeout(res, 3000)),
    ]);
  } catch (e) { /* the fallback faces still read */ }
}

function loaded(img, ms) {
  if (!img) return Promise.resolve();
  const wait = new Promise((res) => setTimeout(res, ms));
  const ready = typeof img.decode === 'function'
    ? img.decode().catch(() => {})
    : new Promise((res) => { img.onload = res; img.onerror = res; });
  return Promise.race([ready, wait]);
}

function picHtml(pic) {
  if (!pic) return '';
  if (pic.url) return `<img class="pic" alt="" src="${esc(pic.url)}">`;
  if (pic.file) return `<img class="pic file" alt="" src="${esc(new URL(`./static/art/${pic.file}`, import.meta.url).href)}">`;
  return '';
}

const fill = (b) => `background:${b[0]};color:${b[1]}`;

function numHtml(it, cls = 'big') {
  return `<div class="${cls} d">${esc(it.value)}${it.unit ? `<small>${esc(it.unit)}</small>` : ''}</div>`;
}

/* A size for the big number that keeps it on one line: the longer it is, the smaller. */
function bigSize(text, land) {
  const n = Math.max(3, String(text).length);
  return land ? `min(${Math.min(24, 120 / n).toFixed(1)}cqw, 40cqh)` : `min(${Math.min(36, 165 / n).toFixed(1)}cqw, 30cqh)`;
}

function rows(list, p, withValue) {
  return (list || []).map((r, i) => `<div class="row"><span class="n" style="background:${p.dot}">${i + 1}</span><span class="nm">${esc(r.name)}</span>${withValue ? `<span class="v">${esc(r.value)}${r.unit ? ` ${esc(r.unit)}` : ''}</span>` : ''}</div>`).join('');
}

function summary(d, p) {
  const s = d.stats || [];
  const blocks = s.slice(0, 2).map((it, i) => `<div class="blk" style="${fill(p.blocks[i])}"><div class="lab">${esc(it.name)}</div>${numHtml(it)}</div>`).join('');
  return `<div class="card sum" style="background:${p.frame};color:${p.frameInk}">
    <div class="title d">${esc(d.title)} ${esc(d.year)}</div>
    <div class="art">${picHtml(d.pic)}</div>
    <div class="lists" style="color:${INK};background:${p.paper}">
      <div class="list"><h3 class="d">${esc(d.list_a_title)}</h3>${rows(d.list_a, p, false)}</div>
      <div class="list"><h3 class="d">${esc(d.list_b_title)}</h3>${rows(d.list_b, p, false)}</div>
    </div>
    ${blocks ? `<div class="blocks">${blocks}</div>` : ''}
  </div>`;
}

const LONG = { h: 'hours' };

function perDay(it) {
  if (!it.count || !Number.isFinite(it.raw)) return '';
  const now = new Date();
  const day = Math.max(1, Math.floor((now - new Date(now.getFullYear(), 0, 0)) / 86400000));
  const rate = it.raw / day;
  if (rate < 0.1) return '';
  return `That's ${rate < 10 ? rate.toFixed(1) : Math.round(rate).toLocaleString('en')} a day.`;
}

function number(d, p, land) {
  const it = (d.stats || [])[d.index || 0];
  if (!it) return `<div class="card msg">Add a stat in the options.</div>`;
  const rate = perDay(it);
  const accent = p.blocks[(d.index || 0) % p.blocks.length][0];
  return `<div class="card num" style="background:${p.paper}">
    <div class="kick">${esc(d.title)} ${esc(d.year)}</div>
    <div class="lab d">${esc(it.name)}</div>
    <div class="big d" style="font-size:${bigSize(it.value, land)}">${esc(it.value)}</div>
    ${it.unit ? `<div class="unit d">${esc(LONG[it.unit] || it.unit)}</div>` : ''}
    <div class="spot"><span class="burst" style="background:${accent}"></span>${d.pic && (d.pic.url || d.pic.file) ? `<div class="disc">${picHtml(d.pic)}</div>` : ''}</div>
    ${rate ? `<div class="rate">${esc(rate)}</div>` : ''}
  </div>`;
}

function top(d, p) {
  const list = (d.list_a || []).slice(0, 5);
  const heading = `Your ${String(d.list_a_title || '').toLowerCase()}`;
  const body = list.map((r, i) => {
    const b = i === 0 ? [p.paper, INK] : p.blocks[(i - 1) % p.blocks.length];
    const v = `<span class="v d">${esc(r.value)}${r.unit ? ` ${esc(r.unit)}` : ''}</span>`;
    const name = `<span class="nm d">${esc(r.name)}</span>`;
    return `<div class="r${i === 0 ? ' first' : ''}" style="${fill(b)}"><span class="i d">${i + 1}</span>${i === 0 ? `<span class="stack">${name}${v}</span>` : name + v}</div>`;
  }).join('');
  return `<div class="card top" style="background:${p.frame};color:${p.frameInk}">
    <div class="head d">${esc(heading)}</div>
    <div class="rows" style="color:${INK}">${body}</div>
  </div>`;
}

function leaderboard(d, p) {
  const list = (d.list_b || []).slice(0, 5);
  const bars = list.map((r, i) => `<div class="lr"><div class="nm">${i + 1}. ${esc(r.name)}</div><div class="track"><div class="bar" style="background:${p.blocks[i % p.blocks.length][0]};width:${Math.max(4, Math.round(100 * r.bar))}%"></div></div><div class="v d">${esc(r.value)}${r.unit ? `<small>${esc(r.unit)}</small>` : ''}</div></div>`).join('');
  return `<div class="card lead" style="background:${p.paper}">
    <div class="kick">${esc(d.title)} ${esc(d.year)}</div>
    <div class="head d">${esc(d.list_b_title)}</div>
    <div class="bars">${bars}</div>
  </div>`;
}

function trading(d, p) {
  const s = (d.stats || []).map((it) => `<div class="s"><span>${esc(it.name)}</span><span class="lead-dots"></span><b>${esc(it.value)}${it.unit ? ` ${esc(it.unit)}` : ''}</b></div>`).join('');
  return `<div class="card trade" style="background:${p.blocks[2][0]}">
    <div class="tc" style="background:${p.paper}">
      <div class="name d">${esc(d.card_name)}<small>${esc(d.year)}</small></div>
      <div class="art">${picHtml(d.pic)}</div>
      <div class="stats">${s}</div>
      <div class="foot"><span>${esc(d.title)}</span><span>1 of 1</span></div>
    </div>
  </div>`;
}

function paintedHtml(d) {
  const p = d.painted;
  const when = /^(\d{4})-(\d{2})-(\d{2})$/.exec(p.when || '');
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const date = when ? `${Number(when[3])} ${months[Number(when[2]) - 1]}` : '';
  const figs = (p.figures || []).map(esc).join(' · ');
  const strip = d.strip && figs ? `<div class="strip">${date ? `<b>Painted ${esc(date)}:</b> ` : ''}${figs}</div>` : '';
  return `<div class="card painted"><img class="pic" alt="" src="${esc(p.url)}">${strip}</div>`;
}

const DRAW = { summary, number, top, leaderboard, trading };

export default async function render(shadow, ctx) {
  const d = (ctx && ctx.data) || {};
  const p = PALETTES[d.palette] || PALETTES.primary;
  const rect = shadow.host && shadow.host.getBoundingClientRect ? shadow.host.getBoundingClientRect() : { width: 0, height: 0 };
  const cell = (ctx && ctx.cell) || {};
  const land = (cell.w || rect.width || 3) > (cell.h || rect.height || 4) * 1.25;
  let body;
  if (d.error) body = `<div class="card msg">${esc(d.error)}</div>`;
  else if (d.painted && d.painted.url) body = paintedHtml(d);
  else body = (DRAW[d.card] || summary)(d, p, land);
  const wrap = `<div class="${d.palette === 'gray' ? 'gray' : ''}" style="width:100%;height:100%">${body}</div>`;
  shadow.innerHTML = `<style>${CSS}</style>${wrap}`;
  const card = shadow.querySelector('.card');
  if (card && d.grain !== false && !d.painted) card.classList.add('grain');
  const painting = !d.painted && ['painting', 'waiting', 'held'].includes(d.paint_state);
  if (card && painting) (card.querySelector('.art') || card).insertAdjacentHTML('beforeend', '<div class="chip">Painting this card</div>');
  if (card && d.sample) (card.querySelector('.art') || card).insertAdjacentHTML('beforeend', '<div class="chip">Sample year</div>');
  await Promise.all([useFaces(), loaded(shadow.querySelector('img.pic'), 8000)]);
}
