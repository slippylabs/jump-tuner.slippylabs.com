#!/usr/bin/env python
"""Play jump-tuner's strip on simulated panels and measure the drawn pacing.

The loop under test is the REAL frame(): requestAnimationFrame is replaced with
a no-op first, which kills the page's own chain, and from then on this script
is the clock. The step rate is a CONTROL on the page, so every (step, panel)
pair is measured -- the point of the fix is that picking "Unity default" on an
ordinary 60 Hz monitor no longer draws every sixth frame twice.

    ~/projects/medievalmmo-dev/venv/bin/python tools/playtest.py          # this checkout
    ~/projects/medievalmmo-dev/venv/bin/python tools/playtest.py https://jump-tuner.slippylabs.com/

TWO MEASUREMENT TRAPS, both of which made this read as a no-op at first:

  * Reading player.x after frame() RETURNS reads the simulation, not what was
    drawn -- swap-and-restore has already put it back. The blended arm measured
    identically to the raw one, 106 duplicates against 107, until the recording
    moved inside drawPlay.
  * The absolute median deviation is NOT a usable bar for a jump arc. The
    character's vertical speed reverses at the apex, so the drawn displacement
    per frame legitimately swings between "rise plus run" and "run alone" --
    9-29% on a perfectly smooth arc, and the SAME in both arms at a 60 Hz step
    on a 60 Hz panel. The duplicate count is what isolates stutter from the arc.
"""
import functools
import http.server
import json
import os
import socketserver
import statistics
import sys
import threading

from playwright.sync_api import sync_playwright


def serve(root):
    h = functools.partial(http.server.SimpleHTTPRequestHandler, directory=root)

    class S(socketserver.TCPServer):
        allow_reuse_address = True

        def handle_error(self, *a):
            pass

    httpd = S(("127.0.0.1", 0), h)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


RUN = """(({ hz, frames, mode, seed }) => {
  window.requestAnimationFrame = () => 0;
  // RECORD INSIDE THE DRAW. Reading player.x after frame() returns reads the
  // SIMULATION, because swap-and-restore has already put it back -- which is
  // what the interp header warns about, and it made the blended arm measure
  // identically to the raw one (106 duplicates against 107). drawPlay is a
  // top-level function in a classic script, so it is a property of window and
  // wrapping it here is what drawBlended will call.
  let drawnAt = null;
  const realDraw = window.drawPlay;
  window.drawPlay = function (p) { drawnAt = { x: player.x, y: player.y }; return realDraw(p); };
  let s = seed >>> 0 || 1;
  const rnd = () => { s ^= s << 13; s >>>= 0; s ^= s >> 17; s ^= s << 5; s >>>= 0; return s / 4294967296; };
  // Hold right and tap jump, so the strip is actually being played.
  input.right = true;
  accumulator = 0; prevPose = null;
  const rows = [];
  let t = 0;
  for (let f = 0; f < frames; f++) {
    if (f % Math.round(hz * 0.9) === 0) pressJump();
    if (f % Math.round(hz * 0.9) === 6) releaseJump();
    const dt = cachedParams.dt > 0 ? cachedParams.dt : 1 / 60;
    if (mode === 'shipped') {
      frame(t);
    } else {
      // Before the fix: the same accumulator, drawn straight from the sim.
      accumulator += Math.min(0.25, (t - lastFrame) / 1000);
      lastFrame = t;
      let guard = 0;
      while (accumulator >= dt && guard++ < 12) { prevPose = { x: player.x, y: player.y }; stepPlayer(cachedParams, dt); accumulator -= dt; }
      drawPlay(cachedParams);
    }
    // The simulation's OWN speed goes in the row. Holding right for ten
    // seconds parks the character against the wall clamp, and at the apex of a
    // jump it is then genuinely, momentarily still -- vertical velocity
    // through zero with no horizontal motion left. That is not a frozen frame,
    // and nothing about the drawn path alone can tell the two apart, because
    // both neighbours are moving either way.
    rows.push([t, drawnAt ? drawnAt.x : player.x, drawnAt ? drawnAt.y : player.y,
               Math.hypot(player.vx, player.vy)]);
    t += (1000 / hz) * (1 + (rnd() - 0.5) * 0.04);
  }
  input.right = false; releaseJump();
  window.drawPlay = realDraw;
  return rows;
})"""

