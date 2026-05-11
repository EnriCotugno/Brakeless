# Intelligenza Artificiale (Behavioral Cloning) tramite K-NN Regressor
import time
import glob
import os
import pandas as pd
import numpy as np
from sklearn.neighbors import KNeighborsRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error
import snakeoil as snakeoil3

# Configurazioni
DATASET_FILE = "dataset.csv"
LAPS_FOLDER  = "Laps"          # Cartella contenente i file lap_*.csv
K_NEIGHBORS  = 5               # Numero di "ricordi" vicini da consultare
MAX_STEPS    = 200_000


def merge_laps(laps_folder: str, output_path: str) -> None:
    """
    Unisce tutti i file lap_*.csv presenti in `laps_folder` in un unico
    dataset CSV, eliminando le intestazioni ridondanti dei singoli giri.
    Il file di output viene (ri)creato ogni volta che viene chiamata questa funzione.
    """
    pattern = os.path.join(laps_folder, "lap_*.csv")
    files = sorted(glob.glob(pattern))

    if not files:
        print(f"[MERGE] Nessun file lap_*.csv trovato in '{laps_folder}'. "
              "Assicurati di aver registrato almeno un giro.")
        return

    print(f"[MERGE] Trovati {len(files)} giri da unire:")
    for f in files:
        print(f"        - {f}")

    dfs = [pd.read_csv(f) for f in files]
    merged = pd.concat(dfs, ignore_index=True)
    merged.to_csv(output_path, index=False)
    print(f"[MERGE] Dataset salvato in '{output_path}' ({len(merged)} campioni totali).\n")


class PilotaKNN:
    def __init__(self, dataset_path):
        self.model = KNeighborsRegressor(n_neighbors=K_NEIGHBORS, weights='distance')
        self.features_names = ['speedX', 'trackPos', 'angle'] + [f'track_{i}' for i in range(19)]
        self.targets_names = ['steer', 'accel', 'brake', 'gear']
        
        self._addestra_modello(dataset_path)

    def _addestra_modello(self, dataset_path):
        print("\n[1] Caricamento del dataset...")
        # Il parametro comment='#' ignora in automatico i divisori dei giri!
        try:
            df = pd.read_csv(dataset_path, comment='#')
        except FileNotFoundError:
            print(f"ERRORE: File {dataset_path} non trovato. Registra prima i dati!")
            exit()

        print(f"Trovati {len(df)} campioni di guida.")

        # Estraiamo X (Sensori) e y (Azioni)
        X = df[self.features_names].values
        y = df[self.targets_names].values

        # Split del dataset (80% train, 20% test)
        # Nota: Non usiamo 'stratify' perché stiamo facendo regressione, non classificazione
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42
        )

        print("[2] Addestramento del modello K-NN in corso...")
        self.model.fit(X_train, y_train)

        print("[3] Valutazione del modello (Mean Squared Error)...")
        y_pred = self.model.predict(X_test)
        
        # Valutiamo l'errore per ogni asse per capire dove l'AI è meno precisa
        mse_steer = mean_squared_error(y_test[:, 0], y_pred[:, 0])
        mse_accel = mean_squared_error(y_test[:, 1], y_pred[:, 1])
        print(f" - Errore Sterzo: {mse_steer:.4f} (Più vicino a 0 è meglio)")
        print(f" - Errore Accel : {mse_accel:.4f} (Più vicino a 0 è meglio)")
        print("\n>>> MODELLO PRONTO! IN ATTESA DI TORCS... <<<\n")

    def predici_azioni(self, sensors: dict) -> dict:
        """Prende i sensori in tempo reale da TORCS e restituisce le azioni predette dal KNN"""
        # 1. Prepariamo l'array dei sensori nello stesso esatto ordine di come li abbiamo addestrati
        current_state = [
            sensors.get('speedX', 0.0),
            sensors.get('trackPos', 0.0),
            sensors.get('angle', 0.0)
        ] + list(sensors.get('track', [200.0] * 19))

        # 2. Il modello richiede un array 2D, quindi lo passiamo dentro una lista []
        pred = self.model.predict([current_state])[0]

        # 3. Mappiamo i risultati
        # Sterzo, Accel e Freno sono continui. La marcia (pred[3]) la arrotondiamo all'intero più vicino.
        return {
            'steer': max(-1.0, min(1.0, pred[0])),
            'accel': max(0.0, min(1.0, pred[1])),
            'brake': max(0.0, min(1.0, pred[2])),
            'gear' : int(round(pred[3]))
        }


# ── Esecuzione Guida Autonoma ──────────────────────────────────────────────────
def main():
    # 0. Unisci i giri registrati in un unico dataset (sovrascrive dataset.csv)
    merge_laps(LAPS_FOLDER, DATASET_FILE)

    # 1. Inizializza e addestra l'AI
    ai_driver = PilotaKNN(DATASET_FILE)
    
    # 2. Connettiti a TORCS
    client = snakeoil3.Client(p=3001, vision=False)
    
    try:
        while True:
            client.get_servers_input()
            t0 = time.time()
            print("=== Gara Iniziata (Guida Autonoma KNN) ===")
            
            for step in range(MAX_STEPS):
                sensors = client.S.d
                
                # Auto-Reset se l'AI sbaglia ed esce di pista
                if abs(sensors.get('trackPos', 0.0)) > 1.2:
                    print("L'AI è uscita di pista! Riavvio sessione...")
                    client.R.d['meta'] = 1
                    client.respond_to_server()
                    break

                # Interroga il K-NN
                actions = ai_driver.predici_azioni(sensors)

                # Invia i comandi dell'AI a TORCS
                client.R.d['steer']  = actions['steer']
                client.R.d['accel']  = actions['accel']
                client.R.d['brake']  = actions['brake']
                client.R.d['gear']   = actions['gear']
                client.R.d['clutch'] = 0.0
                client.R.d['meta']   = 0

                client.respond_to_server()
                client.get_servers_input()
                
            time.sleep(1.0) # Pausa tra i riavvii
                
    except KeyboardInterrupt:
        print("\nChiusura pilota automatico.")
    except Exception as e:
        print(f"\nDisconnesso da TORCS: {e}")

if __name__ == "__main__":
    main()