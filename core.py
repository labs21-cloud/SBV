# ==============================================================================
# ARCHIVO: core.py
# ROL: Motor de Inteligencia Artificial y Procesamiento Biométrico (Modo Offline).
# DESCRIPCIÓN:
#   - Carga NVIDIA TitaNet (NeMo) para embeddings de voz
#   - Carga DF_Arena (Transformers pipeline antispoofing) en modo LOCAL ONLY
#   - Extrae embeddings, hace slicing/augment y verifica con SVM
#   - Mantiene aliases legacy para compatibilidad con main.py y train_worker.py
# ==============================================================================

import os
import logging
import pickle
import codecs
import tempfile
import shutil
import gc
from pathlib import Path
from threading import Lock
from typing import Optional, Dict, Tuple, List

import numpy as np
import soundfile as sf
import librosa
import torch
import torch.nn.functional as F

# ==============================================================================
# 0) OFFLINE + CACHE (ANTES de importar transformers)
# ==============================================================================

# "Solo local": sin llamadas HTTP al Hub (si falta algo, falla).
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

# Cache estable (evita corrupciones del modules cache en Windows)
_LOCALAPPDATA = os.getenv("LOCALAPPDATA")
if _LOCALAPPDATA:
    _HF_BASE = Path(_LOCALAPPDATA) / "tfm_hf_cache"
else:
    _HF_BASE = Path.home() / ".tfm_hf_cache"

os.environ.setdefault("HF_HOME", str(_HF_BASE))
os.environ.setdefault("HF_MODULES_CACHE", str(_HF_BASE / "modules"))
Path(os.environ["HF_HOME"]).mkdir(parents=True, exist_ok=True)
Path(os.environ["HF_MODULES_CACHE"]).mkdir(parents=True, exist_ok=True)

# ==============================================================================
# 1) Imports pesados (después de fijar OFFLINE/HF_HOME)
# ==============================================================================

import nemo.collections.asr as nemo_asr
from transformers import pipeline
from audiomentations import Compose, AddGaussianNoise, Gain

# ==============================================================================
# 2) LOGGING
# ==============================================================================

logger = logging.getLogger("biometria")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s - [%(levelname)s] - %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(_h)

# ==============================================================================
# 3) CONFIG / MODELOS (TitaNet)
# ==============================================================================

SR_TITANET = 16000
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

# define siempre ambos nombres para evitar NameError
titanet_model = None   # nombre "nuevo"
titanetmodel = None    # alias legacy sin underscore

logger.info(f"[core] Cargando Titanet en {DEVICE}...")
try:
    # Modelo embeddings (speaker verification)
    titanet_model = nemo_asr.models.EncDecSpeakerLabelModel.from_pretrained("titanet_large")
    titanet_model = titanet_model.to(DEVICE).eval()

    # Alias legacy (para compatibilidad con main.py y versiones anteriores)
    titanetmodel = titanet_model

    logger.info("[core] Titanet cargado.")
except Exception as e:
    logger.error(f"[core] Error cargando Titanet: {e}")
    titanet_model = None
    titanetmodel = None

# ==============================================================================
# 4) DEEPFAKE / ANTI-SPOOF (DF_Arena local: 1B y 500M)
# ==============================================================================

DEEPFAKE_TASK = "antispoofing"

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# carpetas locales dentro del proyecto
DF_ARENA_DIR_1B = os.path.join(PROJECT_ROOT, "hf_models", "df_arena_1b")
DF_ARENA_DIR_500M = os.path.join(PROJECT_ROOT, "hf_models", "df_arena_500m_v1")

# Variantes disponibles
DEEPFAKE_VARIANTS: Dict[str, str] = {
    "1b": DF_ARENA_DIR_1B,
    "500m": DF_ARENA_DIR_500M,
}

# Variante activa por defecto (se puede sobreescribir por env)
# Ejemplo: setx DF_ARENA_VARIANT "1b" (o "500m")
_active_variant = (os.getenv("DF_ARENA_VARIANT", "500m") or "500m").strip().lower()

# Compatibilidad con env anterior DF_ARENA_PATH:
# - Si DF_ARENA_PATH está seteada, fuerza la variante "custom_path"
_custom_path = (os.getenv("DF_ARENA_PATH", "") or "").strip()
if _custom_path:
    DEEPFAKE_VARIANTS["custom_path"] = _custom_path
    _active_variant = "custom_path"

