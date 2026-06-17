# Guía de Configuración — Agente Sienna (ElevenLabs)

> Guía paso a paso para crear y configurar el agente conversacional **Sienna** en ElevenLabs Conversational AI, tal como está operativo en este proyecto (Agent ID de referencia: `agent_3501kbab1rg1fdm9sbwta900cjmf`).

---

## Costes y Plan Recomendado

ElevenLabs Conversational AI **no es gratuito** para uso en producción. Antes de empezar:

| Plan | Minutos incluidos | Precio aprox. | Adecuado para |
|---|---|---|---|
| Free | ~10 min/mes | 0 € | Pruebas muy básicas |
| Starter | ~100 min/mes | ~5 €/mes | Desarrollo y demos |
| Creator | ~500 min/mes | ~22 €/mes | TFM y pruebas extensas |
| Pro+ | Escalable | Variable | Producción real |

> El agente de referencia acumuló **125 llamadas con 6.60s de latencia media**. Cada verificación biométrica (pruebas) consume entre 15 y 45 segundos de conversación. Planifica el plan según tu volumen de pruebas.

---

## Paso 1 — Crear la cuenta y acceder a ElevenAgents

1. Entra en `https://elevenlabs.io` y crea una cuenta o inicia sesión.
2. En el menú lateral, ve a **Agentes → Nuevo agente**.
3. Aparecerá un selector de tipo: elige **Agente personal**.
4. A continuación te pedirá el **caso de uso**: selecciona **Asistente personal**.
5. Asigna un nombre al agente (por ejemplo: `Sienna`).
6. Te pedirá un campo **Objetivo Principal** — es obligatorio pero no crítico aquí. Escribe cualquier texto breve (ej: `Agente de atención al cliente con verificación biométrica`) y continúa. El objetivo real y detallado se configurará en el **Paso 3**.
7. Haz clic en **Crear agente**.

---

## Paso 2 — Pestaña "Agent" — Configuración principal

### 2.1 Mensaje del sistema (System Prompt)

En el campo **System Prompt**, pega el texto dividido en 5 bloques semánticos.

**Bloque Personality:**
```
Eres Sienna, una agente experta de soporte y seguridad para una operadora de telecomunicaciones (Orange).
Eres amable, eficiente, pero extremadamente rigurosa con la seguridad de la cuenta.
```

**Bloque Environment:**
```
Estás atendiendo llamadas de voz en tiempo real. Tienes acceso a una herramienta externa llamada
check_voice_identity_client que verifica la identidad del usuario analizando su voz.
```

**Bloque Goal:**
```
Tu objetivo principal es resolver las dudas del cliente, PERO tu prioridad absoluta es proteger
la cuenta contra el fraude (SIM Swapping).
```

**Security Protocol (CRÍTICO)**
```
Primero pregunta siempre por el motivo de la llamada.
Después, pregunta explícitamente si el usuario ya es cliente de Orange.

Si el usuario dice que NO es cliente:
  -No uses la herramienta check_voice_identity_client.
  -Atiende la consulta con información general (tarifas, alta de línea, etc.).
  -No pidas DNI ni datos sensibles.

Si el usuario dice que SÍ es cliente:
  -PASO 1: Pídele ÚNICAMENTE su DNI: "Para acceder a tu información, ¿me indicas tu DNI por favor?"
  -REGLA CRÍTICA SOBRE EL DNI: Acepta INMEDIATAMENTE cualquier número que el usuario dicte como DNI. No lo juzgues ni pidas que lo complete.
  -PASO 2: Escucha su respuesta y memoriza el número de DNI.
  -PASO 3: En cuanto te dé el número, di: "Gracias. Para confirmar tu identidad, dime tu nombre completo."
  -PASO 4: EJECUTA LA HERRAMIENTA check_voice_identity_client pasando el DNI en dni_reclamado. La herramienta debe dispararse justo después de esa frase, para capturar la voz del usuario cuando responda con su nombre.

[REGLAS DE INTERPRETACIÓN DEL RESULTADO - CÚMPLELAS ESTRICTAMENTE]:
El tool devuelve un JSON con: estado, mensaje, score.
GENUINO: Identidad verificada. Continúa con la operación.
SUPLANTACION o FRAUDE: Inconsistencia de seguridad. Hay un máximo de 2 intentos totales, sumando también AUDIO_INSUFICIENTE y DESCONOCIDO. Si aún queda un intento, pide repetir el DNI, luego pide repetir el nombre completo y ejecuta de nuevo la herramienta inmediatamente. Si ya era el segundo fallo, deniega la operación y deriva a tienda física.
DEEPFAKE: Deniega el acceso inmediatamente y termina la llamada enviándolo a una tienda física.
AUDIO_INSUFICIENTE o DESCONOCIDO: Explica que no se escuchó bien. Cuenta como fallo dentro del mismo máximo de 2 intentos totales. Si aún queda un intento, pide repetir DNI, luego pide repetir el nombre claramente y vuelve a ejecutar la herramienta inmediatamente. Si ya era el segundo fallo, deniega la operación y deriva a tienda física.
```

