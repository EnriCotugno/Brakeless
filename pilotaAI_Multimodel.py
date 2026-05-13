# Intelligenza Artificiale (Behavioral Cloning) — K-NN Multi-Modello
#
# Cambiamenti rispetto alla versione precedente:
#   - 4 modelli separati: steer, accel, brake (Regressori) + gear (Classificatore)
#   - K diverso per ogni target: accel/brake usano K=5 per evitare lo smoothing bimodale
#   - KNeighborsClassifier per gear: vota la marcia giusta invece di arrotondarla
#   - Scaler condiviso (le feature di input sono le stesse per tutti i modelli)

import time
import glob
import os
import pandas as pd
import numpy as np
from sklearn.neighbors import KNeighborsRegressor, KNeighborsClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error, accuracy_score
import snakeoil as snakeoil3

# ── Configurazioni ─────────────────────────────────────────────────────────────
DATASET_FILE = "dataset_more_parameters.csv"
LAPS_FOLDER  = "Laps_more_parameters"

K_STEER = 7   # Sterzo: continuo, K medio va bene
K_ACCEL = 4   # Accel:  bimodale (0 o 1), K basso = meno smoothing
K_BRAKE = 5   # Freno:  stessa ragione di accel
K_GEAR  = 7   # Gear:   classificatore, K medio

MAX_STEPS = 200_000


# ── Merge giri ─────────────────────────────────────────────────────────────────
def merge_laps(laps_folder: str, output_path: str) -> None:
    pattern = os.path.join(laps_folder, "lap_*.csv")
    files   = sorted(glob.glob(pattern))

    if not files:
        print(f"[MERGE] Nessun file lap_*.csv trovato in '{laps_folder}'. "
              "Assicurati di aver registrato almeno un giro.")
        return

    print(f"[MERGE] Trovati {len(files)} giri da unire:")
    for f in files:
        print(f"        - {f}")

    merged = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    merged.to_csv(output_path, index=False)
    print(f"[MERGE] Dataset salvato in '{output_path}' ({len(merged)} campioni totali).\n")


