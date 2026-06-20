# ==============================================================================
# ARCHIVO: main.py
# ROL: Orquestador Principal y API Backend (FastAPI) — V8.1
# CORRECCIONES: /all_sessions alias, /historysync ElevenLabs, /userdetails
#               normalizado con aliases, /userslist con muestras+createdat.
# ==============================================================================

import requests
import os
import threading
import queue
import shutil
import uuid
import json
import logging
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)  # Solo warnings
import traceback
import subprocess
import sys
import time
import collections
import asyncio
from typing import List, Optional, Tuple
from datetime import datetime, timezone

import numpy as np
import sounddevice as sd
import soundfile as sf
import librosa
from sklearn.svm import SVC
import torch

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Body, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from dotenv import load_dotenv
from supabase import create_client, Client

import core

# ==============================================================================
# 0. CONFIGURACIÓN Y LOGS
# ==============================================================================

logger = logging.getLogger("biometria")
logger.setLevel(logging.INFO)

if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s - [%(levelname)s] - %(message)s", datefmt="%H:%M:%S")
    )
    logger.addHandler(handler)

# class EndpointFilter(logging.Filter):
#     def filter(self, record: logging.LogRecord) -> bool:
#         msg = record.getMessage()
#         return (
#             msg.find("GET /verifylog") == -1
#             and msg.find("GET /progress") == -1
#             and msg.find("GET /health") == -1
#             and msg.find("GET /crm") == -1
#         )
#
# logging.getLogger("uvicorn.access").addFilter(EndpointFilter())
logging.getLogger("httpx").setLevel(logging.WARNING)

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_AGENT_ID = os.getenv("ELEVENLABS_AGENT_ID", "")

if not SUPABASE_URL or not SUPABASE_KEY:
    logger.error("❌ ERROR FATAL: Faltan credenciales en .env")
    raise SystemExit(1)

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    logger.info("✅ Cliente Supabase inicializado.")
except Exception as e:
    logger.error(f"❌ Error inicializando Supabase: {e}")
    raise SystemExit(1)

os.makedirs("temp_uploads", exist_ok=True)
os.makedirs("static", exist_ok=True)
os.makedirs("verificaciones_audio", exist_ok=True)

# ==============================================================================
# VARIABLES GLOBALES Y ESTADO DE SESIONES
# ==============================================================================

CACHEMODELOS = {}
VERIFYLOG = []
VERIFYLOGLOCK = threading.Lock()
LASTPIPELINE = None

NATIVESR = 16000

SYSTEMCONFIG = {
    "umbralidentidad": 0.50,
    "activebiometrics": True,
    "activedeepfake": False,
    "deepfakemodel": "500m",
    "deepfakebypass": True,
    "df500m_threshold": 0.50,
    "df500m_minsecs": 1.20,
    "df500m_minrms": 0.006,
    "df500m_maxabs": 0.999,
    "df1b_threshold": 0.50,
    "df1b_minsecs": 1.20,
    "df1b_minrms": 0.006,
    "df1b_maxabs": 0.999,
    "spybuffersecs": 5,
}

CONFIGROWID: Optional[int] = None
SESSIONSTATE = {}
taskqueue = queue.Queue()

app = FastAPI(title="Biometría Cloud-Native")
app.mount("/static", StaticFiles(directory="static"), name="static")

# ==============================================================================
# CARGA DE SILERO VAD (ON-DEMAND)
# ==============================================================================

logger.info("🎙️ [SISTEMA] Cargando motor Silero VAD....")
try:
    VADMODEL, _ = torch.hub.load(
        repo_or_dir="snakers4/silero-vad",
        model="silero_vad",
        force_reload=False,
    )
    VADMODEL.eval()
    logger.info("✅ Silero VAD cargado correctamente.")
except Exception as e:
    logger.error(f"❌ Error cargando Silero VAD: {e}")
    VADMODEL = None

# ==============================================================================
# 0.1. HELPERS Y WRAPPERS DE CORE
# ==============================================================================

def getcoretitanetmodel():
    for name in ("titanetmodel", "titanet_model", "titanetModel"):
        m = getattr(core, name, None)
        if m is not None:
            return m
    return None


def getcorespoofclassifier():
    for name in ("spoofclassifier", "spoof_classifier", "_spoof_classifier"):
        sc = getattr(core, name, None)
        if sc is not None:
            return sc
    return None


def deepfakeengineready() -> bool:
    try:
        return getcorespoofclassifier() is not None
    except Exception:
        return False


def coredeserializemodel(blob):
    for fn in ("deserialize_model", "deserializemodel", "deserializeModel", "deserialize"):
        f = getattr(core, fn, None)
        if callable(f):
            return f(blob)
    return None


def coreprocessverification(dni: str, wavpath: str, cache: dict, umbral: float):
    for fn in ("process_verification", "processverification", "processVerification"):
        f = getattr(core, fn, None)
        if callable(f):
            return f(dni, wavpath, cache, umbral=umbral)
    raise RuntimeError("core: no se encontró función de verificación.")


def coredetectdeepfake(audionp: np.ndarray, sr: int, threshold: Optional[float] = None) -> Tuple[bool, float]:
    for fn in ("detect_deepfake", "detectdeepfake", "detectDeepfake"):
        f = getattr(core, fn, None)
        if callable(f):
            try:
                if threshold is None:
                    return f(audionp, sr)
                return f(audionp, sr, threshold)
            except TypeError:
                return f(audionp, sr)
            except Exception:
                return False, 0.0
    return False, 0.0


