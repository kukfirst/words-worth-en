# The line panel: how it must behave

This is the contract for **LINE ON SCREEN** — what the panel owes the person using it. It is
written down because the panel was fixed three times by chasing symptoms, and each fix bought a
new one: the editor that vanished under the cursor, the hold that froze the panel, the Save that
went quietly dead. A panel with no stated behaviour cannot be checked, only argued about.

`emu/play_layout_check.py` asserts every rule here. A rule that no check can express is a rule
this file should not contain.

## Words

* **window** — the game's own message window, 56 columns by 4 rows, read off the picture.
* **entry** — the row in `text/<FILE>.json` that a window line came from.
* **box** — the panel's text area.
* **following** — the panel is showing whatever the window shows (the resting state).
* **held** — the panel is pinned to one entry while the game carries on.

## The rules

**R1 — Nothing here changes size.** The panel, the box, the control bar and the map card keep
their size in every state. The game picture is the only thing that may grow, and only when the
window is resized or the panel is folded away by hand. *Why: the page is a flex column, so a
panel that grows takes its height out of the picture, and the game jumps while being played.*

**R2 — One surface.** The box is the same element in the same place at all times. It is either
editable or not; it never swaps for a different element, never disappears, and never appears.
*Why: a control that comes and goes cannot be clicked on purpose.*

**R3 — Following is the resting state.** With no one working on it, the panel shows the line in
the window and names the entry it came from, or says plainly why it cannot name one.

**R4 — Holding is for work, and it is visible.** The panel stops following only for a line that
can be edited, and only when work starts on it: the caret goes into the box, or `⏸ hold` is
pressed. Where there is nothing to hold — a window the engine printed in pieces, which no single
entry owns — the control is **unavailable**, not merely ineffective: holding there pins a panel
that cannot be typed in, with Save off and a note claiming all is well. While held it says
`held`, and says the game is still running. **The game is never paused by this**, and the
control says `hold` / `follow` rather than showing a bare ⏸/▶, which was read as the game's own
pause.

**R5 — While held, nothing may be taken away.** No reading of the window may change what is in
the box, whether it can be typed into, or which entry Save would write. The window is re-read
several times a second; none of that may reach a line being worked on.

**R6 — A held line is taken back.** If the game leaves the held line and then shows it again,
the panel resumes offering it — editable, with Save available — without anyone pressing
anything.

**R7 — Save is honest.** Save is offered exactly when pressing it would write. The cockpit only
writes the line that is in the window *now*; when that is not true, Save is unavailable and the
panel says why. Save is never offered for something that would be refused for that reason.

**R8 — Refusals are explained in place.** What the checker would refuse is shown while typing,
not only on Save, and a refusal names every reason. Neither may move anything (R1).

**R9 — Letting go is deliberate.** `▶` returns to following and throws away what was typed. If
there is anything to lose, it asks once first.

**R10 — Arrival is not a trap.** The game *types* its messages out, so a window is read many
times before it settles. While that is happening the panel may show the text, but it must not
offer an editor that then withdraws (R2). Once the line has settled and is identified, the box
becomes editable **by itself** — no second click, no reload.

**R11 — Only what may be changed can be changed.** The speaker tag, the `{0}` markers and the
character set are the game's, not the proofreader's. An edit that breaks them is refused, and
said so while typing.

**R12 — The pack is the only thing written.** Corrections go to `text/<FILE>.json` and nowhere
else; the game, the image and the scripts are never touched from here.

**R14 — A line that appears in several places is corrected in all of them.** 12% of this
game's screen lines are rendered by more than one entry (greetings, shop lines, battle lines),
and half of those live inside a single file, so neither the scene nor a picker can say which
occurrence the game is showing. When every one of them holds the **same** text, that question
does not need answering: the panel says `this line appears in N places` and Save writes the
correction to all N. When they hold *different* text, the cockpit cannot know which is on
screen and will not write: it says so and names the entries, rather than offering a picker that
changes nothing. *Why: the panel used to go read-only with a small grey caption and a dead
Save, and the list of "matches" implied that choosing one would help — it never did, because
the write was refused for the whole ambiguous set regardless of the choice.*

**R13 — A finished save lets go.** Holding protects work in progress; once the correction is
written the work is done, so the panel goes back to following the screen by itself. *Why: a
panel left held after a save shows a frozen picture of the past. The message window closes
behind it, Save goes dark because that line is no longer on screen, and every later line is
unreachable — "after the first change it is not updatable in any scenario". Nobody asked the
panel to stay behind, so it must not.*

## What is deliberately not promised

* The panel does not follow the window while held — that is the point of holding (R5).
* Save does not work for a line the game has left (R7). The way back is `▶`, or returning to
  the line in the game (R6).
* Holding nothing is not possible: with no line to pin there is nothing to protect, and a hold
  on nothing can only freeze the panel.
