"""
keyboardcontrol.py — Guida manuale con tastiera per TORCS

Tasti:
  W / ↑   Accelera
  S / ↓   Frena
  A / ←   Sterza sinistra
  D / →   Sterza destra
  R       Reset giro (invalida il buffer corrente)
  Q / ESC Esci
"""

import os
import sys
import time
import json
import threading
from pynput import keyboard
import snakeoil as snakeoil3

# ── Parametri guida tastiera ───────────────────────────────────────────────────
ACCEL_STEP       = 0.08
BRAKE_STEP       = 0.10
RELEASE_STEP     = 0.12
STEER_STEP       = 0.06
STEER_RETURN     = 0.08
MIN_STEER_FACTOR = 0.45
SPEED_STEER_DAMP = 183
POLL_HZ          = 60    # frequenza aggiornamento comandi (Hz)

# ── Cambio automatico basato su RPM ───────────────────────────────────────────
UPSHIFT_RPM   = {1: 5500, 2: 7500, 3: 8500, 4: 9500, 5: 10500}
DOWNSHIFT_RPM = {2: 3500, 3: 4500, 4: 5500, 5: 6500, 6: 7500}
SHIFT_COOLDOWN = 0.5

# ── Validazione giro ───────────────────────────────────────────────────────────
TRACK_LIMIT = 1.2
OUTPUT_DIR  = "laps_keyboard"


# ── Controller tastiera ────────────────────────────────────────────────────────

class KeyboardController:
    """
    pynput aggiorna _keys (set dei tasti premuti) tramite callback on_press/on_release.
    Il thread _update_loop legge _keys a POLL_HZ e aggiorna self.state.
    Il loop TORCS chiama get_state() senza mai bloccarsi.
    """

    # Tasti riconosciuti
    _KEY_ACCEL  = {keyboard.KeyCode.from_char('w'), keyboard.Key.up}
    _KEY_BRAKE  = {keyboard.KeyCode.from_char('s'), keyboard.Key.down}
    _KEY_LEFT   = {keyboard.KeyCode.from_char('a'), keyboard.Key.left}
    _KEY_RIGHT  = {keyboard.KeyCode.from_char('d'), keyboard.Key.right}
    _KEY_RESET  = {keyboard.KeyCode.from_char('r')}
    _KEY_QUIT   = {keyboard.KeyCode.from_char('q'), keyboard.Key.esc}

    def __init__(self):
        self.state           = {'steer': 0.0, 'accel': 0.0, 'brake': 0.0, 'gear': 1}
        self._sensors        = {}
        self._keys           = set()   # tasti attualmente premuti
        self._lock           = threading.Lock()
        self._last_shift     = time.time()
        self.quit_requested  = False
        self.reset_requested = False

        # Listener pynput — callback in thread separato gestito da pynput
        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.start()

        # Thread che trasforma i tasti premuti in comandi continui
        self._update_thread = threading.Thread(target=self._update_loop, daemon=True)
        self._update_thread.start()

        print("⌨️  Controllo tastiera attivo (nessuna finestra — pynput).")
        print("    W/↑ Accelera  |  S/↓ Frena  |  A/← Sinistra  |  D/→ Destra")
        print("    R = invalida giro  |  Q / ESC = esci\n")

    # ── Callback pynput ────────────────────────────────────────────────────────

    def _on_press(self, key) -> None:
        with self._lock:
            self._keys.add(key)
        # Azioni one-shot su KEYDOWN
        if key in self._KEY_QUIT:
            self.quit_requested = True
        if key in self._KEY_RESET:
            self.reset_requested = True

    def _on_release(self, key) -> None:
        with self._lock:
            self._keys.discard(key)

    def _pressed(self, key_set: set) -> bool:
        with self._lock:
            return bool(self._keys & key_set)

    # ── Thread aggiornamento comandi ───────────────────────────────────────────

    def _update_loop(self) -> None:
        interval = 1.0 / POLL_HZ
        while not self.quit_requested:
            t_start = time.time()

            with self._lock:
                sensors = dict(self._sensors)
                state   = dict(self.state)

            speed = sensors.get('speedX', 0.0)
            rpm   = sensors.get('rpm',    0.0)

            # ── Sterzo ────────────────────────────────────────────────────────
            speed_factor = max(MIN_STEER_FACTOR, 1.0 - speed / SPEED_STEER_DAMP)
            steer_step   = STEER_STEP * speed_factor

            if self._pressed(self._KEY_LEFT):
                state['steer'] = min(1.0,  state['steer'] + steer_step)
            elif self._pressed(self._KEY_RIGHT):
                state['steer'] = max(-1.0, state['steer'] - steer_step)
            else:
                s = state['steer']
                state['steer'] = 0.0 if abs(s) < STEER_RETURN else s - STEER_RETURN * (1 if s > 0 else -1)

            # ── Acceleratore ──────────────────────────────────────────────────
            if self._pressed(self._KEY_ACCEL):
                state['accel'] = min(1.0, state['accel'] + ACCEL_STEP)
                state['brake'] = 0.0
            else:
                state['accel'] = max(0.0, state['accel'] - RELEASE_STEP)

            # ── Freno ─────────────────────────────────────────────────────────
            if self._pressed(self._KEY_BRAKE):
                state['brake'] = min(1.0, state['brake'] + BRAKE_STEP)
                state['accel'] = 0.0
            else:
                state['brake'] = max(0.0, state['brake'] - RELEASE_STEP)

            # ── Cambio automatico ──────────────────────────────────────────────
            state['gear'] = self._auto_shift(state['gear'], speed, rpm)

            with self._lock:
                self.state = state

            # Sleep preciso per rispettare POLL_HZ
            elapsed = time.time() - t_start
            sleep   = interval - elapsed
            if sleep > 0:
                time.sleep(sleep)

    def _auto_shift(self, gear: int, speed: float, rpm: float) -> int:
        now = time.time()
        if speed < 10.0 and gear > 1:
            self._last_shift = now
            return 1
        if now - self._last_shift < SHIFT_COOLDOWN:
            return gear
        upshift = (
            {k: v * 0.8 for k, v in UPSHIFT_RPM.items()}
            if speed < 20.0 else UPSHIFT_RPM
        )
        if gear < 6 and rpm > upshift.get(gear, 13000):
            self._last_shift = now
            return min(6, gear + 1)
        if gear > 1 and rpm < DOWNSHIFT_RPM.get(gear, 0):
            self._last_shift = now
            return max(1, gear - 1)
        return gear

    # ── API pubblica ───────────────────────────────────────────────────────────

    def push_sensors(self, sensors: dict) -> None:
        with self._lock:
            self._sensors = sensors

    def get_state(self) -> dict:
        with self._lock:
            return dict(self.state)

    def stop(self) -> None:
        self.quit_requested = True
        self._listener.stop()
        self._update_thread.join(timeout=1.0)


