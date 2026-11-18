# Argo SBD Decoder — Notas para presentación

Documento técnico de referencia para preparar una presentación de la aplicación.
Escrito a partir del código fuente.

---

## 1. ¿Qué es y para qué sirve?

La aplicación descarga, decodifica y visualiza los datos que transmiten los
perfiladores Argo por satélite Iridium. Cada vez que un perfilador sale a
superficie, envía un fichero binario pequeño (SBD — Short Burst Data) por
correo electrónico al operador. Esos ficheros contienen posición GPS, perfil
CTD de temperatura y salinidad, y datos de estado del instrumento.

El caso de uso principal es la **recuperación de floats**: cuando un
perfilador ha terminado su misión y hay que ir a buscarlo con un barco, el
operador necesita saber en tiempo real dónde está, hacia dónde deriva, y a qué
velocidad. La app hace exactamente eso.

Fue desarrollada por **SOCIB e IEO-CSIC** en el marco del proyecto
**Euro-Argo ONE** (Horizonte Europa, Acuerdo de Subvención n.º 101188133).
La lógica de decodificación es una reescritura en Python de la cadena de
procesamiento MATLAB de Coriolis (Rannou et al., 2025; DOI: 10.17882/45589).

---

## 2. Cobertura: floats soportados

87 tipos de float en total, todos los fabricantes principales:

| Familia | IDs Coriolis | Tipo de frame |
|---|---|---|
| NKE ARVOR / PROVOR | 201–232 | Binario fijo 100 bytes |
| NKE SBD2 bio-óptico | 301–303 | Binario fijo 140 bytes |
| NKE ARVOR PFV2 | 401–402 | Fragmentos reensamblados |
| APEX APF9 (Teledyne) | 1001–1016, 1314 | Texto .msg/.log |
| APEX APF11 (Teledyne) | 1101–1132, 1321–1323 | Binario + texto |
| NOVA / DOVA (NVS) | 2001–2003 | Longitud variable |

Los IDs Coriolis identifican no solo el fabricante sino también la versión de
firmware, porque la codificación de los bits puede cambiar entre versiones.
Por ejemplo, el ARVOR-C con firmware 5603A12 (ID 233) usa un offset de +25000
en la codificación de salinidad, diferente al resto de la familia ARVOR-C
(IDs 219, 220).

---

## 3. La interfaz gráfica (GUI)

Un solo ventana tkinter sin pestañas. Panel izquierdo de controles
(desplazable) + panel derecho de previsualización de plots + panel de log
abajo.

### Sección 1 — Configuración del float
- **Float type**: desplegable con más de 50 nombres de modelos NKE. Al
  seleccionar uno, la app lee `config/nke_to_decoder_id.json` y asigna
  automáticamente el ID Coriolis correcto.
- **IMEI**: número de módem Iridium (15 dígitos).
- **WMO**: opcional.
- **Carpeta de trabajo**: donde se guardarán los SBDs, los CSVs decodificados
  y los productos.
- **GEBCO bathy**: fichero NetCDF opcional con batimetría del fondo marino
  (se superpone en los mapas y secciones).

### Sección 2 — Descarga de SBDs
- **Proveedor de email**: Gmail, Outlook, Yahoo, iCloud, o Personalizado. Al
  seleccionar uno, se rellenan automáticamente el servidor IMAP, el puerto y
  la carpeta.
- **Dirección y contraseña de aplicación**: la contraseña de aplicación (App
  Password) es específica de esta app, no es la contraseña normal de la
  cuenta. La app tiene un botón "?" con instrucciones para cada proveedor.
- **Rango de fechas** con selector de calendario.
- **Dirección del remitente**: por defecto `sbdservice@sbd.iridium.com`, que
  es el servicio de Iridium.
- Botón **Download SBDs**.

### Sección 3 — Decodificación y productos
- **Install libraries**: ejecuta `pip install -r requirements.txt`.
- **Decode SBDs**: decodifica todos los `.sbd` de la carpeta `sbd_raw/`.
- **TS + Sections**: genera diagrama T-S y secciones verticales.
- **Map**: genera el mapa de trayectoria.
- **Forecast Position**: genera la predicción de posición y el KMZ.
- **All products**: ejecuta los cuatro anteriores en secuencia.
- **Open output folder**: abre la carpeta `products/` en el explorador.

El panel de previsualización muestra cualquier PNG generado. El desplegable
lista todos los ficheros PNG de la carpeta de productos, ordenados por fecha
de modificación.

---

## 4. El pipeline de procesamiento