def coresetdeepfakemodel(variant: str) -> bool:
    v = (variant or "").strip().lower()
    if not v:
        return False
    for fn in ("setdeepfakemodel", "setdeepfakemodelvariant", "set_deepfake_model_variant", "setdeepfake_model_variant"):
        f = getattr(core, fn, None)
        if callable(f):
            try:
                ok = bool(f(v))
                if not ok:
                    logger.warning(f"⚠️ No se pudo activar deepfakemodel='{v}' en core.")
                return ok
            except Exception as e:
                logger.warning(f"⚠️ Error cambiando deepfakemodel en core: {e}")
                return False
    return False


def nowhms() -> str:
    return datetime.now().strftime("%H:%M:%S")


def safefloat(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def rms_np(x: np.ndarray) -> float:
    try:
        if x is None or len(x) == 0:
            return 0.0
        x = x.astype(np.float32, copy=False)
        return float(np.sqrt(np.mean(x * x)))
    except Exception:
        return 0.0


def clamp_audio_maxabs(audio: np.ndarray, maxabs: float) -> np.ndarray:
    try:
        m = float(maxabs)
        if m <= 0:
            return audio
        return np.clip(audio, -m, m)
    except Exception:
        return audio


def get_df_params_for_model(model_key: str) -> dict:
    mk = (model_key or "500m").strip().lower()
    if mk == "1b":
        return {
            "threshold": safefloat(SYSTEMCONFIG.get("df1b_threshold", 0.5), 0.5),
            "minsecs":   safefloat(SYSTEMCONFIG.get("df1b_minsecs", 1.2), 1.2),
            "minrms":    safefloat(SYSTEMCONFIG.get("df1b_minrms", 0.006), 0.006),
            "maxabs":    safefloat(SYSTEMCONFIG.get("df1b_maxabs", 0.999), 0.999),
        }
    return {
        "threshold": safefloat(SYSTEMCONFIG.get("df500m_threshold", 0.5), 0.5),
        "minsecs":   safefloat(SYSTEMCONFIG.get("df500m_minsecs", 1.2), 1.2),
        "minrms":    safefloat(SYSTEMCONFIG.get("df500m_minrms", 0.006), 0.006),
        "maxabs":    safefloat(SYSTEMCONFIG.get("df500m_maxabs", 0.999), 0.999),
    }

# ==============================================================================
# 1. GESTIÓN DE SESIONES Y CONFIG EN BD
# ==============================================================================

def getsession(dni: str) -> dict:
    now = time.time()
    keys_to_delete = [k for k, v in SESSIONSTATE.items() if (now - v["timestamp"]) > 300]
    for k in keys_to_delete:
        del SESSIONSTATE[k]
    if dni not in SESSIONSTATE:
        SESSIONSTATE[dni] = {"timestamp": now, "intentos": 0, "deepfakepassed": False}
    else:
        SESSIONSTATE[dni]["timestamp"] = now
    return SESSIONSTATE[dni]


LEGACYTOSNAKE = {
    "umbralidentidad": "umbralidentidad",
    "activebiometrics": "activebiometrics",
    "activedeepfake": "activedeepfake",
    "deepfakemodel": "deepfakemodel",
    "deepfakebypass": "deepfakebypass",
    "deepfake_bypass": "deepfakebypass",
    "df500m_threshold": "df500m_threshold",
    "df500m_minsecs": "df500m_minsecs",
    "df500m_minrms": "df500m_minrms",
    "df500m_maxabs": "df500m_maxabs",
    "df1b_threshold": "df1b_threshold",
    "df1b_minsecs": "df1b_minsecs",
    "df1b_minrms": "df1b_minrms",
    "df1b_maxabs": "df1b_maxabs",
    "spybuffersecs": "spybuffersecs",
}

SNAKETOLEGACY = {v: k for k, v in LEGACYTOSNAKE.items()}


def fetchconfigrowlatest() -> Optional[dict]:
    try:
        try:
            res = (
                supabase.table("configuracion")
                .select(
                    "id, umbralidentidad, activebiometrics, activedeepfake, deepfakemodel, deepfake_bypass, "
                    "df500m_threshold, df500m_minsecs, df500m_minrms, df500m_maxabs, "
                    "df1b_threshold, df1b_minsecs, df1b_minrms, df1b_maxabs, spybuffersecs"
                )
                .order("id", desc=True)
                .limit(1)
                .execute()
            )
        except Exception:
            try:
                res = (
                    supabase.table("configuracion")
                    .select("id, umbralidentidad, activebiometrics, activedeepfake, deepfakemodel, spybuffersecs")
                    .order("id", desc=True)
                    .limit(1)
                    .execute()
                )
            except Exception:
                res = (
                    supabase.table("configuracion")
                    .select("id, umbralidentidad, activebiometrics, activedeepfake, spybuffersecs")
                    .order("id", desc=True)
                    .limit(1)
                    .execute()
                )
        if res.data:
            return res.data[0]
        return None
    except Exception as e:
        logger.warning(f"⚠️ No se pudo leer tabla configuracion: {e}")
        return None


def persistconfigtodb() -> None:
    global CONFIGROWID
    payloaddb = {
        "umbralidentidad":  float(SYSTEMCONFIG.get("umbralidentidad", 0.5)),
        "activebiometrics": bool(SYSTEMCONFIG.get("activebiometrics", True)),
        "activedeepfake":   bool(SYSTEMCONFIG.get("activedeepfake", False)),
        "deepfakemodel":    str(SYSTEMCONFIG.get("deepfakemodel", "500m") or "500m"),
        "deepfake_bypass":  bool(SYSTEMCONFIG.get("deepfakebypass", True)),
        "df500m_threshold": float(safefloat(SYSTEMCONFIG.get("df500m_threshold", 0.5), 0.5)),
        "df500m_minsecs":   float(safefloat(SYSTEMCONFIG.get("df500m_minsecs", 1.2), 1.2)),
        "df500m_minrms":    float(safefloat(SYSTEMCONFIG.get("df500m_minrms", 0.006), 0.006)),
        "df500m_maxabs":    float(safefloat(SYSTEMCONFIG.get("df500m_maxabs", 0.999), 0.999)),
        "df1b_threshold":   float(safefloat(SYSTEMCONFIG.get("df1b_threshold", 0.5), 0.5)),
        "df1b_minsecs":     float(safefloat(SYSTEMCONFIG.get("df1b_minsecs", 1.2), 1.2)),
        "df1b_minrms":      float(safefloat(SYSTEMCONFIG.get("df1b_minrms", 0.006), 0.006)),
        "df1b_maxabs":      float(safefloat(SYSTEMCONFIG.get("df1b_maxabs", 0.999), 0.999)),
        "spybuffersecs":    int(SYSTEMCONFIG.get("spybuffersecs", 5)),
    }
    payloaddbfallback = {
        "umbralidentidad":  payloaddb["umbralidentidad"],
        "activebiometrics": payloaddb["activebiometrics"],
        "activedeepfake":   payloaddb["activedeepfake"],
        "deepfakemodel":    payloaddb["deepfakemodel"],
        "spybuffersecs":    payloaddb["spybuffersecs"],
    }
    try:
        if CONFIGROWID is None:
            row = fetchconfigrowlatest()
            if row and row.get("id") is not None:
                CONFIGROWID = int(row["id"])
        if CONFIGROWID is not None:
            try:
                supabase.table("configuracion").update(payloaddb).eq("id", CONFIGROWID).execute()
            except Exception as e:
                logger.error(f"⚠️ Error actualizando config completa en BD (usando fallback): {e}")
                supabase.table("configuracion").update(payloaddbfallback).eq("id", CONFIGROWID).execute()
            return
        try:
            ins = supabase.table("configuracion").insert(payloaddb).execute()
        except Exception as e:
            logger.error(f"⚠️ Error insertando config completa en BD (usando fallback): {e}")
            ins = supabase.table("configuracion").insert(payloaddbfallback).execute()
        if getattr(ins, "data", None) and ins.data and ins.data[0].get("id") is not None:
            CONFIGROWID = int(ins.data[0]["id"])
    except Exception as e:
        logger.error(f"❌ Error persistiendo configuracion en Supabase: {e}")


def loadconfigsync():
    global SYSTEMCONFIG, CONFIGROWID
    try:
        row = fetchconfigrowlatest()
        if row:
            if row.get("id") is not None:
                CONFIGROWID = int(row["id"])
            SYSTEMCONFIG.update({
                "umbralidentidad":  float(row.get("umbralidentidad",  SYSTEMCONFIG.get("umbralidentidad", 0.5))),
                "activebiometrics": bool(row.get("activebiometrics",  SYSTEMCONFIG.get("activebiometrics", True))),
                "activedeepfake":   bool(row.get("activedeepfake",    SYSTEMCONFIG.get("activedeepfake", False))),
                "deepfakemodel":    str(row.get("deepfakemodel",       SYSTEMCONFIG.get("deepfakemodel", "500m")) or "500m"),
                "deepfakebypass":   bool(row.get("deepfake_bypass",    SYSTEMCONFIG.get("deepfakebypass", True))),
                "df500m_threshold": safefloat(row.get("df500m_threshold", SYSTEMCONFIG.get("df500m_threshold", 0.5)), 0.5),
                "df500m_minsecs":   safefloat(row.get("df500m_minsecs",   SYSTEMCONFIG.get("df500m_minsecs", 1.2)), 1.2),
                "df500m_minrms":    safefloat(row.get("df500m_minrms",    SYSTEMCONFIG.get("df500m_minrms", 0.006)), 0.006),
                "df500m_maxabs":    safefloat(row.get("df500m_maxabs",    SYSTEMCONFIG.get("df500m_maxabs", 0.999)), 0.999),
                "df1b_threshold":   safefloat(row.get("df1b_threshold",   SYSTEMCONFIG.get("df1b_threshold", 0.5)), 0.5),
                "df1b_minsecs":     safefloat(row.get("df1b_minsecs",     SYSTEMCONFIG.get("df1b_minsecs", 1.2)), 1.2),
                "df1b_minrms":      safefloat(row.get("df1b_minrms",      SYSTEMCONFIG.get("df1b_minrms", 0.006)), 0.006),
                "df1b_maxabs":      safefloat(row.get("df1b_maxabs",      SYSTEMCONFIG.get("df1b_maxabs", 0.999)), 0.999),
                "spybuffersecs":    int(row.get("spybuffersecs",           SYSTEMCONFIG.get("spybuffersecs", 5))),
            })
            if SYSTEMCONFIG.get("activedeepfake", False):
                coresetdeepfakemodel(SYSTEMCONFIG.get("deepfakemodel"))
            logger.info(f"⚙️ Configuración cargada: {SYSTEMCONFIG}")
            persistconfigtodb()
            return

        persistconfigtodb()
    except Exception as e:
        logger.warning(f"⚠️ No se pudo cargar config: {e}")


def refrescarcachemodelossync():
    global CACHEMODELOS
    try:
        res = supabase.table("usuarios").select("*").execute()
        if not res.data:
            CACHEMODELOS = {}
            return
        nuevocache = {}
        for u in res.data:
            if not u.get("modelo"):
                continue
            try:
                clf = coredeserializemodel(u.get("modelo"))
                if not clf:
                    continue

                # --- Resolución de aliases (camelCase legacy vs snake_case) ---
                autocheck = u.get("auto_check") or u.get("autocheck") or 0.0


                lastseen = u.get("lastseen") or u.get("last_seen")
                intentosfraude = u.get("intentosfraude") or u.get("intentos_fraude") or 0
                refaudiourl = u.get("refaudiourl") or u.get("ref_audio_url")
                muestras = u.get("muestras") or 0
                createdat = u.get("createdat") or u.get("created_at")
                dni = u.get("dni")

                nuevocache[dni] = {
                    "dni":           dni,
                    "nombre":        u.get("nombre"),
                    "modelo":        clf,
                    "autocheck":     float(autocheck),
                    "lastseen":      lastseen,
                    "intentosfraude": int(intentosfraude),
                    "refaudiourl":   refaudiourl,
                    "muestras":      int(muestras),
                    "createdat":     createdat,
                }
            except Exception:
                pass
        CACHEMODELOS = nuevocache
        logger.info(f"✅ Caché actualizada: {len(CACHEMODELOS)} modelos en memoria.")
    except Exception as e:
        logger.error(f"❌ Error refrescando caché: {e}")


def trainingworkerprocess():
    logger.info("⚙️ [WORKER] Hilo de entrenamiento iniciado.")
    while True:
        try:
            task = taskqueue.get()
            dni = task.get("dni")
            nombre = task.get("nombre")
            folderpath = task.get("folderpath")

            filepaths = []
            if folderpath and os.path.exists(folderpath):
                filepaths = [os.path.join(folderpath, f) for f in os.listdir(folderpath)]

            if not filepaths:
                taskqueue.task_done()
                continue

            cmd = [sys.executable, "train_worker.py", str(dni), str(nombre)] + filepaths
            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode == 0:
                logger.info(f"✅ [WORKER] Éxito para {dni}.")
                resjson = f"temp_uploads/result_{dni}.json"
                if os.path.exists(resjson):
                    try:
                        os.remove(resjson)
                    except Exception:
                        pass
                refrescarcachemodelossync()
            else:
                logger.error(f"❌ [WORKER] Fallo entrenamiento {dni}:\n{result.stderr}")

            try:
                if folderpath and os.path.exists(folderpath):
                    shutil.rmtree(folderpath)
            except Exception:
                pass

            taskqueue.task_done()
        except Exception as e:
            logger.error(f"❌ [WORKER] Error loop: {e}")


@app.on_event("startup")
async def startupevent():
    threading.Thread(target=loadconfigsync, daemon=True).start()
    threading.Thread(target=refrescarcachemodelossync, daemon=True).start()
    threading.Thread(target=trainingworkerprocess, daemon=True).start()

# ==============================================================================
# 2. CAPTURA INTELIGENTE ON-DEMAND (SILERO VAD)
# ==============================================================================

def capturevadaudio(maxduration=15.0, silencetail=2.0, sr=16000) -> Optional[np.ndarray]:
    if VADMODEL is None:
        logger.error("VAD Model no cargado.")
        return None

    chunksize = 512
    prebuffersize = int((0.5 * sr) / chunksize)
    prebuffer = collections.deque(maxlen=prebuffersize)
    recordedframes = []
    isspeaking = False
    silencestarttime = None

    logger.info("🎙️ [VAD] Micrófono ABIERTO. Esperando habla humana...")
    try:
        with sd.InputStream(samplerate=sr, channels=1, dtype="float32") as stream:
            starttime = time.time()
            while (time.time() - starttime) < maxduration:
                audiochunk, _ = stream.read(chunksize)
                audionp = audiochunk.flatten()
                audiotensor = torch.from_numpy(audionp)
                speechprob = VADMODEL(audiotensor, sr).item()

                if speechprob > 0.5:
                    if not isspeaking:
                        isspeaking = True
                        logger.info("🎙️ [VAD] Habla detectada. Grabando...")
                        recordedframes.extend(list(prebuffer))
                        silencestarttime = None
                    recordedframes.append(audionp)
                else:
                    if isspeaking:
                        recordedframes.append(audionp)
                        if silencestarttime is None:
                            silencestarttime = time.time()
                        elif (time.time() - silencestarttime) >= silencetail:
                            logger.info(f"🎙️ [VAD] {silencetail}s de silencio detectados. CORTANDO.")
                            break
                    else:
                        prebuffer.append(audionp)
    except Exception as e:
        logger.error(f"❌ Error al acceder al micrófono físico: {e}")
        return None

    if not recordedframes:
        logger.warning("🎙️ [VAD] No se detectó habla humana en el tiempo límite.")
        return None

    logger.info("🎙️ [VAD] Micrófono CERRADO exitosamente.")
    return np.concatenate(recordedframes)

# ==============================================================================
# 3. ENDPOINTS SIMULADOS (compatibilidad UI)
# ==============================================================================

@app.post("/togglespy")
async def togglespy(payload: dict = Body(...)):
    return {"paused": True}

@app.post("/startspy")
async def startspy():
    return {"paused": True}

# ==============================================================================
# 4. TELEMETRÍA Y LOGS
# ==============================================================================

def pushverifylog(entry: dict, pipeline: dict = None) -> None:
    global LASTPIPELINE
    try:
        with VERIFYLOGLOCK:
            VERIFYLOG.insert(0, entry)
            if len(VERIFYLOG) > 50:
                VERIFYLOG.pop()
            if pipeline is not None:
                LASTPIPELINE = pipeline
    except Exception as e:
        logger.error(f"⚠️ Error escribiendo VERIFYLOG: {e}")


def makelogentry(dnifinal: str, estado: str, score: float, mensaje: str, traceid: Optional[str]) -> dict:
    return {
        "timestamp":    nowhms(),
        "dnireclamado": dnifinal,
        "estado":       estado,
        "score":        safefloat(score, 0.0),
        "mensaje":      mensaje or "",
        "traceid":      traceid,
        "source":       "native-vad",
    }


def saveverificationhistory(dni: str, resultado: dict):
    try:
        nowstr = datetime.now().isoformat()
        data = {
            "dniusuario": dni,
            "score":      float(resultado.get("score", 0)),
            "estado":     resultado.get("estado", "ERROR"),
            "mensaje":    resultado.get("mensaje", ""),
            "fecha":      nowstr,
        }
        supabase.table("historialverificaciones").insert(data).execute()

        isfraude = resultado.get("estado") == "FRAUDE"
        res = supabase.table("usuarios").select("intentosfraude").eq("dni", dni).execute()
        currentfraude = res.data[0].get("intentosfraude", 0) if res.data else 0

        updatedata = {"lastseen": nowstr}
        if isfraude:
            currentfraude += 1
            updatedata["intentosfraude"] = currentfraude

        supabase.table("usuarios").update(updatedata).eq("dni", dni).execute()

        if dni in CACHEMODELOS:
            CACHEMODELOS[dni]["lastseen"] = nowstr
            if isfraude:
                CACHEMODELOS[dni]["intentosfraude"] = currentfraude
    except Exception as e:
        logger.error(f"Error guardando historial: {e}")

# ==============================================================================
# 5. ENDPOINT DE VERIFICACIÓN MAESTRO
# ==============================================================================

@app.post("/verify")
@app.post("/verifyfile")
@app.post("/verify_file")
async def verify_endpoint(
    request: Request,
    file: UploadFile = File(None),
    dnireclamado: str = Form(None),
    traceid: str = Form(None),
):
    content_type = (request.headers.get("content-type") or "").lower()
    dnifinal = dnireclamado

    # FIX: frontend envía 'dni_reclamado' (con guion bajo); FastAPI Form() no lo mapea automáticamente.
    # Se lee el form raw para capturar el campo correcto.
    if not dnifinal:
        try:
            form_data = await request.form()
            dnifinal = (
                form_data.get("dni_reclamado") or
                form_data.get("dnireclamado") or
                form_data.get("dni") or
                dnifinal
            )
        except Exception:
            pass

    if "application/json" in content_type:
        try:
            raw = await request.body()
            body = json.loads(raw.decode("utf-8") or "{}") if raw else {}
            dnifinal = (
                body.get("dnireclamado") or 
                body.get("dni_reclamado") or 
                body.get("dni") or 
                dnifinal
            )
            logger.info(f"🧾 [VERIFY] JSON keys={list(body.keys())} dnifinal='{dnifinal}' traceid='{traceid}'")
        except Exception as e:
            logger.warning(f"⚠️ [VERIFY] No se pudo parsear JSON: {e}")

    if not dnifinal:
        return {"estado": "ERROR", "mensaje": "Falta DNI", "score": 0.0}

    pipeline = {
        "input": {"status": "active"},
        "spoof": {"status": "active"},
        "ai":    {"status": "active"},
        "db":    {"status": "active"},
    }

    session = getsession(dnifinal)
    session["intentos"] += 1
    intentoactual = session["intentos"]
    logger.info(f"⚡ [TRIGGER] Verificando DNI: {dnifinal} | Intento: {intentoactual}")

    # ── MODO DE CAPTURA: archivo subido (Inspector UI) o micrófono VAD (ElevenLabs)
    if file is not None and file.filename:
        # Inspector UI envía WAV grabado en navegador → usarlo directamente, NO abrir micrófono
        logger.info(f"📎 [VERIFY] Archivo recibido: {file.filename} — usando audio subido, omitiendo VAD.")
        try:
            import io as _io
            raw_bytes = await file.read()
            audio_np, audio_sr = sf.read(_io.BytesIO(raw_bytes), dtype="float32", always_2d=False)
            if audio_np.ndim > 1:
                audio_np = audio_np[:, 0]
            if audio_sr != NATIVESR:
                audio_np = librosa.resample(audio_np, orig_sr=audio_sr, target_sr=NATIVESR)
            audiocrudo = audio_np
            logger.info(f"✅ [VERIFY] Audio cargado desde archivo: {len(audiocrudo)/NATIVESR:.2f}s @ {NATIVESR}Hz")
        except Exception as e_file:
            logger.error(f"❌ [VERIFY] Error leyendo archivo de audio: {e_file}")
            return {"estado": "ERROR", "mensaje": f"Error procesando audio subido: {e_file}", "score": 0.0}
    else:
        # Sin archivo → captura desde micrófono físico con Silero VAD (ElevenLabs client tool)
        logger.info("🎙️ [VERIFY] Sin archivo adjunto — activando VAD micrófono físico.")
        audiocrudo = await asyncio.to_thread(capturevadaudio, 15.0, 2.0, NATIVESR)

    pipeline["input"]["status"] = "success"

    if audiocrudo is None:
        msg = "No se escuchó al usuario o hay demasiado ruido."
        pushverifylog(makelogentry(dnifinal, "AUDIOINSUFICIENTE", 0.0, msg, traceid), pipeline)
        return {"estado": "AUDIOINSUFICIENTE", "mensaje": msg, "score": 0.0}

    shortid = uuid.uuid4().hex[:6]
    timestampstr = datetime.now().strftime("%y%m%d_%H%M%S")
    audiofilename = f"{dnifinal}_{shortid}_{timestampstr}.wav"
    audiopath = os.path.join("verificaciones_audio", audiofilename)
    sf.write(audiopath, audiocrudo, NATIVESR)
    logger.info(f"📁 [STORAGE] Audio crudo guardado en: {audiopath}")

    # --- DEEPFAKE ---
    if SYSTEMCONFIG.get("activedeepfake", False) and deepfakeengineready():
        bypass_enabled = bool(SYSTEMCONFIG.get("deepfakebypass", True))
        already_passed = bool(session.get("deepfakepassed", False))

        if (not bypass_enabled) or (not already_passed):
            modelkey = (SYSTEMCONFIG.get("deepfakemodel", "500m") or "500m").strip().lower()
            params = get_df_params_for_model(modelkey)

            minsamples = int(max(0.0, float(params["minsecs"])) * float(NATIVESR))
            if len(audiocrudo) < max(1, minsamples):
                logger.info("🕵️ [Deepfake] Skip por minsecs (audio corto).")
                pipeline["spoof"]["status"] = "success"
            else:
                r = rms_np(audiocrudo)
                if r < float(params["minrms"]):
                    logger.info("🕵️ [Deepfake] Skip por minrms (audio muy bajo).")
                    pipeline["spoof"]["status"] = "success"
                else:
                    audiopre = clamp_audio_maxabs(audiocrudo, float(params["maxabs"]))
                    logger.info(
                        f"🕵️ [Deepfake] Evaluando | model={modelkey} "
                        f"| thr={params['threshold']:.4f} | minsecs={params['minsecs']:.2f} | minrms={params['minrms']:.4f}"
                    )
                    isfake, fakescore = coredetectdeepfake(audiopre, NATIVESR, threshold=float(params["threshold"]))
                    pipeline["spoof"]["status"] = "success"
                    statusdf = "DEEPFAKE" if isfake else "REAL"
                    logger.info(f"🕵️ [Deepfake] Resultado: {statusdf} | Score: {fakescore:.4f}")

                    if isfake:
                        msg = f"BLOQUEADO: Audio sintético ({fakescore:.4f})"
                        resfake = {"estado": "DEEPFAKE", "score": float(fakescore), "mensaje": msg}
                        threading.Thread(target=saveverificationhistory, args=(dnifinal, resfake)).start()
                        pushverifylog(makelogentry(dnifinal, "DEEPFAKE", float(fakescore), msg, traceid), pipeline)
                        return resfake

                    if bypass_enabled:
                        session["deepfakepassed"] = True
                        logger.info("🕵️ [Deepfake] Humano OK. Bypass activado para futuros intentos.")
        else:
            logger.info("⏭️ [Deepfake] BYPASS: El usuario ya pasó deepfake en esta sesión.")
            pipeline["spoof"]["status"] = "success"
    else:
        pipeline["spoof"]["status"] = "success"

    # --- BIOMETRÍA ---
    if not bool(SYSTEMCONFIG.get("activebiometrics", True)):
        msg = "Biometría desactivada desde Ajustes."
        resultado = {"estado": "BIOMETRIA_DESACTIVADA", "score": 0.0, "mensaje": msg}
        pipeline["ai"]["status"] = "success"
        pipeline["db"]["status"] = "success"
        pushverifylog(makelogentry(dnifinal, resultado["estado"], 0.0, msg, traceid), pipeline)
        threading.Thread(target=saveverificationhistory, args=(dnifinal, resultado)).start()
        logger.info(f"🏁 [FINAL] {dnifinal}: BIOMETRIA_DESACTIVADA")
        return resultado

    if dnifinal not in CACHEMODELOS:
        refrescarcachemodelossync()

    if dnifinal not in CACHEMODELOS:
        msg = "Usuario no registrado."
        pushverifylog(makelogentry(dnifinal, "DESCONOCIDO", 0.0, msg, traceid), pipeline)
        return {"estado": "DESCONOCIDO", "mensaje": msg, "score": 0.0}

    logger.info("🧠 [Biometría] Evaluando huella original...")
    umbralactual = float(SYSTEMCONFIG.get("umbralidentidad", 0.5))
    resultado = coreprocessverification(dnifinal, audiopath, CACHEMODELOS, umbral=umbralactual)

    pipeline["ai"]["status"] = "success"
    pipeline["db"]["status"] = "success"

    pushverifylog(
        makelogentry(dnifinal, resultado.get("estado"), resultado.get("score", 0.0), resultado.get("mensaje", ""), traceid),
        pipeline,
    )
    threading.Thread(target=saveverificationhistory, args=(dnifinal, resultado)).start()
    logger.info(f"🏁 [FINAL] Verificación {dnifinal}: {resultado.get('estado')} ({safefloat(resultado.get('score', 0.0)):.2f})")
    return resultado

# ==============================================================================
# 6. ENDPOINTS CRUD USUARIOS
# ==============================================================================

@app.post("/users")
async def createuser(dni: str = Form(...), nombre: str = Form(...), files: List[UploadFile] = File(...)):
    if dni in CACHEMODELOS:
        raise HTTPException(400, f"El usuario {dni} ya existe.")
    basetemp = "temp_uploads"
    userfolder = os.path.join(basetemp, f"train_{dni}")
    os.makedirs(userfolder, exist_ok=True)
    for i, fup in enumerate(files):
        savepath = os.path.join(userfolder, f"audio_{i}.{fup.filename.split('.')[-1]}")
        with open(savepath, "wb") as f:
            f.write(await fup.read())
    taskqueue.put({"dni": dni, "nombre": nombre, "folderpath": userfolder})
    return {"status": "ok", "msg": "Entrenamiento en cola."}


@app.delete("/users/{dni}")
def deleteuser(dni: str):
    supabase.table("usuarios").delete().eq("dni", dni).execute()
    try:
        supabase.storage.from_("modelos").remove([f"{dni}.pkl"])
    except Exception:
        pass
    if dni in CACHEMODELOS:
        del CACHEMODELOS[dni]
    return {"status": "ok"}


@app.put("/users/{dni}")
async def updateuser(dni: str, payload: dict = Body(...)):
    if payload.get("nombre"):
        supabase.table("usuarios").update({"nombre": payload["nombre"]}).eq("dni", dni).execute()
        refrescarcachemodelossync()
    return {"status": "ok"}

# ==============================================================================
# 7. ENDPOINTS DE CONFIGURACIÓN
# ==============================================================================

@app.get("/config")
def getconfig():
    out = dict(SYSTEMCONFIG)
    for snake, legacy in SNAKETOLEGACY.items():
        out[legacy] = out.get(snake)
    return out


@app.post("/config")
async def updateconfig(payload: dict = Body(...)):
    global SYSTEMCONFIG
    normalized = {LEGACYTOSNAKE.get(k, k): v for k, v in (payload or {}).items()}
    for k, v in normalized.items():
        if k in SYSTEMCONFIG:
            SYSTEMCONFIG[k] = v
    if "deepfakemodel" in normalized:
        coresetdeepfakemodel(SYSTEMCONFIG.get("deepfakemodel"))
    persistconfigtodb()
    logger.info(f"⚙️ Configuración actualizada vía UI: {SYSTEMCONFIG}")
    return {"status": "ok", "config": getconfig()}

# ==============================================================================
# 8. ENDPOINTS DE TELEMETRÍA Y LOGS
# ==============================================================================

@app.get("/verifylog")
def getverifylog(raw: bool = False):
    if raw:
        return VERIFYLOG
    with VERIFYLOGLOCK:
        return {"log": list(VERIFYLOG), "pipeline": LASTPIPELINE}


@app.get("/progress")
def getprogress(dni: str):
    path = f"temp_uploads/progress_{dni}.json"
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"step": "WAIT", "percent": 0}

