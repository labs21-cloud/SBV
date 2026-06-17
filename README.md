# 🎙️ Sistema Biométrico de Voz - Prevención de fraude telefónico

> Sistema cloud-native de autenticación vocal con detección activa de deepfakes. Desarrollado como Trabajo de Fin de Máster (TFM).

---

## 📋 Descripción

Este sistema autentica la identidad de usuarios mediante su huella vocal y bloquea audios sintéticos o clonados (deepfakes). Combina modelos de Machine Learning de grado industrial ejecutados **estrictamente en local** (sin llamadas a APIs externas para la toma de decisiones) con una infraestructura cloud basada en **Supabase** y un agente conversacional de **ElevenLabs**.

**Flujo principal:**
1. El agente de voz (ElevenLabs) detecta que se requiere verificación y dispara el `Client Tool` desde el frontend.
2. El backend abre el micrófono físico del servidor mediante **Silero VAD**, graba solo el habla humana y cierra automáticamente.
3. El audio pasa por el motor **DF_Arena** (anti-spoofing): si es sintético, se bloquea con estado `DEEPFAKE`.
4. Si supera el filtro, **TitaNet** extrae la huella vocal y el clasificador **SVM** del usuario decide: `GENUINO` o `FRAUDE`.
5. El resultado se persiste en Supabase y se devuelve al agente para autorizar o denegar el servicio.

---

## 🏗️ Arquitectura

```
┌─────────────────────────────────────────────────────────────────┐
│                        FRONTEND (SPA)                           │
│   index.html + styles.css + app.js  (Vanilla JS, sin frameworks)│
│   Widget ElevenLabs <elevenlabs-convai> + Client Tool           │
└───────────────────────┬─────────────────────────────────────────┘
                        │ REST API (FastAPI)
┌───────────────────────▼─────────────────────────────────────────┐
│                     BACKEND — main.py                           │
│  FastAPI · Silero VAD · Gestión de sesiones · CRUD CRM          │
│  Sincronización ElevenLabs (/historysync)                       │
└──────┬───────────────────────────┬──────────────────────────────┘
       │ Inferencia ML (offline)   │ Entrenamiento (subprocess)
┌──────▼──────────┐        ┌───────▼──────────────────────────────┐
│   core.py       │        │          train_worker.py             │
│  TitaNet        │        │  Extrae embeddings + entrena SVM     │
│  DF_Arena       │        │  Serializa modelo → Supabase DB      │
│  SVM verify     │        └──────────────────────────────────────┘
└─────────────────┘
       │
┌──────▼──────────────────────────────────────────────────────────┐
│                     SUPABASE (Cloud)                            │
│  tabla usuarios    → modelo SVM serializado en HEX              │
│  tabla historial_verificaciones → trazabilidad                  │
│  tabla historial_sesiones       → sesiones ElevenLabs           │
│  tabla configuracion            → parámetros dinámicos          │
│  Storage: audiosreferencia      → .wav de enrolamiento          │
└─────────────────────────────────────────────────────────────────┘
```

---

## 📂 Estructura del Repositorio

```
SBV/
│
├── main.py                        # Orquestador FastAPI (API REST + Silero VAD)
├── core.py                        # Motor IA offline: TitaNet + DF_Arena + SVM
├── train_worker.py                # Worker SVM (subprocess, no bloquea FastAPI)
├── vectores_impostores_v2.pkl     # Dataset de huellas impostoras (precompilado)
│
├── vectores_impostores_code/
│   └── vectores_impostores_final.py  # Script para regenerar el .pkl si es necesario
│
├── static/
│   ├── index.html                 # SPA principal (UI Bancaria mobile-first)
│   ├── styles.css                 # Estilos CSS puro
│   └── app.js                     # Lógica Vanilla JS + integración ElevenLabs
│
├── docs/
│   ├── agente-sienna-setup.md     # Guía completa para crear el agente ElevenLabs
│
├── hf_models/                     # ⚠️ NO incluido en el repo (ver sección Modelos)
├── requirements.txt               # Dependencias Python de producción
├── .env                           # Variables de entorno (rellenar antes de arrancar)
├── .gitignore
└── README.md
```

> ⚠️ `vectores_impostores_v2.pkl` ya viene incluido en el repo (392 KB). Solo ejecuta `vectores_impostores_final.py` si necesitas regenerarlo desde cero.

---

## 🤖 Modelos de IA

### TitaNet-Large (Biometría de voz)
Modelo de NVIDIA para extracción de embeddings de speaker verification. Se carga automáticamente vía **NeMo Toolkit** con el identificador `titanet_large`. Tras la primera descarga, opera en modo **HF_HUB_OFFLINE estricto**.