```
Correo electrónico (Iridium SBD service)
        │
        ▼
download_sbd_imap.py  ──► sbd_raw/*.sbd
        │
        ▼
decode_sbd_batch.py
  └── sbd_reader.py ─── lee frames binarios
  └── registry.py ──── selecciona decoder correcto
  └── <decoder>.py ─── extrae GPS, CTD, técnicos
  └── csv_writer.py ── escribe decoded/*.csv
        │
        ├──► generate_quicklook_products.py
        │         └── Diagrama T-S
        │         └── Secciones T y S
        │         └── Mapa de trayectoria
        │
        └──► generate_navigation_products.py
                  └── Mapa con predicción
                  └── KMZ para Google Earth
                  └── Plots de vacío interno, batería, velocidad
                  └── Forecast de recuperación en el log
```

---

## 5. Lectura de ficheros SBD

Fichero de entrada: `YYYYMMDD_MOMSN_IMEI_MTMSN.sbd`

La fecha y el IMEI se extraen del propio nombre del fichero. Cada fichero
contiene uno o más frames:

- **NKE estándar**: frames de exactamente 100 bytes. Byte 0 = tipo de paquete.
  Bytes 1–99 = payload de 99 bytes. Los frames de relleno (todos `0x00` o
  `0x1A`) se descartan.
- **NKE SBD2**: idéntico pero con frames de 140 bytes.
- **NOVA/DOVA**: longitud variable. Byte 0 = tipo, bytes 1–2 = longitud total
  (uint16 big-endian), bytes 3+ = payload.

---

## 6. Cómo funciona la decodificación bit a bit

La función `get_bits()` (`core/bit_utils.py`) extrae campos de bit de los
payloads binarios. Es una traducción directa de `get_bits.m` de Coriolis.
Recibe una lista de anchos de campo en bits y devuelve los valores como
enteros sin signo, con direccionamiento MSB-first en base 1 (igual que MATLAB).

Para los floats NKE, la distribución de bits está documentada en los ficheros
MATLAB de Coriolis. Por ejemplo, en el ARVOR-C (decoder ID 219/220/233), el
paquete técnico (tipo 0) tiene este layout:
```
6 × 16 bits   → datos de ciclo (presiones, etc.)
3 × 16 bits   → más datos
3 × 8 bits    → hora/minuto/segundo del float
10 × 8 bits   → campos internos (offset de presión, etc.)
8 × 8 bits    → GPS (grados, minutos, fracciones, orientación para lat y lon)
2 × 8 bits    → indicadores de estado
448 bits      → relleno
```

El GPS se codifica como grados enteros + minutos enteros + fracciones/10000 +
byte de orientación (0=N/E, 1=S/W).

---

## 7. Conversiones de sensores CTD

Todos los valores raw son enteros de 16 bits que hay que convertir a unidades
físicas. Las fórmulas dependen del grupo de decoder:

**Presión** (3 grupos):
- Grupo A (floats profundos): `P = (complemento2(raw, 16) + 30000) / 10` dbar
- Grupo B (estándar, más común): `P = (complemento2(raw, 16) + 10000) / 10` dbar
- Grupo C (firmware antiguo): `P = complemento2(raw, 16) / 10` dbar

**Temperatura** (universal para todos los NKE):
`T = complemento2(raw, 16) / 1000` °C

**Salinidad** (3 grupos):
- Grupo A: `S = (raw + 10000) / 1000` PSU
- Grupo B (más común): `S = raw / 1000` PSU
- Grupo C (ARVOR-C 5603A12, ID 233): `S = (raw + 25000) / 1000` PSU

El ID 233 tiene el offset de salinidad específico porque es un firmware más
nuevo que codifica con mayor resolución en el rango oceánico (25–42 PSU).

---

## 8. Tipos de paquetes NKE

Cada frame comienza con un byte que indica el tipo de dato:

| Byte | Tipo | Contenido |
|---|---|---|
| 0 | Tech 1 | GPS, hora del float, presiones, batería, metadatos de ciclo |
| 1 | CTD descenso | Perfil CTD en descenso |
| 2 | CTD deriva | Medidas CTD en deriva |
| 3 | CTD ascenso | Perfil CTD en ascenso |
| 4 | Tech 2 | Datos técnicos secundarios |
| 5 | Parámetros 1 | Configuración del float |
| 6 | Hidráulica | Acciones de EV/bomba (cuándo, presión, duración) |
| 7 | Parámetros 2 | Calibración |
| 13 | CTD descenso ext. | Versión extendida (algunos IDs) |
| 14 | CTD ascenso ext. | Versión extendida (algunos IDs) |