_spoof_lock = Lock()
_spoof_classifier = None
_spoof_init_attempted = False

# Aliases públicos (main.py /health los inspecciona)
spoofclassifier = None
spoof_classifier = None

# ---- Parámetros deepfake runtime (por variante) ----
_DEFAULT_DF_PARAMS = {
    "threshold": 0.50,
    "min_secs": 1.20,
    "min_rms": 0.006,
    "max_abs": 0.999,
}

_DEEPFAKE_PARAMS_BY_VARIANT: Dict[str, Dict[str, float]] = {
    k: dict(_DEFAULT_DF_PARAMS) for k in DEEPFAKE_VARIANTS.keys()
}


def available_deepfake_models() -> List[str]:
    """Devuelve las claves disponibles para UI/config."""
    return sorted(list(DEEPFAKE_VARIANTS.keys()))


def get_deepfake_model_variant() -> str:
    """Devuelve la variante activa."""
    return _active_variant


def _ensure_variant_params_exists(variant: str) -> None:
    v = (variant or "").strip().lower()
    if not v:
        return
    if v not in _DEEPFAKE_PARAMS_BY_VARIANT:
        _DEEPFAKE_PARAMS_BY_VARIANT[v] = dict(_DEFAULT_DF_PARAMS)


def get_deepfake_params(variant: Optional[str] = None) -> Dict[str, float]:
    """Devuelve params efectivos para una variante (o la activa si None)."""
    v = (variant or _active_variant or "500m").strip().lower()
    _ensure_variant_params_exists(v)
    return dict(_DEEPFAKE_PARAMS_BY_VARIANT.get(v, _DEFAULT_DF_PARAMS))


def set_deepfake_params(
    variant: Optional[str] = None,
    threshold: Optional[float] = None,
    min_secs: Optional[float] = None,
    min_rms: Optional[float] = None,
    max_abs: Optional[float] = None,
) -> Dict[str, float]:
    """
    Actualiza params deepfake por variante (o activa si variant=None).
    No toca el pipeline; solo cambia guardas/threshold para futuras llamadas.
    """
    v = (variant or _active_variant or "500m").strip().lower()
    _ensure_variant_params_exists(v)

    p = _DEEPFAKE_PARAMS_BY_VARIANT[v]

    def _upd(key: str, val: Optional[float]):
        if val is None:
            return
        try:
            p[key] = float(val)
        except Exception:
            pass

    _upd("threshold", threshold)
    _upd("min_secs", min_secs)
    _upd("min_rms", min_rms)
    _upd("max_abs", max_abs)

    # sane defaults
    if p.get("min_secs", 0.0) < 0:
        p["min_secs"] = 0.0
    if p.get("min_rms", 0.0) < 0:
        p["min_rms"] = 0.0
    if p.get("max_abs", 0.999) <= 0:
        p["max_abs"] = 0.999

    return dict(p)


def _seed_transformers_modules_cache_from_local_repo(model_dir: Path) -> None:
    """
    Con trust_remote_code=True, Transformers ejecuta el código del modelo desde:
    HF_MODULES_CACHE/transformers_modules/<module_name>/
    Se siembra los .py del repo local para evitar FileNotFoundError (conformer.py, etc.)
    """
    try:
        module_name = model_dir.name
        modules_cache = Path(os.environ["HF_MODULES_CACHE"])
        dst = modules_cache / "transformers_modules" / module_name
        dst.mkdir(parents=True, exist_ok=True)

        py_files = list(model_dir.glob("*.py"))
        if not py_files:
            logger.warning(f"[core] DF_Arena local no tiene .py en: {model_dir}")
            return

        for py in py_files:
            shutil.copy2(py, dst / py.name)

        if (dst / "conformer.py").exists():
            logger.info(f"[core] Remote-code cache sembrado: {dst} (conformer.py OK)")
        else:
            logger.warning(f"[core] no quedó conformer.py en modules cache: {dst}")

    except Exception as e:
        logger.warning(f"[core] No se pudo sembrar modules cache (ignorable): {e}")