# ==============================================================================
# 9. ENDPOINTS CRM — USUARIOS
# ==============================================================================

@app.get("/userslist")
def getuserslist():
    users = []
    for dni, data in CACHEMODELOS.items():
        users.append({
            "dni":           dni,
            "nombre":        data.get("nombre"),
            "autocheck":     safefloat(data.get("autocheck", 0.0), 0.0),
            "auto_check":    safefloat(data.get("autocheck", 0.0), 0.0),
            "lastseen":      data.get("lastseen"),
            "last_seen":     data.get("lastseen"),
            "intentosfraude": int(data.get("intentosfraude", 0) or 0),
            "intentos_fraude": int(data.get("intentosfraude", 0) or 0),
            "muestras":      int(data.get("muestras", 0) or 0),
            "createdat":     data.get("createdat"),
            "created_at":    data.get("createdat"),
            "refaudiourl":   data.get("refaudiourl"),
            "ref_audio_url": data.get("refaudiourl"),
        })
    return users


@app.get("/userdetails/{dni}")
@app.get("/user_details/{dni}")
def getuserdetails(dni: str):
    res = supabase.table("usuarios").select("*").eq("dni", dni).execute()
    if not res.data:
        raise HTTPException(404, "Usuario no encontrado")
    u = res.data[0]

    autocheck_val = float(u.get("autocheck") or u.get("auto_check") or 0.0)
    refaudio_val  = u.get("refaudiourl") or u.get("ref_audio_url") or ""
    lastseen_val  = u.get("lastseen") or u.get("last_seen")
    createdat_val = u.get("createdat") or u.get("created_at")
    fraudes_val   = int(u.get("intentosfraude") or u.get("intentos_fraude") or 0)

    return {
        "dni":             u.get("dni"),
        "nombre":          u.get("nombre"),
        "muestras":        int(u.get("muestras") or 0),
        "autocheck":       autocheck_val,
        "auto_check":      autocheck_val,
        "refaudiourl":     refaudio_val,
        "ref_audio_url":   refaudio_val,
        "lastseen":        lastseen_val,
        "last_seen":       lastseen_val,
        "createdat":       createdat_val,
        "created_at":      createdat_val,
        "intentosfraude":  fraudes_val,
        "intentos_fraude": fraudes_val,
        "modelo":          u.get("modelo"),
    }


