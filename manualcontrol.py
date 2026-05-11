import math
import os
import sys
import pygame
from pynput.keyboard import Key, Listener
import snakeoil as snakeoil3
import time
from datetime import datetime
import json


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



    # cambio automatico basato sulla velocità
    def auto_shift_gear(self, speed):
        """
        Cambia automaticamente la marcia in base alla velocità del veicolo.
        :param speed: Velocità corrente del veicolo (speedX dal sensore).
        """
        # Definire le curve di cambio della marcia
        min_speeds = [0, 20, 40, 60, 80, 100, 120]  # Velocità massime per ogni marcia
        current_gear = self.state['gear']
        target_gear = 1

        for i in range(len(min_speeds) - 1):
            if min_speeds[i] <= speed < min_speeds[i + 1]:
                target_gear = i + 1
                break
        else:
            # Se la velocità è superiore all'ultima marcia definita, usare l'ultima marcia
            target_gear = len(min_speeds) - 1

        # Cambiare gradualmente la marcia per evitare salti bruschi
        if abs(target_gear - current_gear) > 0:
            self.state['gear'] += math.copysign(1, target_gear - current_gear)
    
        # Assicurarsi che la marcia rimanga all'interno dei limiti validi (-1 a 6)
        self.state['gear'] = max(-1, min(6, self.state['gear']))



    def update(self, sensors):
        pygame.event.clear()
        pygame.event.pump() # Aggiorna lo stato del controller

        # --- STERZO (Levetta Sinistra - Asse 0) ---
        # L'asse X va da -1.0 (sinistra) a 1.0 (destra)
        steer_input = self.joystick.get_axis(0)
        
        # Deadzone per evitare che la macchina sterzi da sola se la levetta è usurata
        if abs(steer_input) < 0.1:
            steer_input = 0.0
        self.state['steer'] = -steer_input # Inverti se necessario in base al simulatore

        # --- ACCELERATORE E FRENO (Grilletti / Triggers) ---
        # Su Xbox: 
        # Grilletto Sinistro (LT) è spesso Asse 4 o 2
        # Grilletto Destro (RT) è spesso Asse 5 o 5
        # NOTA: Pygame legge i trigger da -1 (riposo) a 1 (premuto)
        
        rt_axis = self.joystick.get_axis(5) # Acceleratore
        lt_axis = self.joystick.get_axis(4) # Freno

        # Convertiamo il range da [-1, 1] a [0, 1]
        self.state['accel'] = (rt_axis + 1.0) / 2.0
        self.state['brake'] = (lt_axis + 1.0) / 2.0

        # Clamp di sicurezza
        self.state['steer'] = max(-1.0, min(1.0, self.state['steer']))
        self.state['accel'] = max(0.0, min(1.0, self.state['accel']))
        self.state['brake'] = max(0.0, min(1.0, self.state['brake']))


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
            controller.auto_shift_gear(S.get('speedX', 0))
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
            if current_damage > last_damage or abs(track_pos) > 1.7:
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