#!/usr/bin/env python3
"""
FFXIV Mechanics Board relay
---------------------------
Listens on port 49002 for combat lines from your game tool and posts them to the
shared board (Firebase), so everyone on the board sees each pull live.

Accepts the lines over any of:  UDP,  plain TCP (one line per message / newline),
or HTTP POST (body = one or more lines).

  combat_start line   ->  creates a new slide "Pull #N" in the Pulls section
  cast / effect lines ->  creates a shape at the in-game position, named after the attack

Run:   python ffxiv_relay.py
       python ffxiv_relay.py --board savage-p2      (same as #b=savage-p2 on the website)
       python ffxiv_relay.py --types effect          (only resolved hits, no cast telegraphs)
       python ffxiv_relay.py --dry-run               (print what would be sent; post nothing)

Only needs Python 3.8+ (no extra packages).
"""
import argparse, http.client, json, math, queue, socket, socketserver, ssl, sys, threading, time

DB_HOST = "ffxiv-diagram-default-rtdb.firebaseio.com"

DEFAULT_ARENA = {"shape": "circle", "radius": 20, "cx": 100, "cy": 100, "margin": 4, "bg": "#070d1f",
                 "floor": "#1a2b5c", "floor2": "#0d1838", "edge": "#5fd3ff", "grid": True, "gridStep": 1,
                 "rings": True, "spokes": True, "cardinals": True, "clip": True, "snap": 0.5, "showTitle": True}

# Game "cast type" (shape) codes -> board shapes
CIRCLE = {2, 5, 6, 7}
CONE = {3, 13}
LINE = {4, 8, 12}
DONUT = {10}
CROSS = {11}

COLORS = {"cast": "#ff8a2b", "effect": "#ff3b3b"}