# ── Salvataggio ────────────────────────────────────────────────────────────────

def save_lap(output_dir, lap_buffer_csv, lap_buffer_json, lap_time):
    track_headers = ",".join([f"track_{i}" for i in range(19)])
    csv_header    = f"time,steer,accel,brake,gear,speedX,trackPos,angle,rpm,damage,{track_headers}\n"
    time_str  = f"{lap_time:.2f}" if lap_time > 0 else "partial"
    csv_path  = os.path.join(output_dir, f"lap_{time_str}.csv")
    json_path = os.path.join(output_dir, f"lap_{time_str}.json")
    with open(csv_path, "w") as f:
        f.write(csv_header)
        f.writelines(lap_buffer_csv)
    with open(json_path, "w") as f:
        json.dump(lap_buffer_json, f, indent=2)
    print(f"✅ Giro completato in {lap_time:.2f}s — salvato in '{csv_path}'")


# ── Main ────────────────────────────────────────────────────────────────────────

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    client     = snakeoil3.Client(p=3001, vision=False)
    controller = KeyboardController()
    print(f"📁 Giri salvati in '{OUTPUT_DIR}/'")

    client.get_servers_input()
    print("🏁 Registrazione attiva. Guida pulita per popolare il dataset!")

    lap_buffer_csv  = []
    lap_buffer_json = []
    is_lap_valid    = True
    last_damage     = 0
    last_lap_time   = 0
    t0              = time.time()

    try:
        while not controller.quit_requested:
            client.get_servers_input()
            S = client.S.d

            controller.push_sensors(S)

            if controller.reset_requested:
                controller.reset_requested = False
                lap_buffer_csv, lap_buffer_json = [], []
                is_lap_valid = True
                print("🔄 Buffer azzerato.")

            a = controller.get_state()

            client.R.d.update({
                'steer': a['steer'], 'accel': a['accel'],
                'brake': a['brake'], 'gear':  a['gear'],
            })
            client.respond_to_server()

            current_damage = S.get('damage', 0)
            track_pos      = S.get('trackPos', 0)

            if current_damage > last_damage or abs(track_pos) > TRACK_LIMIT:
                if is_lap_valid:
                    print("⚠️  Giro invalidato (Danno o Fuori Pista). Dati scartati.")
                    is_lap_valid = False
                    lap_buffer_csv, lap_buffer_json = [], []

            track_sensors = S.get('track', [0.0] * 19)
            track_str     = ",".join(map(str, track_sensors))

            csv_row = (
                f"{time.time()-t0:.3f},{a['steer']:.4f},{a['accel']:.4f},"
                f"{a['brake']:.4f},{a['gear']},"
                f"{S.get('speedX',0):.2f},{track_pos:.4f},{S.get('angle',0):.4f},"
                f"{S.get('rpm',0)},{current_damage},{track_str}\n"
            )
            json_step = {
                "sensors": {
                    "speedX": S.get('speedX'), "trackPos": track_pos,
                    "angle":  S.get('angle'),  "track": track_sensors,
                },
                "actions": {
                    "steer": a['steer'], "accel": a['accel'],
                    "brake": a['brake'], "gear":  a['gear'],
                },
            }
            lap_buffer_csv.append(csv_row)
            lap_buffer_json.append(json_step)

            current_lap_time = S.get('lastLapTime', 0)
            if current_lap_time > 0 and current_lap_time != last_lap_time:
                if is_lap_valid:
                    save_lap(OUTPUT_DIR, lap_buffer_csv, lap_buffer_json, current_lap_time)
                else:
                    print("❌ Giro CONCLUSO MA SCARTATO (non valido).")
                lap_buffer_csv, lap_buffer_json = [], []
                is_lap_valid  = True
                last_lap_time = current_lap_time

            last_damage = current_damage

    except KeyboardInterrupt:
        print("\n🛑 Sessione interrotta.")

    finally:
        # Pulizia finale
        #print(f"Dataset finale pronto: 'manual_log.csv' e 'manual_log.json'")
        sys.exit()


if __name__ == "__main__":
    main()