def _free_spoof_classifier() -> None:
    global _spoof_classifier, spoofclassifier, spoof_classifier
    try:
        _spoof_classifier = None
        spoofclassifier = None
        spoof_classifier = None
        gc.collect()
    except Exception:
        pass


def set_deepfake_model_variant(variant: str) -> bool:
    """
    Cambia la variante activa (1B/500M/custom_path) y carga ese modelo.
    Importante: asegura que SOLO haya 1 pipeline cargado a la vez.
    """
    global _active_variant, _spoof_init_attempted

    v = (variant or "").strip().lower()
    if v not in DEEPFAKE_VARIANTS:
        logger.error(f"[core] deepfake variant inválida: {variant}. Disponibles: {available_deepfake_models()}")
        return False

    _ensure_variant_params_exists(v)

    with _spoof_lock:
        if v == _active_variant and _spoof_classifier is not None:
            return True
        _active_variant = v
        _spoof_init_attempted = False
        _free_spoof_classifier()

    # intenta cargar fuera del lock (pero _ensure_spoof_classifier ya es thread-safe)
    return _ensure_spoof_classifier() is not None


def _ensure_spoof_classifier():
    """
    Inicializa DF_Arena (solo 1 variante activa) una sola vez (thread-safe).
    Si falla, degradación segura: devuelve None.
    """
    global _spoof_classifier, _spoof_init_attempted, spoofclassifier, spoof_classifier

    with _spoof_lock:
        if _spoof_classifier is not None:
            return _spoof_classifier
        if _spoof_init_attempted:
            return None
        _spoof_init_attempted = True

        try:
            device_index = 0 if torch.cuda.is_available() else -1
            model_path = DEEPFAKE_VARIANTS.get(_active_variant)

            if not model_path:
                logger.error(f"[core] No hay path para variante: {_active_variant}")
                return None

            if not os.path.isdir(model_path):
                logger.error(
                    f"[core] DF_Arena LOCAL NO existe para '{_active_variant}': {model_path}. "
                    "Modo LOCAL ONLY: no se descargará nada."
                )
                return None

            logger.info(f"[core] DF_Arena LOCAL ({_active_variant}) detectado en: {model_path}")
            _seed_transformers_modules_cache_from_local_repo(Path(model_path))

            logger.info(f"[core] Cargando DF_Arena (antispoofing) variante={_active_variant} device={device_index}...")
            _spoof_classifier = pipeline(
                DEEPFAKE_TASK,
                model=model_path,           # <- LOCAL
                trust_remote_code=True,     # <- código custom del repo
                device=device_index,
            )

            spoofclassifier = _spoof_classifier
            spoof_classifier = _spoof_classifier

            logger.info(f"[core] DF_Arena cargado OK (variante={_active_variant}).")
            return _spoof_classifier

        except Exception as e:
            logger.error(f"[core] Error cargando DF_Arena: {e}")
            _free_spoof_classifier()
            return None


# Intento de carga al importar (si falla, sigue modo degradado)
_ensure_spoof_classifier()


def _parse_df_arena_output(out) -> float:
    """Convierte salida del pipeline a fake_score [0..1] (alto => spoof)."""
    if isinstance(out, list) and len(out) > 0:
        out = out[0]
    if not isinstance(out, dict):
        return 0.0

    label = str(out.get("label", "")).lower()
    score = float(out.get("score", 0.0) or 0.0)
    all_scores = out.get("all_scores", {}) or {}

    if "spoof" in label:
        return score
    if "bonafide" in label:
        return 1.0 - score

    if isinstance(all_scores, dict) and all_scores:
        if "spoof" in all_scores:
            return float(all_scores.get("spoof", 0.0) or 0.0)
        if "bonafide" in all_scores:
            return 1.0 - float(all_scores.get("bonafide", 0.0) or 0.0)

    return 0.0


def _rms(x: np.ndarray) -> float:
    try:
        if x is None or len(x) == 0:
            return 0.0
        x = x.astype(np.float32, copy=False)
        return float(np.sqrt(np.mean(x * x)))
    except Exception:
        return 0.0


