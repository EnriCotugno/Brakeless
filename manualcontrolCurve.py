import math
import os
import sys
import pygame
import snakeoil as snakeoil3
import time
from datetime import datetime

# ── Parametri controller (da JoystickController) ──────────────────────────────
UPSHIFT_RPM = {1: 5500, 2: 7500, 3: 8500, 4: 9500, 5: 10500}
DOWNSHIFT_RPM = {2: 3500, 3: 4500, 4: 5500, 5: 6500, 6: 7500}
SHIFT_COOLDOWN     = 0.5   # secondi minimi tra due cambi marcia
SOFT_REV_LIMIT_RPM = 15200 # rev limiter morbido
HARD_REV_LIMIT_RPM = 15800 # rev limiter duro
TCS_SLIP_THRESHOLD = 3.0   # soglia slittamento controllo trazione
ACCEL_SMOOTH       = 0.55  # interpolazione acceleratore (0=istantaneo)
BRAKE_SMOOTH       = 0.50  # interpolazione freno
MIN_STEER_FACTOR   = 0.45  # smorzamento sterzo ad alta velocità
STEER_SMOOTH       = 0.25  # velocità di rotazione ruote
STEER_CENTERING    = 0.10  # velocità ritorno volante al centro
SPEED_STEER_DAMP   = 183   # fattore smorzamento sterzo vs velocità

class ArcadeController:
    def __init__(self):
        pygame.init()
        pygame.joystick.init()
        
        if pygame.joystick.get_count() == 0:
            print("❌ Nessun Gamepad rilevato! Collega il controller Xbox o PS.")
            sys.exit()
        
        self.joystick = pygame.joystick.Joystick(0)
        self.joystick.init()
        print(f"🎮 Controller rilevato: {self.joystick.get_name()}")

        self.state = {'steer': 0.0, 'accel': 0.0, 'brake': 0.0, 'gear': 1}
        self._last_shift_time = time.time()
        
        # --- Nuove variabili per il toggle manuale ---
        self.is_recording = False
        self._last_button_state = 0

    def auto_shift_gear(self, speed, rpm=0.0):
        now = time.time()
        if speed < 10.0 and self.state['gear'] > 1:
            self.state['gear'] = 1
            self._last_shift_time = now
            return

        if now - self._last_shift_time < SHIFT_COOLDOWN:
            return

        upshift = ({k: v * 0.8 for k, v in UPSHIFT_RPM.items()} if speed < 20.0 else UPSHIFT_RPM)
        gear = self.state['gear']
        if gear < 6 and rpm > upshift.get(gear, 13000):
            self.state['gear'] += 1
            self._last_shift_time = now
        elif gear > 1 and rpm < DOWNSHIFT_RPM.get(gear, 0):
            self.state['gear'] -= 1
            self._last_shift_time = now

        self.state['gear'] = max(1, min(6, self.state['gear']))

    def update(self, sensors):
        pygame.event.clear()
        pygame.event.pump()

        # --- CONTROLLO PULSANTE REGISTRAZIONE (Cerchio/B) ---
        # L'indice 1 corrisponde al tasto Cerchio (PS) o B (Xbox) sulla maggior parte dei driver.
        # Se non dovesse funzionare, prova l'indice 2.
        current_button_state = self.joystick.get_button(1)
        
        # Rileva solo la *pressione* del tasto (transizione da 0 a 1)
        if current_button_state == 1 and self._last_button_state == 0:
            self.is_recording = not self.is_recording
            if self.is_recording:
                print("\n🔴 REGISTRAZIONE AVVIATA")
            else:
                print("\n⏹️ REGISTRAZIONE FERMATA")
                
        self._last_button_state = current_button_state

        speed = sensors.get('speedX', 0.0)
        rpm   = sensors.get('rpm',    0.0)

        # --- STERZO ---
        raw_steer = -self.joystick.get_axis(0)
        if abs(raw_steer) < 0.05:
            raw_steer = 0.0

        speed_factor = max(MIN_STEER_FACTOR, 1.0 - speed / SPEED_STEER_DAMP)
        target_steer = raw_steer * speed_factor

        current_steer = self.state['steer']
        if abs(raw_steer) > 0.0:
            self.state['steer'] = current_steer + (target_steer - current_steer) * STEER_SMOOTH
        else:
            self.state['steer'] = current_steer * (1.0 - STEER_CENTERING)

        # --- ACCELERATORE ---
        accel_raw = (self.joystick.get_axis(5) + 1.0) / 2.0
        if accel_raw < 0.05:
            accel_raw = 0.0

        if rpm > HARD_REV_LIMIT_RPM:
            accel_raw = 0.0
        elif rpm > SOFT_REV_LIMIT_RPM:
            accel_raw *= 1.0 - (rpm - SOFT_REV_LIMIT_RPM) / (HARD_REV_LIMIT_RPM - SOFT_REV_LIMIT_RPM)

        wheel_slip = sensors.get('wheelSpinVel', [0.0] * 4)
        if isinstance(wheel_slip, (list, tuple)) and len(wheel_slip) == 4:
            rear_slip = (wheel_slip[2] + wheel_slip[3]) / 2.0
            front_ref = (wheel_slip[0] + wheel_slip[1]) / 2.0
            if front_ref > 0 and (rear_slip / (front_ref + 1e-6)) > TCS_SLIP_THRESHOLD:
                accel_raw *= 0.5

        self.state['accel'] += (accel_raw - self.state['accel']) * (1.0 - ACCEL_SMOOTH)

        # --- FRENO ---
        brake_raw = (self.joystick.get_axis(4) + 1.0) / 2.0
        if brake_raw < 0.05:
            brake_raw = 0.0
        self.state['brake'] += (brake_raw - self.state['brake']) * (1.0 - BRAKE_SMOOTH)

        self.state['steer'] = max(-1.0, min(1.0, self.state['steer']))
        self.state['accel'] = max(0.0,  min(1.0, self.state['accel']))
        self.state['brake'] = max(0.0,  min(1.0, self.state['brake']))


