import math
import os
import sys
import pygame
from pynput.keyboard import Key, Listener
import snakeoil as snakeoil3
import time
from datetime import datetime
import json


# ── Parametri controller (da JoystickController) ──────────────────────────────
UPSHIFT_RPM = {1: 5500, 2: 7500, 3: 8500, 4: 9500, 5: 10500}
DOWNSHIFT_RPM = {2: 3500, 3: 4500, 4: 5500, 5: 6500, 6: 7500}
SHIFT_COOLDOWN     = 0.5   # secondi minimi tra due cambi marcia
SOFT_REV_LIMIT_RPM = 13200 # rev limiter morbido
HARD_REV_LIMIT_RPM = 13800 # rev limiter duro
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
            print("❌ Nessun Gamepad rilevato! Collega il controller Xbox.")
            sys.exit()
        
        self.joystick = pygame.joystick.Joystick(0)
        self.joystick.init()
        print(f"🎮 Controller rilevato: {self.joystick.get_name()}")

        self.state = {'steer': 0.0, 'accel': 0.0, 'brake': 0.0, 'gear': 1}
        self._last_shift_time = time.time()



    # cambio automatico basato su RPM (più preciso della versione solo-velocità)
    def auto_shift_gear(self, speed, rpm=0.0):
        """
        Cambia automaticamente la marcia in base a RPM e velocità.
        - Forza la 1ª sotto i 10 km/h
        - Scala su/giù rispettando soglie RPM e cooldown
        - Abbassa le soglie di upshift del 20% a bassa velocità (<20 km/h)
        """
        now = time.time()

        if speed < 10.0 and self.state['gear'] > 1:
            self.state['gear'] = 1
            self._last_shift_time = now
            return

        if now - self._last_shift_time < SHIFT_COOLDOWN:
            return

        upshift = (
            {k: v * 0.8 for k, v in UPSHIFT_RPM.items()}
            if speed < 20.0 else UPSHIFT_RPM
        )

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

        speed = sensors.get('speedX', 0.0)
        rpm   = sensors.get('rpm',    0.0)

        # --- STERZO con smoothing e smorzamento velocità-dipendente ---
        raw_steer = -self.joystick.get_axis(0)
        if abs(raw_steer) < 0.05:
            raw_steer = 0.0

        # Smorzamento: più vai veloce, meno angolo applichi
        speed_factor = max(MIN_STEER_FACTOR, 1.0 - speed / SPEED_STEER_DAMP)
        target_steer = raw_steer * speed_factor

        current_steer = self.state['steer']
        if abs(raw_steer) > 0.0:
            # Interpolazione verso il target (più reattivo)
            self.state['steer'] = current_steer + (target_steer - current_steer) * STEER_SMOOTH
        else:
            # Ritorno al centro quando la levetta è a riposo
            self.state['steer'] = current_steer * (1.0 - STEER_CENTERING)

        # --- ACCELERATORE con smoothing e rev limiter ---
        accel_raw = (self.joystick.get_axis(5) + 1.0) / 2.0
        if accel_raw < 0.05:
            accel_raw = 0.0

        # Rev limiter morbido/duro
        if rpm > HARD_REV_LIMIT_RPM:
            accel_raw = 0.0
        elif rpm > SOFT_REV_LIMIT_RPM:
            accel_raw *= 1.0 - (rpm - SOFT_REV_LIMIT_RPM) / (HARD_REV_LIMIT_RPM - SOFT_REV_LIMIT_RPM)

        # TCS: riduci gas se le ruote slittano troppo
        wheel_slip = sensors.get('wheelSpinVel', [0.0] * 4)
        if isinstance(wheel_slip, (list, tuple)) and len(wheel_slip) == 4:
            rear_slip = (wheel_slip[2] + wheel_slip[3]) / 2.0
            front_ref = (wheel_slip[0] + wheel_slip[1]) / 2.0
            if front_ref > 0 and (rear_slip / (front_ref + 1e-6)) > TCS_SLIP_THRESHOLD:
                accel_raw *= 0.5

        self.state['accel'] += (accel_raw - self.state['accel']) * (1.0 - ACCEL_SMOOTH)

        # --- FRENO con smoothing ---
        brake_raw = (self.joystick.get_axis(4) + 1.0) / 2.0
        if brake_raw < 0.05:
            brake_raw = 0.0
        self.state['brake'] += (brake_raw - self.state['brake']) * (1.0 - BRAKE_SMOOTH)

        # Clamp finale
        self.state['steer'] = max(-1.0, min(1.0, self.state['steer']))
        self.state['accel'] = max(0.0,  min(1.0, self.state['accel']))
        self.state['brake'] = max(0.0,  min(1.0, self.state['brake']))


## FUNZIONI DI SALVATAGGIO
def save_lap(output_dir, lap_buffer_csv, lap_buffer_json, lap_time):
    """Salva un giro pulito nei file CSV e JSON dedicati."""
    track_headers = ",".join([f"track_{i}" for i in range(19)])
    csv_header = f"time,steer,accel,brake,gear,speedX,trackPos,angle,rpm,damage,{track_headers}\n"
 
    # Nome file basato su lastLapTime (es. lap_87.43.csv), o "partial" se salvato da Ctrl+C
    time_str = f"{lap_time:.2f}" if lap_time > 0 else "partial"
    csv_path  = os.path.join(output_dir, f"lap_{time_str}.csv")
    json_path = os.path.join(output_dir, f"lap_{time_str}.json")
 
    with open(csv_path, "w") as f:
        f.write(csv_header)
        f.writelines(lap_buffer_csv)
 
    with open(json_path, "w") as f:
        json.dump(lap_buffer_json, f, indent=2)
 
    print(f"✅ Giro completato in {lap_time:.2f}s — salvato in '{csv_path}'")
 


