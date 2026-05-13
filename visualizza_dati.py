import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
import os

DATASET_FILE = "dataset_more_parameters.csv"

def visualizza_features(dataset_path):
    if not os.path.exists(dataset_path):
        print(f"Errore: Il file '{dataset_path}' non è stato trovato.")
        return

    df = pd.read_csv(dataset_path, comment='#')
    print(f"Dataset caricato: {len(df)} campioni.")

    sns.set_theme(style="whitegrid")

    # ==========================================
    # GRAFICO 1: Dinamica dell'auto (Dati Scalari)
    # ==========================================
    fig, axs = plt.subplots(1, 3, figsize=(16, 5))
    fig.suptitle("Dinamica dell'Auto (Features Scalari)", fontsize=16, fontweight='bold')

    # Velocità longitudinale
    sns.histplot(df['speedX'], bins=40, ax=axs[0], color='cyan', kde=True)
    axs[0].set_title('Velocità (speedX)\n(Se troppo stretta, l\'IA non sa guidare piano)')

    # Posizione rispetto al centro pista
    sns.histplot(df['trackPos'], bins=40, ax=axs[1], color='purple', kde=True)
    axs[1].set_title('Posizione in Pista (trackPos)\n0 = Centro | -1 = Bordo Destro | 1 = Bordo Sinistro')
    axs[1].axvline(0, color='black', linestyle='--')

    # Angolo rispetto all'asse della pista
    sns.histplot(df['angle'], bins=40, ax=axs[2], color='orange', kde=True)
    axs[2].set_title('Angolo dell\'Auto (angle)\n0 = Perfettamente dritta')
    axs[2].axvline(0, color='black', linestyle='--')

    plt.tight_layout()
    plt.show()

    # ==========================================
    # GRAFICO 2: Il "Radar" dell'auto (I 19 sensori track)
    # ==========================================
    track_cols = [f'track_{i}' for i in range(19)]
    
    # Calcoliamo la distanza media e la deviazione standard misurata da ciascun sensore
    mean_distances = df[track_cols].mean()
    std_distances = df[track_cols].std()
    angoli_sensori = np.linspace(-90, 90, 19) # In TORCS i 19 sensori coprono 180 gradi (da -90 a +90)

    fig2, ax2 = plt.subplots(figsize=(10, 6))
    fig2.suptitle("Profilo Visivo Medio (Cosa vede l'IA in media)", fontsize=16, fontweight='bold')
    
    ax2.plot(angoli_sensori, mean_distances, marker='o', color='blue', label='Distanza Media (m)')
    ax2.fill_between(angoli_sensori, 
                     mean_distances - std_distances, 
                     mean_distances + std_distances, 
                     color='blue', alpha=0.2, label='Varianza (Deviazione Standard)')
    
    ax2.set_xlabel('Angolo del sensore (Gradi)')
    ax2.set_ylabel('Distanza dal bordo pista (Metri)')
    ax2.set_title('I sensori centrali (0°) vedono più lontano nei rettilinei, quelli laterali misurano la larghezza')
    ax2.axvline(0, color='red', linestyle='--', alpha=0.5, label='Centro dell\'auto')
    ax2.legend()
    
    plt.tight_layout()
    plt.show()

    # ==========================================
    # GRAFICO 3: Matrice di Correlazione (Features vs Azioni)
    # ==========================================
    # Selezioniamo le features chiave e le azioni per vedere come sono matematicamente legate
    cols_to_correlate = ['speedX', 'trackPos', 'angle', 'steer', 'accel', 'brake']
    corr_matrix = df[cols_to_correlate].corr()

    fig3, ax3 = plt.subplots(figsize=(8, 6))
    fig3.suptitle("Matrice di Correlazione: Features vs Azioni", fontsize=16, fontweight='bold')
    
    # Una heatmap che va da -1 (correlazione inversa perfetta) a +1 (correlazione diretta perfetta)
    sns.heatmap(corr_matrix, annot=True, cmap='coolwarm', vmin=-1, vmax=1, fmt=".2f", linewidths=.5, ax=ax3)
    
    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    visualizza_features(DATASET_FILE)