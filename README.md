# FFXIV Mechanics Board

A live, collaborative raid-mechanics diagrammer. Open the site, type your name, and everyone on the board sees each other's cursors and edits in real time.

- Slides for multiple arenas (duplicate / copy / reorder / delete)
- AoE shapes in yalms: circle, donut, rectangle/line, cone, cross, 8-way star, tower, boss
- Player markers, waymarks, pen, eraser, text, arrows, tethers with live distance
- Shapes can target a player and auto-aim at them
- Export PNG / zip of all slides; JSON backup and import

Add `#b=name` to the URL to open a separate board. Data lives in Firebase Realtime Database (`boards/<name>`).

## Live pulls from the game (relay)

`relay/ffxiv_relay.py` listens on port 49002 (UDP, TCP or HTTP POST) for combat lines and creates a
"Pull #N" slide on every `combat_start`, with a shape for each cast/effect at its in-game position.
Run it on the PC that sends the lines: double-click `relay/start_relay.bat` (Windows, needs Python 3.8+),
or `python relay/ffxiv_relay.py --help` for options (`--board`, `--types effect`, `--cone 120`, ...).

### Fights
On the website use **+ New fight** before starting a new boss: pulls restart at #1 and are grouped under that fight.
The relay asks the board which fight is recording at every `combat_start`, so you never need to restart it.
Each fight can store an arena (custom shapes included) that new pulls start with.
