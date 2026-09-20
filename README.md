# Jump & Gravity Tuner

Say how high and how long the jump should be; get the gravity and launch velocity to put in your engine — corrected for the timestep your game actually runs at — with a playable preview. Runs entirely in your browser.

**Live:** <https://jump-tuner.slippylabs.com/>

## What it does

- Enter jump height, time to apex, fall multiplier, run speed, release cut and terminal velocity; get every number your character controller needs.
- Corrects those numbers for your **fixed timestep and integration order**, and shows the textbook values failing next to them.
- Coyote time and jump buffer, reported in both milliseconds and frames.
- A trajectory plot with one dot per simulated step, and a playable strip you can actually jump in.
- Exports for Unity, Godot 4, GameMaker and plain JavaScript.

## How it works

The continuous solution every tutorial gives is `g = 2h/t²`, `v₀ = 2h/t`. It is wrong for your game, because your game does not integrate continuously.

Take the usual order — velocity first, then position. After n steps `y = n·dt·v₀ − g·dt²·n(n+1)/2`, against the continuous `y(t) = v₀t − gt²/2`. Subtract: the discrete curve sits exactly `(g·dt/2)·t` **below** the real parabola. At the apex, where `v₀ = g·tₐ`, that shortfall is `v₀·dt/2` — half a frame of launch velocity, independent of how small the jump is. At 60 Hz with a 3 m jump that is 12 cm.

So the apex a velocity-first loop really reaches is `v₀(tₐ − dt)/2`, and asking for that to equal h gives

    v₀ = 2h / (tₐ − dt)        g = 2h / (tₐ(tₐ − dt))

One `tₐ` becomes `(tₐ − dt)`. That is the whole correction, it is exact, and the position-first order picks up the opposite sign. The fall time and the shortest hop are derived the same way — the hop needs an explicit floor, because a jump cut halfway up has its apex between two steps.

## Verification

`verify_jump.py` re-implements the loop in Python — nothing shared with the page but the numbers it prints — and measures:

- **250 cases** (5 rates × 5 heights × 5 apex times × 2 orders). Where the apex lands on a step boundary the corrected numbers hit the requested height **exactly**; elsewhere within one step of velocity.
- A **control that must fail**: the textbook gravity through the same loop misses by exactly `v₀·dt/2` in every case.
- The airtime closed form against the same loop at its own timestep: within one step in all 250 cases, and exactly `sqrt(2h/g)` when there is no fixed step.
- The shortest hop over 32 cases, **exact to 2.2e-15**.
- The correction shrinks as `2h·dt/(tₐ(tₐ−dt))` exactly at every rate from 30 Hz to 10 kHz.

**1,503 checks**, plus a browser suite that jumps in the live preview and confirms a held jump goes higher than a tapped one.