def detect_deepfake(
    audio: np.ndarray,
    sr: int = SR_TITANET,
    threshold: Optional[float] = None,
    min_secs: Optional[float] = None,
    min_rms: Optional[float] = None,
    max_abs: Optional[float] = None,
    variant: Optional[str] = None,
) -> Tuple[bool, float]:
    """
    Devuelve (is_fake: bool, fake_score: float).

    Runtime-configurable:
    - Si se pasa threshold/min_secs/min_rms/max_abs, se aplican directamente.
    - Si no se pasa, se usan los params guardados por variante (set_deepfake_params()).
    """
    clf = _ensure_spoof_classifier()
    if clf is None:
        return False, 0.0

    try:
        if audio is None or len(audio) == 0:
            return False, 0.0

        # mono + float32
        if getattr(audio, "ndim", 1) > 1:
            audio = audio.mean(axis=1)
        audio = audio.astype(np.float32, copy=False)

        # params efectivos
        v = (variant or _active_variant or "500m").strip().lower()
        p = get_deepfake_params(v)

        thr = float(threshold) if threshold is not None else float(p["threshold"])
        msecs = float(min_secs) if min_secs is not None else float(p["min_secs"])
        mrms = float(min_rms) if min_rms is not None else float(p["min_rms"])
        mabs = float(max_abs) if max_abs is not None else float(p["max_abs"])

        if msecs < 0:
            msecs = 0.0
        if mrms < 0:
            mrms = 0.0
        if mabs <= 0:
            mabs = 0.999

        # guardas defensivas
        sr_in = int(sr) if sr else SR_TITANET
        min_samples = int(float(msecs) * float(sr_in))
        if len(audio) < max(1, min_samples):
            return False, 0.0
        if _rms(audio) < float(mrms):
            return False, 0.0

        # resample a 16k
        if sr_in != SR_TITANET:
            try:
                audio = librosa.resample(audio, orig_sr=sr_in, target_sr=SR_TITANET)
            except Exception as e:
                logger.error(f"[core] Error resample deepfake (ignorado): {e}")
                return False, 0.0

        # clamp
        audio = np.clip(audio, -mabs, mabs)

        out = clf(audio)
        fake_score = _parse_df_arena_output(out)
        is_fake = float(fake_score) >= float(thr)
        return bool(is_fake), float(fake_score)

    except Exception as e:
        logger.error(f"[core] Error detect_deepfake (ignorado): {e}")
        return False, 0.0


# ==============================================================================
# 5) AUGMENTACIÓN / SLICER
# ==============================================================================

aug_pipeline = Compose([
    AddGaussianNoise(min_amplitude=0.001, max_amplitude=0.005, p=0.3),
    Gain(min_gain_db=-3.0, max_gain_db=3.0, p=0.8),
])


def smart_slicer(audio: np.ndarray, sr: int) -> list:
    if audio is None or len(audio) == 0:
        return []
    if getattr(audio, "ndim", 1) > 1:
        audio = audio.mean(axis=1)

    if sr != SR_TITANET:
        try:
            audio = librosa.resample(audio, orig_sr=sr, target_sr=SR_TITANET)
        except Exception as e:
            logger.error(f"[smart_slicer] Error resample: {e}")
            return []

    try:
        intervals = librosa.effects.split(audio, top_db=25)
    except Exception:
        intervals = [[0, len(audio)]]

    chunks = []
    for start, end in intervals:
        frag = audio[start:end]
        if len(frag) <= SR_TITANET * 0.5:
            continue
        if len(frag) > SR_TITANET * 5:
            step = int(SR_TITANET * 4)
            for i in range(0, len(frag), step):
                sub = frag[i:i + step]
                if len(sub) > SR_TITANET * 2:
                    chunks.append(sub)
        else:
            chunks.append(frag)

    return chunks


def augment_audio(audio_chunk: np.ndarray) -> np.ndarray:
    try:
        return aug_pipeline(samples=audio_chunk, sample_rate=SR_TITANET)
    except Exception:
        return audio_chunk


# ==============================================================================
# 6) EMBEDDING (TitaNet)
# ==============================================================================

