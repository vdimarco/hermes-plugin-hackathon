// Renders the catalog card images (1200x600, 2:1, saved at 2x) into the folder given as the first argument.
// Usage: node scripts/render-catalog-cards.js assets/catalog   (needs the playwright package and Inter + JetBrains Mono fonts)
const { chromium } = require('playwright')
const fs = require('fs')
const path = require('path')
const OUT = process.argv[2]

const css = `
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { width: 1200px; height: 600px; }
body { background: #0c0e12; color: #e9ebef; font-family: 'Inter', sans-serif; overflow: hidden; position: relative; }
.glow { position: absolute; inset: 0; background:
  radial-gradient(700px 420px at 92% 8%, var(--a20), transparent 70%),
  radial-gradient(500px 300px at 0% 100%, var(--a10), transparent 70%); }
.grid { position: absolute; inset: 0; opacity: .5; background-image:
  linear-gradient(rgba(255,255,255,.035) 1px, transparent 1px),
  linear-gradient(90deg, rgba(255,255,255,.035) 1px, transparent 1px);
  background-size: 40px 40px; mask-image: radial-gradient(900px 500px at 80% 40%, #000 30%, transparent 75%); }
.wrap { position: relative; display: grid; grid-template-columns: 520px 1fr; gap: 44px; padding: 64px 64px 56px 72px; height: 100%; }
.left { display: flex; flex-direction: column; }
.kicker { font-family: 'JetBrains Mono', monospace; font-size: 14px; letter-spacing: .18em; text-transform: uppercase; color: var(--a); font-weight: 600; }
h1 { font-family: 'Inter Display', 'Inter', sans-serif; font-size: 96px; line-height: 1; font-weight: 800; letter-spacing: -0.035em; margin-top: 22px; }
.tag { font-size: 27px; line-height: 1.32; color: #b9bec8; margin-top: 22px; font-weight: 450; max-width: 470px; }
.tag b { color: #f2f3f5; font-weight: 600; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: auto; }
.chip { font-family: 'JetBrains Mono', monospace; font-size: 13px; color: #c9cdd4; border: 1px solid rgba(255,255,255,.12); border-radius: 6px; padding: 6px 10px; background: rgba(255,255,255,.03); }
.repo { font-family: 'JetBrains Mono', monospace; font-size: 13px; color: #6f7684; margin-top: 18px; }
.right { display: flex; align-items: center; justify-content: center; }
.panel { width: 100%; background: rgba(20,23,29,.92); border: 1px solid rgba(255,255,255,.09); border-radius: 14px;
  box-shadow: 0 30px 80px rgba(0,0,0,.55), 0 0 0 1px rgba(255,255,255,.02) inset; overflow: hidden; }
.bar { display: flex; align-items: center; gap: 8px; padding: 12px 16px; border-bottom: 1px solid rgba(255,255,255,.07); font-size: 13px; color: #8a909c; }
.bar i { width: 10px; height: 10px; border-radius: 50%; background: rgba(255,255,255,.12); display: inline-block; }
.bar span { margin-left: 8px; font-family: 'JetBrains Mono', monospace; }
.body { padding: 18px 20px 20px; }
.mono, .chip, .kicker, .repo, .bar span { font-family: 'JetBrains Mono', monospace; font-variant-ligatures: none; font-feature-settings: 'liga' 0, 'calt' 0; }
.dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; flex: none; }
.good { background: #3ecf8e; } .bad { background: #f2555a; } .warn { background: #f5b13d; } .muted { background: #5d6470; }
.row { display: flex; align-items: center; gap: 10px; font-size: 15px; }
.sub { font-size: 12.5px; color: #8a909c; margin: 4px 0 0 19px; }
.sep { height: 1px; background: rgba(255,255,255,.07); margin: 12px 0; }
.badge { font-size: 11.5px; font-weight: 600; padding: 3px 8px; border-radius: 4px; }
`

const card = (accent, a20, a10, kicker, name, tag, chips, panel) => `<!doctype html><html><head><meta charset="utf-8"><style>
:root { --a: ${accent}; --a20: ${a20}; --a10: ${a10}; } ${css}</style></head><body>
<div class="glow"></div><div class="grid"></div>
<div class="wrap"><div class="left">
<div class="kicker">${kicker}</div><h1>${name}</h1><div class="tag">${tag}</div>
<div class="chips">${chips.map(c => `<span class="chip">${c}</span>`).join('')}</div>
<div class="repo">github.com/vdimarco/hermes-plugin-hackathon</div>
</div><div class="right">${panel}</div></div></body></html>`

const vouchPanel = `<div class="panel"><div class="bar"><i></i><i></i><i></i><span>hermes · reply</span></div><div class="body">
<div style="font-size:16px;line-height:1.5;color:#d6d9df">Fixed the discount rounding in <span class="mono" style="color:#f2f3f5">cart.py</span>. All tests pass and I pushed the fix to origin.</div>
<div class="sep"></div>
<div class="row" style="font-size:13px;color:#8a909c"><b style="color:#f2f3f5;font-weight:650">Vouch</b> · 3 claims checked · 1 contradicted · 1 unverified · 1 verified</div>
<div style="margin-top:12px;display:flex;flex-direction:column;gap:10px">
<div><div class="row"><span class="dot bad"></span><b style="font-weight:600">tests pass</b></div><div class="sub"><span class="mono">pytest -q | tail -1</span> exited 0, but the output shows 1 failed</div></div>
<div><div class="row"><span class="dot warn"></span><b style="font-weight:600">changes pushed</b></div><div class="sub">no git push command ran in this session</div></div>
<div><div class="row"><span class="dot good"></span><b style="font-weight:600">file changed (cart.py)</b></div><div class="sub">patch cart.py (ok)</div></div>
</div>
<div class="sep"></div>
<div class="row mono" style="font-size:12.5px;color:#8a909c;justify-content:flex-end;gap:7px"><span class="dot bad"></span>vouch ✓1 ⚠1 ✗1</div>
</div></div>`