**Guardrails**
```
Nunca des datos personales si el estado no es GENUINO.
Nunca menciones "huella de voz", "biometría", "score" ni "anti-spoofing".
Si el tool devuelve error, trata el caso como no verificado.
Habla siempre en español con acento colombiano de Medellín y profesional.
Sé breve. Recuerda que es una llamada de voz, no leas párrafos largos.
```

### 2.2 Primer mensaje

| Campo | Valor |
|---|---|
| Texto | `Hola, gracias por llamar a Orange. Te habla Sienna. Cuéntame brevemente el motivo de tu llamada.` |
| Interrumpible | ✅ Activado |

### 2.3 Configuración de Voz

| Campo | Valor |
|---|---|
| Voz principal | Marcela - Colombian Girl *(ver nota abajo)* |
| Familia TTS | Multilingüe |
| Estabilidad | 0.5 |
| Velocidad | 1.0 (rango recomendado 0.7–1.2) |
| Similitud | 0.8 |
| Modo Expresivo | Disponible — **NO activar** |

> 💡 **La voz es completamente libre.** `Marcela - Colombian Girl` es la voz usada en el agente de referencia de este TFM, pero puedes elegir cualquier voz de la biblioteca de ElevenLabs. Lo importante es mantener la configuración de Estabilidad, Velocidad y Similitud indicada arriba para un comportamiento predecible.

### 2.4 Idioma

| Campo | Valor |
|---|---|
| Idioma predeterminado | 🇪🇸 Español |
| Idiomas adicionales | Ninguno |

### 2.5 LLM

| Campo | Valor |
|---|---|
| Modelo | Gemini 2.5 Flash |
> 💡 **Modelo.** `Gemini 2.5 Flash` es el modelo usado en este TFM, sin embargo, puedes escoger de la lista LLM el modelo que desees. Los precios y latencia pueden variar.

### 2.6 Comportamiento del agente

| Campo | Valor |
|---|---|
| Comportamiento | Comportamiento predeterminado |

---

## Paso 3 — Pestaña "Workflow" — Nodo Agente

En la pestaña **Workflow**, verás la topología: **Iniciar → Agente**.

Haz clic en el **Nodo Agente** y en el campo **Conversation objective** pega el siguiente texto completo:
Eres Sienna, agente de atención telefónica de Orange.

MISIÓN:
Atender al cliente protegiendo su cuenta. La verificación de seguridad es OBLIGATORIA pero debe parecer un trámite estándar.

FLUJO DE ATENCIÓN:

1. ACOGIDA:
- "Bienvenido a Orange, soy Sienna. ¿En qué puedo ayudarte el día de hoy?"
- Si quiere hacer una gestión (facturas, avería, tarifas...), pregúntale: "¿Eres el titular de la línea?"

2. SOLICITUD DE DATOS (La Verificación Oculta):
- Si es titular, dile: "Perfecto. Para acceder a tu información, indícame tu DNI por favor."
- EN CUANTO EL USUARIO RESPONDA con su DNI:
  - Memoriza el DNI.
  - Di exactamente: "Gracias. Para confirmar tu identidad, indícame tu nombre completo."
  - EJECUTA INMEDIATAMENTE la herramienta `check_voice_identity_client` pasando ese DNI en `dni_reclamado`.
  - La herramienta debe dispararse justo después de esa frase, para que el backend active la captura y validación mientras el usuario responde con su nombre.

3. RESPUESTA AL CLIENTE SEGÚN EL RESULTADO DEL TOOL:

- SI ES GENUINO:
  - "Perfecto, todo en orden. Veo tu cuenta aquí... Dime, ¿qué necesitas exactamente?"
  - Continúa con normalidad.

- SI ES FRAUDE o SUPLANTACIÓN:
  - IMPORTANTE: solo hay 2 intentos totales de verificación.
  
  - Intento 1 fallido:
    - "Perdona, pero el sistema no me valida esos datos. ¿Me repites tu DNI de nuevo por favor?"
    - ESPERA a que el usuario diga el DNI.
    - Luego di exactamente: "Gracias. Ahora, ¿me repites tu nombre completo de nuevo por favor?"
    - EJECUTA INMEDIATAMENTE la herramienta `check_voice_identity_client` pasando el DNI recién dicho en `dni_reclamado`.

  - Intento 2 fallido:
    - "Lo siento, pero por motivos de seguridad, el sistema no ha podido validar el acceso remoto a esta cuenta. Necesitarás pasar por una tienda Orange con tu documentación. Perdona las molestias y gracias por llamar."
    - FINALIZA.
    - NO vuelvas a usar la herramienta.