## FUNZIONI DI SALVATAGGIO
def save_lap(output_dir, lap_buffer_csv, lap_time):
    """Salva un giro intero."""
    track_headers = ",".join([f"track_{i}" for i in range(19)])
    csv_header = f"time,steer,accel,brake,gear,speedX,speedY,speedZ,trackPos,angle,rpm,damage,wheelSpin_0,wheelSpin_1,wheelSpin_2,wheelSpin_3,{track_headers}\n"
 
    ts = datetime.now().strftime("%H%M%S")
    time_label = f"{lap_time:.2f}" if lap_time > 0 else "partial"
    filename = f"lap_{time_label}_{ts}.csv"
    csv_path = os.path.join(output_dir, filename)
 
    with open(csv_path, "w") as f:
        f.write(csv_header)
        f.writelines(lap_buffer_csv)
    print(f"✅ Giro completo ({lap_time:.2f}s) salvato in '{filename}'")

def save_segment(output_dir, lap_buffer_csv):
    """Salva uno spezzone manuale (es. singola curva)."""
    track_headers = ",".join([f"track_{i}" for i in range(19)])
    csv_header = f"time,steer,accel,brake,gear,speedX,speedY,speedZ,trackPos,angle,rpm,damage,wheelSpin_0,wheelSpin_1,wheelSpin_2,wheelSpin_3,{track_headers}\n"
 
    ts = datetime.now().strftime("%H%M%S")
    filename = f"corner_ideal_{ts}.csv"
    csv_path = os.path.join(output_dir, filename)
 
    with open(csv_path, "w") as f:
        f.write(csv_header)
        f.writelines(lap_buffer_csv)
    print(f"✅ Spezzone salvato in '{filename}' ({len(lap_buffer_csv)} frames)")