const contractPanel = `<div class="panel"><div class="bar"><i></i><i></i><i></i><span>Contract</span></div><div class="body">
<div style="font-size:17px;font-weight:650">Fix double discount</div>
<div class="row" style="margin-top:8px;font-size:13px;color:#8a909c"><span class="badge" style="background:rgba(62,207,142,.14);color:#3ecf8e">met</span>2/2 checks pass</div>
<div class="sep"></div>
<div style="display:flex;flex-direction:column;gap:11px">
<div><div class="row"><span class="dot good"></span><b style="font-weight:600">c1 unit tests</b><span style="margin-left:auto;font-size:12px;color:#8a909c">pass</span></div><div class="sub mono">pytest -q tests/test_cart.py</div></div>
<div><div class="row"><span class="dot good"></span><b style="font-weight:600">c2 discount is applied once</b><span style="margin-left:auto;font-size:12px;color:#8a909c">pass</span></div><div class="sub mono">python -c "assert apply_discount(100, 10) == 90"</div></div>
</div>
<div class="sep"></div>
<div class="row" style="font-size:12.5px;color:#8a909c;gap:6px;flex-wrap:wrap">🔒 <span class="mono">tests/*</span> <span class="mono">conftest.py</span> <span class="mono">test_*.py</span></div>
<div class="row mono" style="font-size:12.5px;color:#b9bec8;margin-top:10px">receipt 9f2c41d0a7e3c8b1 <span style="margin-left:auto;color:#6f7684">tree 4b1e0c9a77</span></div>
<div style="margin-top:14px;border:1px solid rgba(245,177,61,.35);background:rgba(245,177,61,.08);border-radius:8px;padding:9px 12px;font-size:13px;color:#f5d39a">
<b style="color:#f5b13d">Contract lock</b> · blocked a patch edit to <span class="mono">tests/test_cart.py</span></div>
</div></div>`

const mvPanel = `<div class="panel"><div class="bar"><i></i><i></i><i></i><span>Multiverse</span></div><div class="body">
<div style="font-size:17px;font-weight:650">Fix the double discount in cart.py</div>
<div class="row" style="margin-top:8px;font-size:13px;color:#8a909c"><span class="badge" style="background:rgba(167,139,250,.16);color:#c4b5fd">judged</span>3 universes · base HEAD 1a2b3c4</div>
<div class="sep"></div>
<div style="display:flex;flex-direction:column;gap:12px">
<div><div class="row"><span class="dot good"></span><b style="font-weight:600">🏆 u2</b><span class="mono" style="font-size:11px;color:#8a909c;border:1px solid rgba(255,255,255,.12);border-radius:4px;padding:1px 6px">model-b</span><span style="margin-left:auto;font-size:12.5px;color:#b9bec8">1/1 checks · +0 −1</span></div><div class="sub">Make the smallest change that can possibly work</div></div>
<div><div class="row"><span class="dot good"></span><b style="font-weight:600">u1</b><span class="mono" style="font-size:11px;color:#8a909c;border:1px solid rgba(255,255,255,.12);border-radius:4px;padding:1px 6px">model-a</span><span style="margin-left:auto;font-size:12.5px;color:#b9bec8">1/1 checks · +1 −1</span></div><div class="sub">Take the most direct route to a correct fix</div></div>
<div><div class="row"><span class="dot bad"></span><b style="font-weight:600">u3</b><span style="margin-left:auto;font-size:12.5px;color:#b9bec8">0/1 checks · +4 −1</span></div><div class="sub">Find a different design and implement it</div></div>
</div>
<div class="sep"></div>
<div class="mono" style="font-size:12px;color:#6f7684;line-height:1.7">.shop.multiverse/3f9a02c1/u1<br>.shop.multiverse/3f9a02c1/u2 <span style="color:#3ecf8e">← adopt</span><br>.shop.multiverse/3f9a02c1/u3</div>
</div></div>`

const cards = {
  vouch: card('#f5b13d', 'rgba(245,177,61,.22)', 'rgba(245,177,61,.07)', 'Plugin for Hermes Agent', 'Vouch',
    'Checks every <b>“done”</b> in the agent’s reply against what it really ran.',
    ['tests pass?', 'committed?', 'pushed?', 'deployed?'], vouchPanel),
  contract: card('#3ecf8e', 'rgba(62,207,142,.2)', 'rgba(62,207,142,.07)', 'Plugin for Hermes Agent', 'Contract',
    'You approve the definition of done. The agent <b>cannot edit the tests</b> or finish while a check fails.',
    ['locked tests', 'finish gate', 're-runnable receipt'], contractPanel),
  multiverse: card('#a78bfa', 'rgba(167,139,250,.24)', 'rgba(167,139,250,.08)', 'Plugin for Hermes Agent', 'Multiverse',
    'Races attempts at a coding task in <b>separate git worktrees</b> and keeps the one that passes.',
    ['2–6 attempts', 'pinned subagents', 'model races'], mvPanel),
}

;(async () => {
  const browser = await chromium.launch()
  const page = await browser.newPage({ viewport: { width: 1200, height: 600 }, deviceScaleFactor: 2 })
  for (const [name, html] of Object.entries(cards)) {
    fs.writeFileSync(path.join(OUT, `${name}.html`), html)
    await page.setContent(html, { waitUntil: 'load' })
    await page.evaluate(() => document.fonts.ready)
    await page.screenshot({ path: path.join(OUT, `${name}.png`) })
  }
  await browser.close()
})()