def get_embedding(audio: np.ndarray) -> Optional[np.ndarray]:
    """
    Extrae embedding con TitaNet desde un np.ndarray mono (16k).
    Se usa titanet_model.get_embedding(path2audio_file=...) (API NeMo correcta).
    """
    # Recupera el objeto modelo de forma robusta
    model = titanet_model if titanet_model is not None else titanetmodel
    if model is None:
        return None
    if audio is None or len(audio) < SR_TITANET * 0.3:
        return None

    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(prefix="emb_", suffix=".wav")
        os.close(fd)
        sf.write(tmp_path, audio, SR_TITANET)

        # API correcta NeMo
        emb = model.get_embedding(path2audio_file=tmp_path)
        emb = F.normalize(emb, p=2, dim=-1)
        return emb.squeeze(0).cpu().detach().numpy()

    except Exception as e:
        logger.error(f"[get_embedding] Error: {e}")
        return None

    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


# ==============================================================================
# 7) SERIALIZACIÓN / VERIFICACIÓN
# ==============================================================================

def deserialize_model(model_data):
    if not model_data:
        return None
    try:
        blob = model_data
        if isinstance(blob, str):
            if blob.startswith("\\x"):
                blob = blob[2:]
            try:
                blob = codecs.decode(blob, "hex")
            except Exception:
                blob = blob.encode("latin1")
            try:
                texto_interno = blob.decode("ascii")
                if all(c in "0123456789abcdefABCDEF" for c in texto_interno[:20]):
                    blob = codecs.decode(texto_interno, "hex")
            except Exception:
                pass
        return pickle.loads(blob)
    except Exception as e:
        logger.error(f"[core] Error deserializando: {e}")
        return None


def verify_print(embedding: np.ndarray, model_svc, umbral: float = 0.5) -> tuple:
    if model_svc is None or embedding is None:
        return False, 0.0
    try:
        probs = model_svc.predict_proba([embedding])
        score = probs[0][1]
        return (score >= umbral), float(score)
    except Exception:
        return False, 0.0


# ==============================================================================
# 8) ORQUESTADOR DE VERIFICACIÓN
# ==============================================================================

def process_verification(dni: str, audio_path: str, cache_modelos: dict, umbral: float = 0.5):
    info = cache_modelos.get(dni)
    if not info:
        return {"estado": "DESCONOCIDO", "score": 0.0, "mensaje": f"Usuario {dni} no existe."}

    modelo = info.get("modelo")
    nombre = info.get("nombre", "Usuario")

    try:
        audio, sr = sf.read(audio_path)
        if getattr(audio, "ndim", 1) > 1:
            audio = audio.mean(axis=1)
    except Exception:
        return {"estado": "AUDIO_INSUFICIENTE", "score": 0.0, "mensaje": "Error leyendo audio."}

    chunks = smart_slicer(audio, sr)
    if not chunks:
        chunks = [audio]

    embs = []
    for ch in chunks:
        ch_aug = augment_audio(ch)
        e = get_embedding(ch_aug)
        if e is not None:
            embs.append(e)

    if not embs:
        return {"estado": "AUDIO_INSUFICIENTE", "score": 0.0, "mensaje": "Voz no detectada."}

    emb_final = np.mean(embs, axis=0)
    es_genuino, score = verify_print(emb_final, modelo, umbral)

    estado = "GENUINO" if es_genuino else "FRAUDE"
    msg = f"Identidad verificada: {nombre}" if es_genuino else "No coincide con la huella."
    return {"estado": estado, "score": float(score), "mensaje": msg}


# ==============================================================================
# 9) ALIASES (compatibilidad estricta con main.py y train_worker.py)
# ==============================================================================

SRTITANET = SR_TITANET

# ambos públicos (por si main.py inspecciona cualquiera)
titanet_model = titanet_model
titanetmodel = titanetmodel

# lo que main.py inspecciona
spoofclassifier = _spoof_classifier
spoof_classifier = _spoof_classifier

# aliases de deepfake
detectdeepfake = detect_deepfake
detectDeepfake = detect_deepfake

# aliases de biometría/entrenamiento
getembedding = get_embedding
processverification = process_verification
deserializemodel = deserialize_model

# helpers públicos para UI/config
deepfakemodelsavailable = available_deepfake_models
deepfakemodelcurrent = get_deepfake_model_variant
setdeepfakemodel = set_deepfake_model_variant

# setters/getters params por si el backend quiere inyectar desde Supabase
setdeepfakeparams = set_deepfake_params
getdeepfakeparams = get_deepfake_params