@app.get("/userhistory/{dni}")
@app.get("/user_history/{dni}")
def getuserhistory(dni: str):
    res = (
        supabase.table("historialverificaciones")
        .select("*")
        .eq("dniusuario", dni)
        .order("fecha", desc=True)
        .limit(20)
        .execute()
    )
    return {"history": res.data}


@app.get("/allsessions")
@app.get("/all_sessions")
def getallsessions():
    try:
        res = (
            supabase.table("historialsesiones")
            .select("*")
            .order("fechainicio", desc=True)
            .limit(100)
            .execute()
        )
        return [
            {
                "conversationid": s.get("conversationid") or s.get("conversation_id"),
                "conversation_id": s.get("conversationid") or s.get("conversation_id"),
                "fecha":          s.get("fechainicio") or s.get("fecha_inicio"),
                "duracion":       int(s.get("duracionseg") or s.get("duracion_seg") or 0),
                "dni":            s.get("dniusuario") or s.get("dni_usuario"),
                "nombreusuario":  (
                    CACHEMODELOS[s.get("dniusuario") or s.get("dni_usuario")]["nombre"]
                    if (s.get("dniusuario") or s.get("dni_usuario")) in CACHEMODELOS
                    else "Desconocido"
                ),
                "estado": s.get("estadollamada") or s.get("estado_llamada") or "unknown",
            }
            for s in (res.data or [])
        ]
    except Exception as e:
        logger.error(f"❌ Error leyendo historialsesiones: {e}")
        return []