def analyse(rows):
    speeds = [r[3] for r in rows if r[3] > 0]
    floor = (statistics.median(speeds) * 0.15) if speeds else 0
    samples = []
    for a, b in zip(rows, rows[1:]):
        dt = (b[0] - a[0]) / 1000.0
        if dt <= 0: continue
        # Skip where the simulation itself is near-still: see the comment in RUN.
        if min(a[3], b[3]) < floor: continue
        gap = ((b[1]-a[1])**2 + (b[2]-a[2])**2) ** 0.5
        samples.append(gap / dt)
    if len(samples) < 50: return None
    W, local, dupes = 4, [], 0
    for i in range(len(samples)):
        lo, hi = max(0, i-W), min(len(samples), i+W+1)
        nb = [samples[j] for j in range(lo, hi) if j != i]
        med = statistics.median(nb)
        if med <= 1e-9: continue
        local.append(abs(samples[i]-med)/med)
        if samples[i] < med*0.1:
            pm = i>0 and samples[i-1] > med*0.5
            nm = i+1 < len(samples) and samples[i+1] > med*0.5
            if pm and nm: dupes += 1
    if len(local) < 50: return None
    ls = sorted(local)
    return {"med": statistics.median(local), "p95": ls[int(len(ls)*0.95)], "dupes": dupes,
            "inband": sum(1 for v in local if v < 0.25)/len(local)}

def main(url):
    fails = []
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--use-gl=swiftshader","--enable-unsafe-swiftshader"])
        print(f"jump-tuner play test against {url}")
        for step_hz in (60, 50, 30):
            for panel in (60, 144):
                res = {}
                for mode in ("shipped", "old"):
                    pg = b.new_page(viewport={"width": 1100, "height": 900})
                    errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
                    pg.goto(url, wait_until="load", timeout=120000)
                    # WAIT ON THE CONDITION, not a timeout. The live page pulls
                    # in the deck and a11y scripts and a Cloudflare beacon, so a
                    # fixed 1.2 s was enough locally and not enough through the
                    # edge -- the harness evaluated before the inline script had
                    # finished and reported `input is not defined`, which reads
                    # like a broken page and was a broken wait.
                    pg.wait_for_function(
                        "typeof input !== 'undefined' && typeof frame === 'function'"
                        " && typeof drawBlended === 'function'", timeout=60000)
                    pg.select_option("#rate-select", str(step_hz))
                    pg.wait_for_timeout(400)
                    rows = pg.evaluate(RUN + "(" + json.dumps(
                        {"hz": panel, "frames": int(10*panel), "mode": mode, "seed": 5}) + ")")
                    res[mode] = (analyse(rows), errs)
                    pg.close()
                (on, on_e), (off, off_e) = res["shipped"], res["old"]
                fmt = lambda m: (f"med {m['med']*100:5.1f}% p95 {m['p95']*100:6.1f}% "
                                 f"in-band {m['inband']*100:5.1f}% dupes {m['dupes']:4d}") if m else "too little motion"
                print(f"  step {step_hz:3d} Hz on a {panel:3d} Hz panel")
                print(f"          shipped  {fmt(on)}")
                print(f"          old      {fmt(off)}")
                if not on: fails.append(f"step {step_hz} panel {panel}: nothing to judge")
                else:
                    if on["dupes"] > 0:
                        fails.append(f"step {step_hz} panel {panel}: {on['dupes']} duplicate frames shipped")
                # The absolute median is NOT a usable bar here and is reported
                # only as context. The tracked thing is a jumping character:
                # its vertical speed reverses at the apex, so the drawn
                # displacement per frame legitimately swings from "rise plus
                # run" to "run alone" and back, several times a run. That puts
                # the local median at 9-26% on a perfectly smooth arc -- and it
                # reads the SAME in both arms at a 60 Hz step on a 60 Hz panel
                # (18.9% against 18.9%), which is the proof that it is the arc
                # being measured and not the drawing. The duplicate count is
                # what isolates stutter from the arc.
                # Where the old arm duplicated frames, the shipped one must
                # not. At a 60 Hz step on a 60 Hz panel there is nothing to
                # beat against, so there is nothing to discriminate either.
                if off and on and off["dupes"] > 3 and on["dupes"] >= off["dupes"]:
                    fails.append(f"step {step_hz} panel {panel}: shipped {on['dupes']} duplicates vs "
                                 f"old {off['dupes']} -- no better")
                for e in on_e + off_e: fails.append(f"step {step_hz} panel {panel}: page error {e[:80]}")
        b.close()
    print()
    for f in fails: print("FAIL " + f)
    print("jump-tuner: evenly paced at every step and panel" if not fails else f"{len(fails)} problems")
    return 1 if fails else 0

if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if arg.startswith("http"):
        sys.exit(main(arg))
    httpd, port = serve(arg)
    try:
        sys.exit(main(f"http://127.0.0.1:{port}/"))
    finally:
        httpd.shutdown()
