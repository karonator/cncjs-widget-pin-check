#!/usr/bin/env python3
"""Fake Grbl 1.1 on a virtual serial port, for testing cncjs without a machine.

Creates a pty and symlinks it to ~/fake-grbl-tty, then answers like Grbl:
startup banner, `ok` for lines, `$$`/`$I`/`$G`, and `<...|Pn:...>` status
reports for the `?` real-time query.

Keys (when run in a terminal):
    x y z p   toggle X / Y / Z limit and probe pins
    c         clear all pins
    q         quit

    --demo    instead of keys, cycle X -> Y -> Z -> P -> all -> none every second
"""
import argparse
import os
import select
import signal
import sys
import termios
import time
import tty

LINK = os.path.expanduser('~/fake-grbl-tty')
PIN_ORDER = 'XYZPDHRS'  # order Grbl prints Pn: letters in
BANNER = b"\r\nGrbl 1.1h ['$' for help]\r\n"
SETTINGS = [
    '$0=10', '$1=25', '$2=0', '$3=0', '$4=0', '$5=0', '$6=0',
    '$10=1', '$11=0.010', '$12=0.002', '$13=0',
    '$20=0', '$21=0', '$22=1', '$23=0', '$24=25.000', '$25=500.000',
    '$100=250.000', '$101=250.000', '$102=250.000',
    '$110=500.000', '$111=500.000', '$112=500.000',
    '$120=10.000', '$121=10.000', '$122=10.000',
    '$130=200.000', '$131=200.000', '$132=200.000',
]
DEMO_STEPS = ['X', 'Y', 'Z', 'P', 'XYZP', '']
IDLE_RESET_SEC = 3  # no traffic this long => treat next byte as a fresh connection


class FakeGrbl:
    def __init__(self, master):
        self.master = master
        self.pins = set()
        self.line = b''
        self.connected = False
        self.last_rx = 0.0

    def send(self, data):
        os.write(self.master, data)

    def status(self):
        pn = ''.join(c for c in PIN_ORDER if c in self.pins)
        report = '<Idle|MPos:0.000,0.000,0.000|FS:0,0'
        if pn:  # Grbl omits Pn: entirely when nothing is triggered
            report += '|Pn:' + pn
        return (report + '>\r\n').encode()

    def handle_line(self, line):
        line = line.strip()
        if line == '$$':
            out = SETTINGS
        elif line == '$I':
            out = ['[VER:1.1h.20190825:]', '[OPT:V,15,128]']
        elif line == '$G':
            out = ['[GC:G0 G54 G17 G21 G90 G94 M5 M9 T0 F0 S0]']
        elif line == '$#':
            out = ['[G54:0.000,0.000,0.000]', '[PRB:0.000,0.000,0.000:0]']
        else:
            out = []
        self.send(''.join(s + '\r\n' for s in out + ['ok']).encode())

    def feed(self, data):
        now = time.monotonic()
        if not self.connected or now - self.last_rx > IDLE_RESET_SEC:
            # A real Arduino resets on port open and prints the banner
            self.connected = True
            self.line = b''
            self.send(BANNER)
            log('host connected')
        self.last_rx = now

        for b in data:
            c = bytes([b])
            if c == b'?':
                self.send(self.status())
            elif c == b'\x18':  # soft reset
                self.line = b''
                self.send(BANNER)
            elif c in (b'!', b'~') or b >= 0x80:  # other real-time commands
                pass
            elif c in (b'\n', b'\r'):
                if self.line.strip():
                    self.handle_line(self.line.decode(errors='replace'))
                self.line = b''
            else:
                self.line += c

    def toggle(self, pin):
        self.pins ^= {pin}
        self.show()

    def set_pins(self, pins):
        self.pins = set(pins)
        self.show()

    def show(self):
        pn = ''.join(c for c in PIN_ORDER if c in self.pins) or '-'
        log(f'pins: {pn}')


def log(msg):
    sys.stdout.write(msg + '\r\n')
    sys.stdout.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--demo', action='store_true', help='cycle pins automatically')
    args = parser.parse_args()

    # Treat `kill` like Ctrl+C so the symlink gets cleaned up
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))

    master, slave = os.openpty()
    tty.setraw(slave)  # no echo / line discipline, like a real UART
    if os.path.lexists(LINK):
        os.remove(LINK)
    os.symlink(os.ttyname(slave), LINK)
    # Keep `slave` open so the pty survives cncjs closing and reopening it

    grbl = FakeGrbl(master)
    log(f'Fake Grbl on {LINK} -> {os.ttyname(slave)}')

    interactive = not args.demo and sys.stdin.isatty()
    old_attrs = None
    if interactive:
        old_attrs = termios.tcgetattr(sys.stdin)
        tty.setcbreak(sys.stdin)
        log('keys: x y z p = toggle pin, c = clear, q = quit')
    elif args.demo:
        log('demo mode: cycling pins every second (Ctrl+C to stop)')

    fds = [master] + ([sys.stdin] if interactive else [])
    step = 0
    next_step = time.monotonic()
    try:
        while True:
            if args.demo and time.monotonic() >= next_step:
                grbl.set_pins(DEMO_STEPS[step % len(DEMO_STEPS)])
                step += 1
                next_step = time.monotonic() + 1

            ready, _, _ = select.select(fds, [], [], 0.1)
            if master in ready:
                try:
                    grbl.feed(os.read(master, 1024))
                except OSError:
                    pass
            if interactive and sys.stdin in ready:
                key = sys.stdin.read(1).lower()
                if key == 'q':
                    break
                if key == 'c':
                    grbl.set_pins('')
                elif key in ('x', 'y', 'z', 'p'):
                    grbl.toggle(key.upper())
    except KeyboardInterrupt:
        pass
    finally:
        if old_attrs:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_attrs)
        # Another instance may have taken the link over since; leave theirs alone
        if os.path.islink(LINK) and os.readlink(LINK) == os.ttyname(slave):
            os.remove(LINK)


if __name__ == '__main__':
    main()