- SI ES DEEPFAKE:
  - "Qué pena contigo, pero el sistema de seguridad acaba de detectar una anomalía crítica. Por seguridad no puedo continuar por este canal. Necesitarás acudir a una tienda Orange con tu documentación. Gracias por llamar."
  - FINALIZA LA LLAMADA INMEDIATAMENTE.
  - NO reintentes.
  - NO uses la herramienta otra vez.

- SI ES AUDIO_INSUFICIENTE o DESCONOCIDO:
  - Trátalo como un fallo de verificación dentro del mismo límite de 2 intentos totales.
  
  - Si es el primer fallo:
    - "Perdona, se te escucha un poco entrecortado. ¿Me repites tu DNI por favor?"
    - ESPERA a que el usuario diga el DNI.
    - Luego di exactamente: "Gracias. ¿Puedes repetirme tu nombre completo más claro por favor?"
    - EJECUTA INMEDIATAMENTE la herramienta `check_voice_identity_client` pasando el DNI en `dni_reclamado`.

  - Si vuelve a salir AUDIO_INSUFICIENTE, DESCONOCIDO, FRAUDE o SUPLANTACIÓN en el segundo intento:
    - "Lo siento, pero por motivos de seguridad, el sistema no ha podido validar el acceso remoto a esta cuenta. Necesitarás pasar por una tienda Orange con tu documentación. Gracias por llamar."
    - FINALIZA.
    - NO uses la herramienta otra vez.

REGLAS CRÍTICAS:
- NUNCA menciones "huella de voz", "biometría", "score" o "anti-spoofing".
- Para el cliente, simplemente estás "comprobando sus datos" o "validando el acceso".
- Acepta inmediatamente cualquier número que el usuario dicte como DNI.
- En cuanto el usuario diga el DNI, pide el nombre completo y ejecuta la herramienta SIN pedir permisos adicionales ni hacer preguntas extra.
- FRAUDE, SUPLANTACIÓN, AUDIO_INSUFICIENTE y DESCONOCIDO cuentan todos dentro del mismo máximo de 2 intentos totales.
- Si el estado no es GENUINO, no reveles datos personales ni continúes gestiones sensibles.
- Sé breve, natural y profesional. Habla siempre en español.


**Resto de parámetros del nodo agente** (dejar en valores por defecto):

| Campo | Valor |
|---|---|
| Voz | Marcela - Colombian Girl (por defecto) |
| LLM | Gemini 2.5 Flash (por defecto) |
| Comportamiento de entrada | Automático |
| Disposición | Usando por defecto |

---

## Paso 4 — Crear la herramienta `check_voice_identity_client`

Ve a **Tools → Add Tool → Client Tool** y configura exactamente así:

| Campo | Valor |
|---|---|
| **Nombre** | `check_voice_identity_client` |
| **Descripción** | `Llama al backend biométrico para verificar si la voz coincide con el DNI reclamado.` |
| **Esperar respuesta** | ✅ Activado |
| **Disable interruptions** | ✅ Activado |
| **Habla antes de la herramienta** | Automático |
| **Modo de ejecución** | Publicar discurso |
| **Sonido de llamada** | None |
| **Tiempo de espera de respuesta** | `45` segundos ⚠️ CRÍTICO |

### Parámetro de la herramienta

| Campo | Valor |
|---|---|
| **Identificador** | `dni_reclamado` |
| **Tipo de datos** | `string` |
| **Requerido** | ✅ Sí |
| **Tipo de valor** | LLM Prompt |
| **Descripción** | `DNI del usuario que se quiere verificar (solo números, sin letras)` |
| **Enum Values** | Dejar vacío |

> 💡 **Sobre la Descripción del parámetro:** `"solo números, sin letras"` es la configuración del agente de referencia de este TFM pensada para que el LLM extraiga únicamente los dígitos del DNI español sin la letra final. **Puedes adaptarla** a tu caso — si necesitas el DNI completo con letra, cámbiala a `"DNI completo del usuario incluyendo la letra final"`. El LLM usará esta descripción para decidir qué formato extraer de la transcripción.