@app.post("/historysync")
@app.post("/history/sync")
async def sync_history_from_elevenlabs():
    if not ELEVENLABS_API_KEY:
        return {"status": "error", "msg": "Falta ELEVENLABS_API_KEY en .env"}

    try:
        import httpx
        headers = {"xi-api-key": ELEVENLABS_API_KEY}
        params = {"page_size": 50}
        if ELEVENLABS_AGENT_ID:
            params["agent_id"] = ELEVENLABS_AGENT_ID

        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                "https://api.elevenlabs.io/v1/convai/conversations",
                headers=headers,
                params=params,
            )
            r.raise_for_status()
            convs = r.json().get("conversations", [])

        new_sessions = 0
        for c in convs:
            conv_id = c.get("conversation_id") or c.get("id")
            if not conv_id:
                continue
            try:
                exists = (
                    supabase.table("historialsesiones")
                    .select("id")
                    .eq("conversationid", conv_id)
                    .execute()
                )
                if exists.data:
                    continue
            except Exception:
                continue

            start_ts = c.get("start_time_unix_secs") or c.get("start_time")
            if start_ts:
                try:
                    fecha_iso = datetime.fromtimestamp(float(start_ts), tz=timezone.utc).isoformat()
                except Exception:
                    fecha_iso = datetime.now(tz=timezone.utc).isoformat()
            else:
                fecha_iso = datetime.now(tz=timezone.utc).isoformat()

            row = {
                "conversationid": conv_id,
                "dniusuario":     None,
                "fechainicio":    fecha_iso,
                "duracionseg":    int(c.get("call_duration_secs") or c.get("duration") or 0),
                "estadollamada":  c.get("status", "unknown"),
                "metadata":       c,
            }
            try:
                supabase.table("historialsesiones").insert(row).execute()
                new_sessions += 1
            except Exception as e_ins:
                logger.warning(f"[SYNC] No se pudo insertar sesión {conv_id}: {e_ins}")

        logger.info(f"[SYNC] Sincronización completada. Nuevas sesiones: {new_sessions}")
        return {"status": "ok", "new_sessions": new_sessions}

    except Exception as e:
        logger.error(f"❌ [SYNC] Error sincronizando ElevenLabs: {e}")
        return {"status": "error", "msg": str(e)}