# ============================================================
# MAIN
# ============================================================

def main():
    # Cartella dove vengono salvati i giri
    OUTPUT_DIR = "laps"
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    client = snakeoil3.Client(p=3001, vision=False)
    controller = ArcadeController()
    print(f"📁 I giri puliti verranno salvati in '{OUTPUT_DIR}/'")

    client.get_servers_input()

    print("Arcade driving mode attivo")
    
    
    # Inizializzazione file CSV con intestazioni complete per AI
    # Includiamo i 19 sensori 'track' che sono vitali per il KNN
    track_headers = ",".join([f"track_{i}" for i in range(19)])
    csv_header = f"time,steer,accel,brake,gear,speedX,trackPos,angle,rpm,damage,{track_headers}\n"
    
    with open("manual_log.csv", "w") as f:
        f.write(csv_header)

    # Variabili di stato per il logging
    lap_buffer_csv = []
    lap_buffer_json = []
    all_clean_data_json = []
    
    is_lap_valid = True
    last_damage = 0
    last_lap_time_prev = 0
    t0 = time.time()

    print("🏁 Registrazione attiva. Guida pulita per popolare il dataset!")

    try:
        while True:
            client.get_servers_input()
            S = client.S.d
            controller.update(S)
            controller.auto_shift_gear(S.get('speedX', 0), S.get('rpm', 0.0))
            a = controller.state

            # Invio comandi
            client.R.d.update({'steer': a['steer'], 'accel': a['accel'], 'brake': a['brake'], 'gear': a['gear']})
            client.respond_to_server()
            

            # --- MONITORAGGIO VALIDITÀ GIRO ---
            current_damage = S.get('damage', 0)
            track_pos = S.get('trackPos', 0)

            #print("Dati ricevuti:", S.keys())
            #print(f"laps={S.get('laps',0)} dist={S.get('distRaced',0):.1f} pos={track_pos:.2f}", end='\r')
            
            # Se colpisci qualcosa o esci dai bordi (1.0 = bordo), invalida il buffer attuale
            if current_damage > last_damage or abs(track_pos) > 1.4:
                if is_lap_valid:
                    print("⚠️ Giro invalidato (Danno o Fuori Pista). Dati scartati.")
                    is_lap_valid = False

            # --- ACCUMULO DATI NEL BUFFER ---
            track_sensors = S.get('track', [0.0]*19)
            track_str = ",".join(map(str, track_sensors))
            
            csv_row = (f"{time.time()-t0:.3f},{a['steer']:.4f},{a['accel']:.4f},{a['brake']:.4f},{a['gear']},"
                       f"{S.get('speedX',0):.2f},{track_pos:.4f},{S.get('angle',0):.4f},"
                       f"{S.get('rpm',0)},{current_damage},{track_str}\n")
            
            json_step = {
                "sensors": {"speedX": S.get('speedX'), "trackPos": track_pos, "angle": S.get('angle'), "track": track_sensors},
                "actions": {"steer": a['steer'], "accel": a['accel'], "brake": a['brake'], "gear": a['gear']}
            }
            
            lap_buffer_csv.append(csv_row)
            lap_buffer_json.append(json_step)

            # --- CONTROLLO FINE GIRO ---
            # 'lastLapTime' aumenta quando passi il traguardo
            current_lap_time = S.get('lastLapTime', 0)
            
            if current_lap_time > 0 and current_lap_time != last_lap_time_prev:
                if is_lap_valid:
                    # SALVATAGGIO FISICO
                    save_lap(OUTPUT_DIR, lap_buffer_csv, lap_buffer_json, current_lap_time)
                    
                    print(f"✅ Giro completato in {current_lap_time:.2f}s — SALVATO!")
                else:
                    print(f"❌ Giro CONCLUSO MA SCARTATO (non valido).")
                
                # Reset per il nuovo giro
                lap_buffer_csv, lap_buffer_json = [], []
                is_lap_valid = True
                last_lap_time_prev = current_lap_time

            last_damage = current_damage
            time.sleep(0.005) # Piccola pausa per evitare di sovraccaricare la CPU

    except KeyboardInterrupt:
        print("\n🛑 Sessione interrotta dall'utente.")
        if is_lap_valid and len(lap_buffer_csv) > 100:
            with open("manual_log.csv", "a") as f:
                f.writelines(lap_buffer_csv)
            all_clean_data_json.extend(lap_buffer_json)
            with open("manual_log.json", "w") as f:
                json.dump(all_clean_data_json, f, indent=2)
            print(f"✅ {len(lap_buffer_csv)} step salvati da Ctrl+C.")
    finally:
        # Pulizia finale
        print(f"Dataset finale pronto: 'manual_log.csv' e 'manual_log.json'")
        sys.exit()
    
    


if __name__ == "__main__":
    main()