El ciclo se abre con cada paquete tipo 0. Los paquetes CTD siguientes se
asignan al ciclo más reciente. Si `deep_cycle = false` (el float no profundizó),
los datos CTD se descartan.

---

## 9. CSVs de salida (decoded/)

| Fichero | Contenido | Columnas clave |
|---|---|---|
| `Technical Message.csv` | Una fila por fix GPS por ciclo | Cycle, Date, Lat, Lon, GPS valid, Internal pressure, Has profile |
| `Ascent profile CTD Message.csv` | Una fila por medida CTD en ascenso | Cycle, Date, Pressure (dbar), Temperature (°C), Salinity (PSU) |
| `Descent profile CTD Message.csv` | Ídem en descenso (si existe) | — |
| `Drift measurements.csv` | Ídem en deriva (si existe) | — |
| `Hydraulic actions.csv` | Acciones de bomba/electroválvula | Cycle, Action type, Pressure, Duration |

---

## 10. Productos visuales

### Diagrama T-S (`products/TS_IMEI_*.png`)
- Temperatura vs. Salinidad para todos los perfiles.
- Cada perfil coloreado por orden cronológico (colormap turbo).
- Isolíneas de densidad potencial (σ₀) calculadas con `gsw` (Gibbs SeaWater).

### Secciones verticales (`products/section_*.png`)
- Panel superior: temperatura (colormap RdYlBu_r) con contornos de 1°C y σ₀.
- Panel inferior: salinidad (colormap viridis) con contornos de 0.5 PSU.
- Eje X: índice de perfil con etiquetas de fecha.
- Si hay batimetría GEBCO: fondo marino en gris sobre cada panel.
- Se generan dos versiones: profundidad completa y 0–300 dbar.

### Mapa de trayectoria (`products/maps/trajectory_map_*.png`)
- Posiciones GPS coloreadas por orden (viridis).
- Perfiles con CTD: círculos rojos abiertos.
- Fixes solo GPS (sin perfil CTD): puntos grises.
- Primer punto: estrella amarilla. Último punto: círculo rojo.
- Si hay GEBCO: batimetría como fondo azul con contornos de profundidad.

### KMZ para Google Earth (`products/kmz/*.kmz`)
Cinco capas:
1. Track de todas las posiciones (línea cian)
2. Posiciones con perfil (círculos verdes, etiquetados P1, P2…)
3. Posiciones solo GPS (círculos azules, etiquetados G1, G2…)
4. Posiciones predichas (chinchetas rojas, etiquetadas Pred N)
5. Vectores de predicción (líneas rojas desde la posición real hacia la predicción)

---

## 11. Módulo de navegación y predicción de posición

### Algoritmo de predicción
Implementación en Python del algoritmo original de Gene Massion (MBARI),
diseñado para la recuperación de floats costeros.

Para cada par consecutivo de fixes GPS:
1. Se proyectan las posiciones a UTM (pyproj, zona seleccionada automáticamente).
2. Se calcula el vector de desplazamiento: ΔEast, ΔNorth.
3. Se extrapola ese mismo vector desde el fix más reciente para obtener la
   posición predicha.
4. Se calcula rumbo (true north) y velocidad de deriva.

La predicción es más precisa en modo EOL (End-of-Life), cuando el float está
en superficie transmitiendo cada ~2 minutos. En modo de perfilado (un fix
cada ~24 h), el error puede ser mayor porque no se captura el desplazamiento
subsuperficial.

Validación publicada con datos reales (ARVOR-C, Mediterráneo occidental):
- Modo perfilado: error mediano 249 m, máximo 829 m
- Modo EOL: error mediano 24 m, máximo 66 m

### Outputs de navegación
- `navigation_summary.csv`: posición, rumbo, velocidad, predicción para cada fix.
- `navigation_profiles_only.csv`: ídem filtrado solo a ciclos con perfil CTD.
- Plots de serie temporal: vacío interno (mbar), batería (V), velocidad (m/h),
  rumbo (°), distancia al fix anterior (m).
- Mapa de zoom sobre los últimos 10 puntos con el vector de predicción.

### Forecast de recuperación en el log
Cuando el script de navegación termina, imprime en el log de la GUI (en verde):
```
==================================================
  RECOVERY FORECAST - GO TO:
  >>> 38.884665, 3.570367 <<<
==================================================
  Drift speed: 2400 m/h
  Heading: 047 deg
  Last known fix (2026-11-09 07:45 UTC):
  38.877432, 3.562105
==================================================
```
Estas coordenadas son las que se pasan directamente al patrón del barco.

