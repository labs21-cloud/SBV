import os
import tarfile
import pickle
import glob
import shutil
import urllib.request
import numpy as np
import librosa
import soundfile as sf
import core 

# CONFIGURACIÓN
URL_DATASET = "http://download.tensorflow.org/data/speech_commands_v0.01.tar.gz"
CARPETA_TEMP = "temp_dataset_impostores"
ARCHIVO_SALIDA = "vectores_impostores_universales.pkl"
CANTIDAD_VECTORES = 500  # 500 voces distintas es ideal

def descargar_y_extraer():
    if os.path.exists(CARPETA_TEMP):
        print(f"📂 La carpeta {CARPETA_TEMP} ya existe. Usando contenido actual.")
        return

    print(f"⬇️  Descargando dataset público (Speech Commands)... Por favor espera.")
    os.makedirs(CARPETA_TEMP, exist_ok=True)
    ruta_tar = os.path.join(CARPETA_TEMP, "speech_commands.tar.gz")
    

    urllib.request.urlretrieve(URL_DATASET, ruta_tar)
    
    print("📦 Extrayendo archivos...")
    with tarfile.open(ruta_tar, "r:gz") as tar:
        tar.extractall(path=CARPETA_TEMP)
    
    os.remove(ruta_tar) # Limpiar el .tar.gz
    print("✅ Descarga y extracción completada.")

def generar_vectores():
    print("="*60)
    print("🏭 INICIANDO GENERACIÓN DE IMPOSTORES")
    print(f"🎯 Objetivo: {CANTIDAD_VECTORES} vectores de voces distintas")
    print("="*60)

    # Buscar archivos WAV en subcarpetas (cada subcarpeta es una palabra distinta/gente distinta)
    # Speech Commands tiene estructura: carpeta/palabra/usuario_hash.wav
    audios = glob.glob(os.path.join(CARPETA_TEMP, "*", "*.wav"))
    
    if not audios:
        print("❌ Error: No se encontraron audios extraídos.")
        return

    print(f"🔍 Se encontraron {len(audios)} archivos de audio disponibles.")
    
    # Mezclar para tener variedad aleatoria
    import random
    random.shuffle(audios)
    
    vectores = []
    procesados = 0
    
    # Usamos TitaNet (core.py) para extraer características
    print("🧠 Cargando motor biométrico (TitaNet)...")
    
    for ruta in audios:
        try:
            # 1. Cargar audio
            audio, sr = librosa.load(ruta, sr=16000)
            
            # 2. Ignorar silencios o audios rotos (< 0.5s)
            if len(audio) < 8000: continue
            
            # 3. Extraer vector con TU core
            # core.get_embedding espera un array 1D y sr
            
            emb = core.get_embedding(audio)
            
            if emb is not None:
                vectores.append(emb)
                procesados += 1
                if procesados % 10 == 0:
                    print(f"   ⚡ Procesados: {procesados}/{CANTIDAD_VECTORES}")
            
            if len(vectores) >= CANTIDAD_VECTORES:
                break
                
        except Exception as e:
            # Algunos archivos pueden fallar, los ignoramos
            continue

    # Guardar
    if len(vectores) > 0:
        with open(ARCHIVO_SALIDA, "wb") as f:
            pickle.dump(vectores, f)
        print("="*60)
        print(f"🎉 ÉXITO TOTAL. Archivo generado: {ARCHIVO_SALIDA}")
        print(f"📊 Contiene {len(vectores)} huellas de desconocidos.")
        print("✅ Tu sistema ahora usará este archivo legítimo.")
    else:
        print("❌ Error crítico: No se pudieron generar vectores.")

    # Limpieza (Opcional)
    print("🧹 Limpiando archivos temporales...")
    try:
        shutil.rmtree(CARPETA_TEMP)
    except:
        pass
    print("✨ Todo listo.")

if __name__ == "__main__":
    descargar_y_extraer()
    generar_vectores()
