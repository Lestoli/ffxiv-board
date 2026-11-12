# FFXIV Mechanics Board

A live, collaborative raid-mechanics diagrammer. Open the site, type your name, and everyone on the board sees each other's cursors and edits in real time.

- Slides for multiple arenas (duplicate / copy / reorder / delete)
- AoE shapes in yalms: circle, donut, rectangle/line, cone, cross, 8-way star, tower, boss
- Player markers, waymarks, pen, eraser, text, arrows, tethers with live distance
- Shapes can target a player and auto-aim at them
- Export PNG / zip of all slides; JSON backup and import

Add `#b=name` to the URL to open a separate board. Data lives in Firebase Realtime Database (`boards/<name>`).