@app.get("/health")
def healthcheck():
    return {
        "status":    "ok",
        "usuarios":  len(CACHEMODELOS),
        "spypaused": True,
        "config":    getconfig(),
        "models": {
            "titanet":  {"loaded": getcoretitanetmodel() is not None},
            "deepfake": {"loaded": deepfakeengineready()},
        },
    }


@app.get("/")
def readindex():
    return FileResponse("static/index.html")


# ==============================================================================
# 10. HISTORIAL — DETALLES DE CONVERSACIÓN Y AUDIO
# ==============================================================================

@app.get("/history/details/{conv_id}")
async def get_conversation_details(conv_id: str):
    """Obtiene la transcripción de una conversación de ElevenLabs por su ID."""
    if not ELEVENLABS_API_KEY:
        raise HTTPException(500, "Falta ELEVENLABS_API_KEY en .env")
    if not conv_id or conv_id in ("undefined", "null", ""):
        raise HTTPException(400, "conversation_id inválido")
    try:
        import httpx
        headers = {"xi-api-key": ELEVENLABS_API_KEY}
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"https://api.elevenlabs.io/v1/convai/conversations/{conv_id}",
                headers=headers,
            )
            if r.status_code == 404:
                return {"transcription": [], "error": "Conversación no encontrada en ElevenLabs."}
            r.raise_for_status()
            data = r.json()
        transcript_raw = data.get("transcript") or []
        normalized = []
        for msg in transcript_raw:
            normalized.append({
                "role": msg.get("role", "agent"),
                "text": msg.get("message") or msg.get("text") or "",
                "time_in_call_secs": msg.get("time_in_call_secs"),
            })
        return {
            "conversation_id": conv_id,
            "transcription": normalized,
            "status": data.get("status"),
            "duration": data.get("call_duration_secs") or data.get("duration"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[HISTORY-DETAILS] Error para {conv_id}: {e}")
        return {"transcription": [], "error": f"Error obteniendo transcripción: {str(e)}"}


@app.get("/history/audio/{conv_id}")
async def get_conversation_audio(conv_id: str):
    """Proxy del audio de una conversación desde ElevenLabs."""
    if not ELEVENLABS_API_KEY:
        raise HTTPException(500, "Falta ELEVENLABS_API_KEY en .env")
    if not conv_id or conv_id in ("undefined", "null", ""):
        raise HTTPException(400, "conversation_id inválido")
    try:
        import httpx
        from fastapi.responses import StreamingResponse
        headers = {"xi-api-key": ELEVENLABS_API_KEY}
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(
                f"https://api.elevenlabs.io/v1/convai/conversations/{conv_id}/audio",
                headers=headers,
            )
            if r.status_code == 404:
                raise HTTPException(404, "Audio no disponible para esta conversación.")
            r.raise_for_status()
            content = r.content
            content_type = r.headers.get("content-type", "audio/mpeg")
        return StreamingResponse(
            iter([content]),
            media_type=content_type,
            headers={
                "Content-Disposition": f"inline; filename={conv_id}.mp3",
                "Accept-Ranges": "bytes",
                "Cache-Control": "no-cache",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[HISTORY-AUDIO] Error para {conv_id}: {e}")
        raise HTTPException(500, f"Error obteniendo audio: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)