---

## 12. Descarga de SBDs por IMAP

La contraseña **nunca se almacena** en el fichero de configuración. El GUI la
pasa como variable de entorno `IMAP_APP_PASSWORD` al subproceso justo antes
de lanzarlo.

El sistema ejecuta cuatro búsquedas IMAP diagnósticas de menor a mayor
especificidad para detectar problemas de conectividad o de configuración de la
carpeta. Si la búsqueda más restrictiva no devuelve resultados, se prueba con
criterios más amplios automáticamente.

Los SBDs se guardan en `sbd_raw/` (modo plano, por defecto) para que la
decodificación procese toda la secuencia de transmisión de un ciclo en un
único pasada, incluso si la transmisión cruzó la medianoche.

---

## 13. Distribución y empaquetado

La app se empaqueta con **PyInstaller** (`build_exe.py`) en modo `--onedir`
(carpeta con el ejecutable y todas las dependencias). El GUI detecta si está
corriendo en modo empaquetado (`sys.frozen = True`) y ajusta todas las rutas.
Los logs y la configuración del usuario se guardan en `~/.argo_sbd_decoder/`
para no mezclarlos con los ficheros de la app.

Plataformas soportadas:
- **Windows**: `ArgoSBDDecoder.exe`, distribuido como ZIP.
- **macOS**: `ArgoSBDDecoder.app`, distribuido como DMG.
- **Linux**: binario, distribuido como ZIP.

Requisitos mínimos: Python 3.8+, 3.10+ recomendado. Sin software propietario.

---

## Ideas para la presentación

### Estructura sugerida (10–15 diapositivas)

1. **¿Qué problema resuelve?**
   - Float en el mar → hay que recuperarlo → ¿dónde está?
   - Sin software propietario, sin NKE Decoder, sin MATLAB.

2. **Contexto Argo**
   - Ciclo de un perfilador (descenso, deriva, ascenso, transmisión).
   - Qué información llega por Iridium (GPS, CTD, estado).

3. **Cobertura: 87 tipos de float**
   - Tabla de familias (NKE, APEX, NOVA).
   - Destacar que cada versión de firmware tiene su propio esquema de bits.

4. **El pipeline en una imagen**
   - Diagrama de flujo: email → SBD → decodificación → CSV → plots.
   - Mostrar la estructura de carpetas de salida.

5. **La GUI**
   - Captura de pantalla de la interfaz.
   - Tres secciones: configuración, descarga, decode & productos.
   - Preview de plots en tiempo real.

6. **Cómo se decodifica un SBD**
   - Frame binario de 100 bytes.
   - `get_bits()` extrae campos bit a bit.
   - Conversiones T, S, P específicas por firmware.
   - Resultado: una medida CTD en 3 líneas de código.

7. **Productos visuales**
   - Mostrar el diagrama T-S con densidades.
   - Mostrar una sección vertical.
   - Mostrar el mapa de trayectoria.

8. **El módulo de predicción de posición**
   - Esquema del algoritmo de extrapolación del vector de desplazamiento.
   - El KMZ abierto en Google Earth.
   - El forecast de recuperación en la consola (coordenadas en verde).

9. **Validación de la predicción**
   - Tabla con errores por modo (perfilado vs. EOL).
   - El float ARVOR-C recuperado en el Mediterráneo occidental en 35 minutos.

10. **Arquitectura técnica**
    - Python puro, sin software propietario.
    - Dependencias: numpy, pandas, matplotlib, gsw, pyproj.
    - Empaquetado con PyInstaller para Windows/macOS/Linux.

11. **Código abierto y reproducibilidad**
    - Basado en el decoder MATLAB de Coriolis (DOI: 10.17882/45589).
    - Python permite auditar cada conversión de sensor.
    - Fácil de extender a nuevos tipos de float.

12. **Trabajo futuro / pendiente**
    - Validar decoders APEX con datos reales propios.
    - Integrar el análisis de comportamiento del ciclo (timing, velocidades).
    - Interfaz web (Flask/Dash) para uso remoto desde el barco.

---

## Mensajes clave para llevar a casa

- **Sin software propietario**: funciona con Python estándar, sin licencias.
- **Tiempo real para recuperación**: del correo de Iridium a las coordenadas
  del barco en menos de un minuto.
- **87 tipos de float**: cubre prácticamente toda la flota Argo europea.
- **Todo local**: los datos nunca salen del ordenador del operador (salvo la
  descarga del email, que ya es del operador).
- **Validado en el mar**: probado durante la recuperación real de un ARVOR-C
  en el Mediterráneo occidental.