# ============================================================
# MAIN
# ============================================================
def main():
    OUTPUT_DIR = "corner_1_ideal"
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    client = snakeoil3.Client(p=3001, vision=False)
    controller = ArcadeController()
    print(f"📁 I giri e gli spezzoni verranno salvati in '{OUTPUT_DIR}/'")

    client.get_servers_input()
    print("Arcade driving mode attivo")
    
    track_headers = ",".join([f"track_{i}" for i in range(19)])
    csv_header = f"time,steer,accel,brake,gear,speedX,speedY,speedZ,trackPos,angle,rpm,damage,wheelSpin_0,wheelSpin_1,wheelSpin_2,wheelSpin_3,{track_headers}\n"
    
    with open("manual_log.csv", "w") as f:
        f.write(csv_header)

    lap_buffer_csv = []
    
    is_lap_valid = True
    last_damage = 0
    last_lap_time_prev = 0
    t0 = time.time()
    
    # Tiene traccia dello stato di registrazione per capire quando viene fermato
    was_recording = False 

    print("🏁 Premi il tasto 'CERCHIO/B' sul controller per avviare/fermare la registrazione.")

    try:
        while True:
            client.get_servers_input()
            S = client.S.d
            controller.update(S)
            controller.auto_shift_gear(S.get('speedX', 0), S.get('rpm', 0.0))
            a = controller.state

            client.R.d.update({'steer': a['steer'], 'accel': a['accel'], 'brake': a['brake'], 'gear': a['gear']})
            client.respond_to_server()
            
            current_damage = S.get('damage', 0)
            track_pos = S.get('trackPos', 0)
            is_currently_recording = controller.is_recording

            # --- RACCOLTA DATI SE ATTIVA ---
            if is_currently_recording:
                if current_damage > last_damage or abs(track_pos) > 1.7:
                    if is_lap_valid:
                        print("⚠️ Errore (Danno o Fuori Pista). Questo spezzone/giro verrà ignorato.")
                        is_lap_valid = False

                wheel_slip = S.get('wheelSpinVel', [0.0, 0.0, 0.0, 0.0])
                if not isinstance(wheel_slip, (list, tuple)) or len(wheel_slip) < 4:
                    wheel_slip = [0.0, 0.0, 0.0, 0.0]

                track_sensors = S.get('track', [0.0]*19)
                track_str = ",".join(map(str, track_sensors))
                
                csv_row = (f"{time.time()-t0:.3f},{a['steer']:.4f},{a['accel']:.4f},{a['brake']:.4f},{a['gear']},"
                           f"{S.get('speedX',0):.2f},{S.get('speedY',0):.2f},{S.get('speedZ',0):.2f},"
                           f"{track_pos:.4f},{S.get('angle',0):.4f},{S.get('rpm',0)},{current_damage},"
                           f"{wheel_slip[0]:.2f},{wheel_slip[1]:.2f},{wheel_slip[2]:.2f},{wheel_slip[3]:.2f},"
                           f"{track_str}\n")
                
                lap_buffer_csv.append(csv_row)

            # --- TRIGGER FINE REGISTRAZIONE MANUALE ---
            # Se la registrazione era attiva al frame precedente e ora è disattivata
            if was_recording and not is_currently_recording:
                if is_lap_valid and len(lap_buffer_csv) > 10:
                    save_segment(OUTPUT_DIR, lap_buffer_csv)
                else:
                    print("❌ Spezzone scartato (Troppo corto o non valido).")
                
                # Reset
                lap_buffer_csv = []
                is_lap_valid = True

            # --- CONTROLLO FINE GIRO (Funziona anche se registri tutto il giro manualmente) ---
            current_lap_time = S.get('lastLapTime', 0)
            if current_lap_time > 0 and current_lap_time != last_lap_time_prev:
                if is_currently_recording: # Salva solo se stavamo effettivamente registrando
                    if is_lap_valid:
                        save_lap(OUTPUT_DIR, lap_buffer_csv, current_lap_time)
                    else:
                        print(f"❌ Giro completato ma SCARTATO (non valido).")
                    
                    lap_buffer_csv = []
                    is_lap_valid = True
                last_lap_time_prev = current_lap_time

            last_damage = current_damage
            was_recording = is_currently_recording
            time.sleep(0.005)

    except KeyboardInterrupt:
        print("\n🛑 Sessione interrotta dall'utente.")
        sys.exit()
    
if __name__ == "__main__":
    main()