def num(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def r2(v):
    return round(v, 2)


def heading_to_rot(h):
    """Game heading (radians, 0 = south, +pi/2 = east) -> board rotation (degrees, 0 = north, clockwise)."""
    deg = 180.0 - math.degrees(h)
    return r2(((deg + 180) % 360 + 360) % 360 - 180)


class Board:
    def __init__(self, args):
        self.args = args
        self.base = f"/boards/{args.board}"
        self.q = queue.Queue()
        self.lock = threading.RLock()
        self.pull_n = 0
        self.slide = None
        self.slide_t0 = 0
        self.z = 0
        self.seq = 0
        self.conn = None
        self.rconn = None   # separate connection for reads (writer thread owns self.conn)
        self.fight_name = ""
        if not args.dry_run:
            f = self._active_fight()
            self.fight_name = (f or {}).get("name", "")
            self.pull_n = self._max_existing_pull((f or {}).get("id", ""))
        threading.Thread(target=self._writer, daemon=True).start()

    # ---------- firebase REST ----------
    def _request(self, method, path, body=None, read=False):
        attr = "rconn" if read else "conn"
        for attempt in range(3):
            try:
                if getattr(self, attr) is None:
                    setattr(self, attr, http.client.HTTPSConnection(DB_HOST, timeout=10, context=ssl.create_default_context()))
                conn = getattr(self, attr)
                data = None if body is None else json.dumps(body, separators=(",", ":"))
                conn.request(method, path + ".json", body=data, headers={"Content-Type": "application/json"})
                r = conn.getresponse()
                txt = r.read().decode("utf-8", "replace")
                if r.status >= 400:
                    print(f"  ! Firebase {r.status}: {txt[:200]}")
                    return None
                return json.loads(txt) if txt else None
            except Exception as e:  # reconnect and retry
                setattr(self, attr, None)
                if attempt == 2:
                    print(f"  ! Could not reach the board: {e}")
                time.sleep(0.4 * (attempt + 1))
        return None

    def _max_existing_pull(self, fight=""):
        slides = self._request("GET", self.base + "/slides", read=True) or {}
        return max([int(s.get("pull") or 0) for s in slides.values()
                    if isinstance(s, dict) and s.get("kind") == "pull" and (s.get("fight") or "") == fight] or [0])

    def _active_fight(self):
        """The fight picked on the website ("+ New fight" / "Record new pulls here"): newest activeAt wins."""
        fights = self._request("GET", self.base + "/fights", read=True) or {}
        best = None
        for f in fights.values():
            if isinstance(f, dict) and (best is None or (f.get("activeAt") or 0) > (best.get("activeAt") or 0)):
                best = f
        return best

    def _writer(self):
        while True:
            kind, path, body = self.q.get()
            batch = {}
            if kind == "item":
                batch[path] = body
                deadline = time.time() + 0.15          # gather a burst of attacks into one request
                while time.time() < deadline:
                    try:
                        k2, p2, b2 = self.q.get(timeout=max(0, deadline - time.time()))
                    except queue.Empty:
                        break
                    if k2 != "item":
                        self._flush(batch); batch = {}
                        self._send(k2, p2, b2)
                        break
                    batch[p2] = b2
                self._flush(batch)
            else:
                self._send(kind, path, body)

    def _flush(self, batch):
        if not batch:
            return
        if self.args.dry_run:
            for k, v in batch.items():
                print("  [dry-run] item", k, json.dumps(v)[:300])
            return
        self._request("PATCH", self.base + "/items", batch)

    def _send(self, kind, path, body):
        if self.args.dry_run:
            print("  [dry-run]", kind, path, json.dumps(body)[:300]); return
        self._request("PUT", self.base + path, body)

    # ---------- pulls ----------
    def start_pull(self, unix_ms):
        with self.lock:
            fight = None if self.args.dry_run else self._active_fight()
            fid = (fight or {}).get("id", "")
            self.fight_name = (fight or {}).get("name", "")
            # numbering restarts for each fight; read from the board so restarting the relay keeps counting
            self.pull_n = (self.pull_n if self.args.dry_run else self._max_existing_pull(fid)) + 1
            arena = (fight or {}).get("arena") or dict(DEFAULT_ARENA, cx=self.args.cx, cy=self.args.cy, radius=self.args.radius)
            sid = f"pull-{unix_ms}"
            self.slide, self.slide_t0, self.z = sid, unix_ms, 0
            slide = {"id": sid, "title": f"Pull #{self.pull_n}", "kind": "pull", "pull": self.pull_n, "fight": fid,
                     "order": self.pull_n, "startedAt": unix_ms, "arena": arena}
            self.q.put(("slide", f"/slides/{sid}", slide))
        print(f"\n=== {self.fight_name + ' - ' if self.fight_name else ''}Pull #{self.pull_n} started ===")

    def attack(self, f, line):
        typ = f[2].strip().lower()
        if typ not in self.args.types:
            return
        unix_ms = int(num(f[1], time.time() * 1000))
        with self.lock:
            if self.slide is None:
                self.start_pull(unix_ms)
        name = f[4].strip() or f"Action {f[3]}"
        shape = int(num(f[5], 0))
        length, width = num(f[7]), num(f[8])
        x, y, head = num(f[12], self.args.cx), num(f[14], self.args.cy), num(f[15])
        tail = f[20:]
        dist = tail[-1] if tail and tail[-1].strip().isdigit() and 1 <= int(tail[-1]) <= 8 else ""
        hits = ", ".join(p.split("/")[1] if p.count("/") >= 2 else p for p in tail if "/" in p)

        color = COLORS.get(typ, "#ffd43b")
        big = length >= 30
        it = {"x": r2(x), "y": r2(y), "color": color, "opacity": 0.14 if big else 0.42, "stroke": True}
        if shape in CIRCLE:
            it.update(type="circle", radius=r2(max(length, 0.5)))
        elif shape in CONE:
            it.update(type="cone", radius=r2(max(length, 0.5)), angle=self.args.cone, rot=heading_to_rot(head))
        elif shape in LINE:
            it.update(type="rect", length=r2(max(length, 0.5)), width=r2(width if width > 0 else 4),
                      anchor="back", rot=heading_to_rot(head))
        elif shape in DONUT:
            it.update(type="donut", outer=r2(max(length, 1)), inner=r2(max(length * self.args.donut, 0.5)))
        elif shape in CROSS:
            it.update(type="cross", length=r2(max(length, 0.5)), width=r2(width if width > 0 else 4),
                      rot=heading_to_rot(head))
        else:
            print(f"  · {typ:6} {name}  (shape {shape}: single-target / no area, skipped)")
            return

        with self.lock:
            self.z += 1
            self.seq += 1
            iid = f"a{unix_ms}_{self.seq}"
            it.update(id=iid, slide=self.slide, z=self.z, name=name, visible=True, locked=False,
                      label="", labelSize=1.5, auto=True, pinned=False, at=unix_ms,
                      ev={"type": typ, "actionId": f[3], "action": name, "shape": shape, "len": r2(length),
                          "wid": r2(width), "src": f[10], "tgt": f[18] if len(f) > 18 else "",
                          "castTime": f[20] if len(f) > 20 and num(f[20]) > 0 else "",
                          "dist": dist, "hits": hits[:300], "raw": line[:600]})
        self.q.put(("item", iid, it))
        rel = (unix_ms - self.slide_t0) / 1000
        print(f"  +{rel:6.1f}s {typ:6} {name:28} {it['type']:6} at ({x:.1f}, {y:.1f})")

    def handle(self, line):
        line = line.strip().strip("\x00")
        if not line:
            return
        f = line.split("|")
        if len(f) < 3:
            return
        typ = f[2].strip().lower()
        if typ == "combat_start":
            self.start_pull(int(num(f[1], time.time() * 1000)))
        elif typ in ("combat_end", "combat_stop", "wipe"):
            print(f"=== {typ} ===")
        elif len(f) >= 17:
            try:
                self.attack(f, line)
            except Exception as e:
                print(f"  ! Could not read line ({e}): {line[:120]}")


def main():
    ap = argparse.ArgumentParser(description="Relay FFXIV combat lines to the shared mechanics board.")
    ap.add_argument("--port", type=int, default=49002)
    ap.add_argument("--host", default="127.0.0.1", help="interface to listen on (default: this PC only)")
    ap.add_argument("--board", default="raid", help="board name (matches #b=name in the website URL)")
    ap.add_argument("--types", default="cast,effect", help="which line types become shapes (default cast,effect)")
    ap.add_argument("--cone", type=float, default=90, help="cone angle in degrees (not in the data; default 90)")
    ap.add_argument("--donut", type=float, default=0.4, help="donut inner radius as a fraction of outer (default 0.4)")
    ap.add_argument("--cx", type=float, default=100); ap.add_argument("--cy", type=float, default=100)
    ap.add_argument("--radius", type=float, default=20, help="arena radius for new pull slides")
    ap.add_argument("--dry-run", action="store_true", help="print instead of posting to the board")
    args = ap.parse_args()
    args.types = {t.strip().lower() for t in args.types.split(",") if t.strip()}

    board = Board(args)

    class UDP(socketserver.BaseRequestHandler):
        def handle(self):
            for ln in self.request[0].decode("utf-8", "replace").splitlines():
                board.handle(ln)

    class TCP(socketserver.StreamRequestHandler):
        def handle(self):
            first = self.rfile.readline().decode("utf-8", "replace")
            if first.split(" ")[0] in ("POST", "PUT", "OPTIONS", "GET"):   # HTTP
                length = 0
                while True:
                    h = self.rfile.readline().decode("latin-1").strip()
                    if not h:
                        break
                    if h.lower().startswith("content-length:"):
                        length = int(h.split(":", 1)[1].strip() or 0)
                body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
                for ln in body.splitlines():
                    board.handle(ln)
                self.wfile.write(b"HTTP/1.1 200 OK\r\nAccess-Control-Allow-Origin: *\r\n"
                                 b"Access-Control-Allow-Headers: *\r\nAccess-Control-Allow-Methods: POST, OPTIONS\r\n"
                                 b"Content-Length: 2\r\nConnection: close\r\n\r\nok")
                return
            board.handle(first)
            for raw in self.rfile:
                board.handle(raw.decode("utf-8", "replace"))

    socketserver.ThreadingUDPServer.allow_reuse_address = True
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    socketserver.ThreadingTCPServer.daemon_threads = True
    try:
        udp = socketserver.ThreadingUDPServer((args.host, args.port), UDP)
        tcp = socketserver.ThreadingTCPServer((args.host, args.port), TCP)
    except OSError as e:
        print(f"Port {args.port} is busy ({e}). Close the other program using it, or run with --port.")
        sys.exit(1)
    for srv in (udp, tcp):
        threading.Thread(target=srv.serve_forever, daemon=True).start()

    print(f"FFXIV board relay listening on {args.host}:{args.port} (UDP, TCP and HTTP)")
    print(f"Board: '{args.board}'  ->  https://lestoli.github.io/ffxiv-board/" + ("" if args.board == "raid" else f"#b={args.board}"))
    print(f"Shapes from: {', '.join(sorted(args.types))}.")
    print(f"Recording fight: {board.fight_name or '(none - pulls go to Unsorted; use + New fight on the website)'}  (last pull #{board.pull_n})")
    print("Waiting for combat_start... (Ctrl+C to stop)\n")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping relay. Pull slides stay on the board.")


if __name__ == "__main__":
    main()
