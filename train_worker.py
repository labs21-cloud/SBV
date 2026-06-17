# train_worker.py

import sys
import os
import pickle
import json
import numpy as np
import librosa
import soundfile as sf
from sklearn.svm import SVC
from supabase import create_client
from dotenv import load_dotenv
import core

# --- SISTEMA DE REPORTE DE PROGRESO ---
def report_progress(dni, step, message, percent):
    path = f"temp_uploads/progress_{dni}.json"
    try:
        with open(path, "w") as f:
            json.dump({
                "step": step,
                "msg": message,
                "percent": percent
            }, f)
    except:
        pass

def log(msg):
    try:
        print(f"   [Worker] {msg}", flush=True)
    except UnicodeEncodeError:
        safe_msg = msg.encode('ascii', 'ignore').decode('ascii')
        print(f"   [Worker] {safe_msg}", flush=True)

# --- FUNCIÓN PARA GUARDAR EL RESULTADO ---
def save_result_to_disk(dni, data):
    """Guarda el resultado final en un JSON para que main.py lo lea de forma segura."""
    path = f"temp_uploads/result_{dni}.json"
    try:
        with open(path, "w") as f:
            json.dump(data, f)
    except Exception as e:
        log(f"¡CRÍTICO! No se pudo guardar el archivo de resultado: {e}")

def main():
    # 1. Validar Argumentos
    if len(sys.argv) < 4:
        log("Error: Faltan argumentos.")
        sys.exit(1)
    
    dni = sys.argv[1]
    nombre = sys.argv[2]
    file_paths = sys.argv[3:]

    # PASO 1: INICIO
    report_progress(dni, "LOAD", "Inicializando entornos...", 5)
    log(f"Tarea iniciada: {nombre} ({dni}) - {len(file_paths)} audios")

    # 2. Conexión Supabase
    load_dotenv()
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        report_progress(dni, "ERROR", "Error de credenciales", 0)
        log("Error: Faltan credenciales .env")
        sys.exit(1)
    supabase = create_client(url, key)

    # 3. Cargar Impostores
    report_progress(dni, "LOAD", "Cargando dataset de impostores...", 10)
    try:
        with open("vectores_impostores_v2.pkl", "rb") as f:
            impostores = pickle.load(f)
    except:
        log("No hay impostores.pkl, usando lista vacía.")
        impostores = []

    # 4. Procesamiento de Audio
    embeddings_positivos = []
    total_files = len(file_paths)
    for idx, path in enumerate(file_paths):
        base_name = os.path.basename(path)
        pct = 15 + int((idx / total_files) * 60)
        report_progress(dni, "SLICE", f"Analizando audio {idx+1}/{total_files}: {base_name}...", pct)
        log(f"Procesando: {base_name}")
        try:
            audio, _ = librosa.load(path, sr=16000)
            chunks = core.smart_slicer(audio, 16000)
            for chunk in chunks:
                emb = core.get_embedding(chunk)
                if emb is not None:
                    embeddings_positivos.append(emb)
                # Data Augmentation x20
                for _ in range(20):
                    aug_chunk = core.augment_audio(chunk)
                    emb_aug = core.get_embedding(aug_chunk)
                    if emb_aug is not None:
                        embeddings_positivos.append(emb_aug)
        except Exception as e:
            log(f"Error leyendo {base_name}: {e}")

    if not embeddings_positivos:
        report_progress(dni, "ERROR", "No se detectó voz válida en los audios.", 0)
        log("Fallo: Sin vectores válidos.")
        sys.exit(1)
    
    log(f"Vectores extraídos: {len(embeddings_positivos)}")

    # 5. Entrenamiento SVM
    report_progress(dni, "TRAIN", f"Entrenando modelo IA ({len(embeddings_positivos)} muestras)...", 80)
    log("Entrenando SVM...")
    import random
    n_imps = min(200, len(impostores))
    negs = random.sample(impostores, n_imps) if impostores else []
    X = np.array(embeddings_positivos + negs)
    y = np.array([1]*len(embeddings_positivos) + [0]*len(negs))
    clf = SVC(probability=True, kernel='rbf', class_weight='balanced')
    clf.fit(X, y)
    score_test = clf.predict_proba([embeddings_positivos[0]])[0][1]

    # 6. Subida a Supabase
    report_progress(dni, "UPLOAD", "Subiendo modelo a la nube segura...", 90)
    log("Subiendo a Supabase...")
    model_bytes = pickle.dumps(clf)
    
    try:
        # A) Upsert Usuario
        data = {
            "dni": dni,
            "nombre": nombre,
            "modelo": model_bytes.hex(),
            "muestras": len(embeddings_positivos),
            "auto_check": float(score_test)
        }
        supabase.table("usuarios").upsert(data).execute()

                # B) Storage Audio Ref
        ref_file_path = file_paths[0]
        remote_path = f"{dni}/ref.wav"
        with open(ref_file_path, "rb") as f:
            supabase.storage.from_("audiosreferencia").upload(
                path=remote_path,
                file=f.read(),
                file_options={"upsert": "true", "content-type": "audio/wav"}
            )

        # C) URL Pública
        public_url = supabase.storage.from_("audiosreferencia").get_public_url(remote_path)
        supabase.table("usuarios").update({"refaudiourl": public_url}).eq("dni", dni).execute()


        # --- DONE ---
        report_progress(dni, "DONE", "¡Entrenamiento Completado!", 100)
        log(f"Finalizado con éxito. Score: {score_test:.4f}")
        
        result = {
            "status": "ok",
            "auto_check": score_test,
            "ref_audio": public_url,
            "msg": nombre 
        }
        
        # En lugar de print(), se guarda en disco
        save_result_to_disk(dni, result)

        sys.exit(0)

    except Exception as e:
        report_progress(dni, "ERROR", "Error de conexión con la nube.", 0)
        log(f"Error crítico subida: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()