# ── Classe principale ──────────────────────────────────────────────────────────
class PilotaKNN:

    FEATURES = ['speedX', 'trackPos', 'angle'] + [f'track_{i}' for i in range(19)]

    def __init__(self, dataset_path: str):
        # Uno scaler condiviso: le feature di input sono identiche per tutti i modelli
        self.scaler = StandardScaler()

        # --- Regressori (output continuo) ---
        self.model_steer = KNeighborsRegressor(n_neighbors=K_STEER, weights='distance')
        self.model_accel = KNeighborsRegressor(n_neighbors=K_ACCEL, weights='distance')
        self.model_brake = KNeighborsRegressor(n_neighbors=K_BRAKE, weights='distance')

        # --- Classificatore (output discreto: marcia 1–6) ---
        # Usa 'distance' anche qui: i vicini più simili pesano di più nel voto
        self.model_gear  = KNeighborsClassifier(n_neighbors=K_GEAR,  weights='distance')

        self._addestra_modello(dataset_path)

    # ── Training ───────────────────────────────────────────────────────────────
    def _addestra_modello(self, dataset_path: str) -> None:
        print("\n[1] Caricamento del dataset...")
        try:
            df = pd.read_csv(dataset_path, comment='#')
        except FileNotFoundError:
            print(f"ERRORE: File '{dataset_path}' non trovato. Registra prima i dati!")
            exit()

        print(f"    Trovati {len(df)} campioni di guida.")

        X = df[self.FEATURES].values
        y_steer = df['steer'].values
        y_accel = df['accel'].values
        y_brake = df['brake'].values
        y_gear  = df['gear'].astype(int).values   # interi per il classificatore

        # Split 80/20 — stesso random_state per tutti così i fold sono allineati
        split = dict(test_size=0.2, random_state=42)
        X_tr, X_te, ys_tr, ys_te = train_test_split(X, y_steer, **split)
        _,    _,    ya_tr, ya_te = train_test_split(X, y_accel, **split)
        _,    _,    yb_tr, yb_te = train_test_split(X, y_brake, **split)
        _,    _,    yg_tr, yg_te = train_test_split(X, y_gear,  **split)

        print("[2] Scaling features (StandardScaler)...")
        X_tr_s = self.scaler.fit_transform(X_tr)
        X_te_s = self.scaler.transform(X_te)

        print("[3] Addestramento dei 4 modelli separati...")
        self.model_steer.fit(X_tr_s, ys_tr)
        self.model_accel.fit(X_tr_s, ya_tr)
        self.model_brake.fit(X_tr_s, yb_tr)
        self.model_gear.fit( X_tr_s, yg_tr)

        print("[4] Valutazione...")
        mse_steer = mean_squared_error(ys_te, self.model_steer.predict(X_te_s))
        mse_accel = mean_squared_error(ya_te, self.model_accel.predict(X_te_s))
        mse_brake = mean_squared_error(yb_te, self.model_brake.predict(X_te_s))
        acc_gear  = accuracy_score(yg_te,     self.model_gear.predict( X_te_s))

        print(f"    Sterzo  MSE : {mse_steer:.4f}  (più vicino a 0 è meglio)")
        print(f"    Accel   MSE : {mse_accel:.4f}  (più vicino a 0 è meglio)")
        print(f"    Freno   MSE : {mse_brake:.4f}  (più vicino a 0 è meglio)")
        print(f"    Marcia  ACC : {acc_gear*100:.1f}%  (più vicino a 100% è meglio)")
        print("\n>>> MODELLI PRONTI! IN ATTESA DI TORCS... <<<\n")

    # ── Inferenza real-time ────────────────────────────────────────────────────
    def predici_azioni(self, sensors: dict) -> dict:
        """Interroga i 4 modelli separati e restituisce le azioni per TORCS."""
        state = [
            sensors.get('speedX',   0.0),
            sensors.get('trackPos', 0.0),
            sensors.get('angle',    0.0),
        ] + list(sensors.get('track', [200.0] * 19))

        state_s = self.scaler.transform([state])

        steer = float(self.model_steer.predict(state_s)[0])
        accel = float(self.model_accel.predict(state_s)[0])
        brake = float(self.model_brake.predict(state_s)[0])
        gear  = int(  self.model_gear.predict( state_s)[0])   # voto diretto, no round()

        return {
            'steer': max(-1.0, min(1.0, steer)),
            'accel': max( 0.0, min(1.0, accel)),
            'brake': max( 0.0, min(1.0, brake)),
            'gear' : max(1,    min(6,   gear)),
        }


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    merge_laps(LAPS_FOLDER, DATASET_FILE)

    ai_driver = PilotaKNN(DATASET_FILE)
    client    = snakeoil3.Client(p=3001, vision=False)

    try:
        while True:
            client.get_servers_input()
            print("=== Gara Iniziata (Guida Autonoma KNN Multi-Modello) ===")

            for _ in range(MAX_STEPS):
                sensors = client.S.d

                if abs(sensors.get('trackPos', 0.0)) > 1.3:
                    print("L'AI è uscita di pista! Riavvio sessione...")
                    client.R.d['meta'] = 1
                    client.respond_to_server()
                    break

                actions = ai_driver.predici_azioni(sensors)

                client.R.d['steer']  = actions['steer']
                client.R.d['accel']  = actions['accel']
                client.R.d['brake']  = actions['brake']
                client.R.d['gear']   = actions['gear']
                client.R.d['clutch'] = 0.0
                client.R.d['meta']   = 0

                client.respond_to_server()
                client.get_servers_input()

            time.sleep(1.0)

    except KeyboardInterrupt:
        print("\nChiusura pilota automatico.")
    except Exception as e:
        print(f"\nDisconnesso da TORCS: {e}")


if __name__ == "__main__":
    main()