### DF_Arena (Anti-Spoofing / Detección de Deepfakes)
Modelos open-source del proyecto **Speech DF Arena**. El sistema soporta dos variantes seleccionables desde la UI:

| Variante | Parámetros | HuggingFace | Carpeta local exacta |
|---|---|---|---|
| **500M** *(default)* | 500M | [DF_Arena_500M_V_1](https://huggingface.co/Speech-Arena-2025/DF_Arena_500M_V_1) | `hf_models/df_arena_500m_v1/` |
| **1B** | 1B | [DF_Arena_1B_V_1](https://huggingface.co/Speech-Arena-2025/DF_Arena_1B_V_1) | `hf_models/df_arena_1b/` |

> ⚠️ Los nombres de carpeta son exactos e inamovibles — `core.py` los referencia directamente.

#### Descarga de DF_Arena (obligatoria antes del primer uso)

```bash
mkdir hf_models\df_arena_500m_v1
mkdir hf_models\df_arena_1b

python -c "
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id='Speech-Arena-2025/DF_Arena_500M_V_1',
    local_dir='hf_models/df_arena_500m_v1',
    local_dir_use_symlinks=False
)
"
```

Una vez descargados, el sistema los usa **100% offline** (`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`).

---

## ⚙️ Instalación Completa

### 1. Prerrequisitos

- Python 3.10 o superior
- Micrófono físico accesible en el servidor (para Silero VAD)
- CUDA 12.x opcional (recomendado para DF_Arena 1B)
- Proyecto en [Supabase](https://supabase.com) con tablas configuradas (ver sección Supabase)
- Agente en [ElevenLabs Conversational AI](https://elevenlabs.io) configurado (ver `docs/agente-sienna-setup.md`)

### 2. Clonar e instalar dependencias

```bash
git clone https://github.com/labs21-cloud/SBV.git
cd SBV
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

> Para GPU CUDA 12.1: `pip install torch==2.4.1 --index-url https://download.pytorch.org/whl/cu121`

### 3. Variables de entorno

Rellena el `.env` que ya existe en la raíz:

```env
SUPABASE_URL=https://TU_PROYECTO.supabase.co
SUPABASE_KEY=TU_SERVICE_ROLE_KEY
ELEVENLABS_API_KEY=TU_API_KEY_ELEVENLABS
ELEVENLABS_AGENT_ID=TU_AGENT_ID
```

> ⚠️ Usa la `service_role` key de Supabase (no la `anon`). Se encuentra en **Project Settings → API**.

### 4. Descargar modelos DF_Arena

Sigue los pasos de la sección **Descarga de DF_Arena** indicada arriba.

### 5. Arrancar el servidor

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Interfaz disponible en `http://localhost:8000`.

---

## 🗄️ Configuración de Supabase

Crea estas tablas **antes de arrancar**. Nombres exactos — el código los referencia directamente.

> ⚠️ **RLS (Row Level Security):** Supabase activa RLS por defecto en todas las tablas nuevas. Debes **desactivarlo** en cada tabla o crear policies que permitan acceso total con la `service_role` key. Sin esto, el sistema no puede leer ni escribir datos.
>
> Para desactivar RLS: en cada tabla ve a **Authentication → Policies** y desactiva el toggle "Enable RLS".

### Tabla: `usuarios`

```sql
CREATE TABLE usuarios (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  dni             text UNIQUE NOT NULL,
  nombre          text,
  modelo          text,
  muestras        integer DEFAULT 0,
  autocheck       float DEFAULT 0.0,
  refaudiourl     text,
  lastseen        text,
  intentosfraude  integer DEFAULT 0,
  createdat       timestamptz DEFAULT now()
);
```

### Tabla: `historial_verificaciones`

```sql
CREATE TABLE historial_verificaciones (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  dniusuario  text,
  score       float,
  estado      text,
  mensaje     text,
  fecha       timestamptz DEFAULT now()
);
```

### Tabla: `historial_sesiones`

```sql
CREATE TABLE historial_sesiones (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  conversationid  text UNIQUE,
  dniusuario      text,
  fechainicio     timestamptz,
  duracionseg     integer DEFAULT 0,
  estadollamada   text,
  metadata        jsonb
);
```

### Tabla: `configuracion`

```sql
CREATE TABLE configuracion (
  id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  umbralidentidad   float DEFAULT 0.50,
  activebiometrics  boolean DEFAULT true,
  activedeepfake    boolean DEFAULT false,
  deepfakemodel     text DEFAULT '500m',
  deepfake_bypass   boolean DEFAULT true,
  df500m_threshold  float DEFAULT 0.50,
  df500m_minsecs    float DEFAULT 1.20,
  df500m_minrms     float DEFAULT 0.006,
  df500m_maxabs     float DEFAULT 0.999,
  df1b_threshold    float DEFAULT 0.50,
  df1b_minsecs      float DEFAULT 1.20,
  df1b_minrms       float DEFAULT 0.006,
  df1b_maxabs       float DEFAULT 0.999,
  spybuffersecs     integer DEFAULT 5
);

-- Fila inicial obligatoria (el sistema la lee al arrancar)
INSERT INTO configuracion DEFAULT VALUES;
```

### Storage Bucket: `audiosreferencia`

En Supabase → **Storage → New bucket**:
- **Nombre:** `audiosreferencia` ← sin guiones, exactamente así
- **Public:** ✅ Activado

---

## 🔌 Endpoints API

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/verify` | Verificación biométrica con Silero VAD (micrófono físico) |
| `POST` | `/verify_file` | Auditoría manual con archivo de audio (Inspector UI) |
| `POST` | `/users` | Enrolar nuevo usuario (encola entrenamiento SVM) |
| `DELETE` | `/users/{dni}` | Eliminar usuario y su modelo |
| `PUT` | `/users/{dni}` | Actualizar nombre de usuario |
| `GET` | `/userslist` | Listar todos los usuarios (CRM) |
| `GET` | `/userdetails/{dni}` | Detalle completo de un usuario |
| `GET` | `/userhistory/{dni}` | Historial de verificaciones |
| `GET` | `/allsessions` | Sesiones ElevenLabs sincronizadas |
| `GET` | `/config` | Leer configuración activa |
| `POST` | `/config` | Actualizar umbrales y parámetros |
| `POST` | `/historysync` | Sincronizar sesiones ElevenLabs → Supabase |
| `GET` | `/health` | Estado del sistema y modelos cargados |
| `GET` | `/progress?dni=X` | Progreso del entrenamiento SVM |
| `GET` | `/verify_log` | Últimas 50 verificaciones en memoria |

---

## 🔒 Seguridad

- Los modelos SVM **nunca se almacenan en disco**: se serializan a hexadecimal y se persisten en la columna `modelo` de `usuarios` en Supabase.
- DF_Arena opera completamente offline — `HF_HUB_OFFLINE=1` y `TRANSFORMERS_OFFLINE=1` forzados en `core.py`. Ningún audio sale del servidor.
- Credenciales únicamente en `.env` local.
- **Bypass por sesión** configurable (`deepfake_bypass`): superado el filtro anti-spoofing una vez, no se re-evalúa en la misma sesión.

---

## 🛠️ Stack Tecnológico

| Capa | Tecnología |
|---|---|
| Backend | Python 3.10+ · FastAPI · Uvicorn |
| Biometría | NVIDIA TitaNet-Large (NeMo Toolkit) |
| Anti-Spoofing | DF_Arena 500M / 1B (Transformers pipeline, offline) |
| Clasificador | scikit-learn SVC (kernel RBF, class_weight=balanced) |
| VAD | Silero VAD (PyTorch · sounddevice, sin FFmpeg) |
| Augmentación | audiomentations (Gaussian Noise + Gain) |
| Cloud DB | Supabase (PostgreSQL + Storage) |
| Agente IA | ElevenLabs Conversational AI (widget `<elevenlabs-convai>`) |
| Frontend | Vanilla JS · HTML5 · CSS3 (sin frameworks) |

---

## 📄 Licencia y Términos de Uso

Copyright © 2026 Jairo Pirona. Todos los derechos reservados.

Este proyecto es de **código visible pero no es de código abierto**. Su publicación tiene fines exclusivamente académicos (TFM). El uso está sujeto a las siguientes condiciones:

### 🟢 Permitido
- Visualizar el código en GitHub
- Estudiarlo con fines académicos
- Descargar y ejecutar el código localmente para pruebas personales o evaluación

### 🛑 Expresamente prohibido sin autorización escrita previa del autor
- Uso comercial directo o indirecto
- Distribución pública de copias (solo se puede descargar desde tu enlace oficial)
- Despliegue en servidores o entornos de producción de terceros o la nube
- Modificación e integración en otros proyectos

Para solicitar licencias comerciales o usos especiales:
📧 **info@jairopirona.cloud**

---

> ⚠️ **Componentes de terceros:** Este repositorio integra modelos con licencias independientes:
> **TitaNet-Large** (Apache 2.0 — NVIDIA/NeMo) y **DF_Arena 500M/1B** (Apache 2.0 — Speech-Arena-2025).
> La licencia propietaria arriba indicada aplica **exclusivamente** al código original desarrollado en este proyecto.

---

*Modelos DF_Arena: Kulkarni et al. (2025), Speech DF Arena — https://huggingface.co/Speech-Arena-2025*