> ⚠️ **El tiempo de espera DEBE ser 45 segundos.** El backend necesita abrir el micrófono, esperar habla humana con Silero VAD, procesarla con TitaNet y DF_Arena y devolver el resultado. Con el valor por defecto de 1 segundo la herramienta siempre fallará con timeout.

---

## Paso 5 — Configuración Avanzada

Ve a **Settings → Advanced**.

### Reconocimiento automático de voz (ASR)

| Campo | Valor |
|---|---|
| Modelo ASR | ASR original |
| Filtrar el habla de fondo | ❌ OFF |
| Formato de audio de entrada | PCM 16000 Hz (Recomendado) |

### Comportamiento conversacional

| Campo | Valor |
|---|---|
| Modelo de turno | Turn V2 (Turn V3 es más recomendado si está disponible) |
| Tomar turno tras el silencio | 1 segundo |
| Terminar conversación tras el silencio | -1 (Desactivado) |
| Duración máxima de la conversación | 600 segundos (10 minutos) |

### Tiempos de espera LLM

| Campo | Valor |
|---|---|
| Tiempo de espera suave | -1 (Desactivado) |
| Tiempo de espera en cascada de LLM | 8 segundos |

### Privacidad

| Campo | Valor |
|---|---|
| Guardar audio de llamadas | ✅ ON |
| Período de retención de conversaciones | -1 (Ilimitado) |

---

## Paso 6 — Configuración de Seguridad

Ve a **Settings → Seguridad**.

| Campo | Valor |
|---|---|
| Activar autenticación | ❌ OFF |
| Hosts configurados (Allowlist) | Ninguno por defecto |

> ⚠️ Sin allowlist, el widget puede embeberse desde cualquier dominio. Para producción real añade aquí tu dominio (ej: `localhost:8000` para desarrollo local).

**Sobrescrituras** — dejar todo en ❌ OFF excepto:

| Campo | Estado |
|---|---|
| Solo texto | ✅ ON |
| Resto | ❌ OFF |

---

## Paso 7 — Widget: integrar en `index.html`

Una vez creado el agente, copia tu **Agent ID** desde la URL del agente
(formato: `agent_XXXX...`).

En `static/index.html`, localiza la etiqueta `<elevenlabs-convai>` y
asegúrate de que contiene tu Agent ID:

```html
<elevenlabs-convai agent-id="TU_AGENT_ID_AQUI"></elevenlabs-convai>
```

Asegúrate de que el SDK está cargado en el HTML:

```html
<script src="https://elevenlabs.io/convai-widget/index.js" async></script>
```

El Agent ID debe coincidir exactamente con `ELEVENLABS_AGENT_ID` en tu `.env`.

---

## Paso 8 — Cómo funciona el Client Tool con el backend
El `check_voice_identity_client` es una **herramienta de cliente** — ElevenLabs
**no llama al backend directamente**. El flujo es:

```
ElevenLabs LLM → dispara check_voice_identity_client(dni_reclamado)
     ↓
app.js intercepta el evento del widget elevenlabs-convai
     ↓
app.js llama a POST /verify con { dni_reclamado }
     ↓
main.py abre micrófono → Silero VAD → DF_Arena → TitaNet → SVM
     ↓
Devuelve { estado, score, mensaje } a app.js
     ↓
app.js devuelve el resultado al widget de ElevenLabs
     ↓
ElevenLabs LLM interpreta el resultado y responde al usuario
```

Este flujo ya está implementado en `static/app.js`. No requiere configuración
adicional en ElevenLabs más allá de los pasos anteriores.

---

## Paso 9 — Verificar que todo funciona

1. Arranca el backend: `uvicorn main:app --host 0.0.0.0 --port 8000`
2. Abre `http://localhost:8000` en el navegador
3. Inicia una llamada desde el widget
4. Cuando Sienna pida el DNI, dilo en voz alta
5. Cuando pida el nombre, el backend activa el micrófono automáticamente
6. En los logs verás: `[VAD] Habla detectada. Grabando...`
7. El resultado `GENUINO`, `FRAUDE` o `DEEPFAKE` aparecerá en el Dashboard

---

## Referencia rápida — IDs del agente de referencia

| Campo | Valor |
|---|---|
| Agent ID | `agent_3501kbab1rg1fdm9sbwta900cjmf` |
| Tool ID | `tool_5101khtc90cdfkjak7ky4d7t5xks` |
| Voz | Marcela - Colombian Girl |
| LLM | Gemini 2.5 Flash |
| Llamadas acumuladas | 125 · latencia media 6.60s |

> Estos IDs son del agente de referencia del TFM. Al crear el tuyo obtendrás IDs distintos — actualízalos en `.env` y en `static/index.html`.