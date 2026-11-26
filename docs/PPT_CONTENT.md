# Argo SBD Decoder — Contenido para presentación

---

## SLIDE 1 — Portada

**Título:** Argo SBD Decoder
**Subtítulo:** Herramienta open-source para la descarga, decodificación y visualización de datos de perfiladores Argo en tiempo real

Desarrollado por **SOCIB & IEO-CSIC**
Euro-Argo ONE project · Grant Agreement No. 101188133

---

## SLIDE 2 — ¿Qué es Argo?

- Red global de ~4.000 perfiladores autónomos distribuidos por todos los océanos
- Lanzado en diciembre de 1999 · 25 años de operación continua
- Más de 50 países participantes · ~25 despliegan perfiladores
- Miden temperatura y salinidad desde superficie hasta 2.000 m (Core-Argo) o 6.000 m (Deep-Argo)
- Cada float sigue un **ciclo de ~10 días**: desciende a 1.000 m (deriva), baja a 2.000 m, asciende y transmite datos vía satélite
- Generan ~100.000 perfiles T/S al año
- Han acumulado **más datos subsuperficiales que todos los demás sistemas de observación combinados** ([NOAA, 2024](https://globalocean.noaa.gov/celebrating-25-years-of-argo-the-crown-jewel-of-ocean-observing-systems/))

> *"The crown jewel of ocean observing systems"* — NOAA, 2024

---

## SLIDE 3 — Argo en Europa: Euro-Argo ONE

- **Euro-Argo ERIC**: infraestructura de investigación europea para la contribución al programa Argo
- En 2024, Europa desplegó **186 perfiladores** ([Argo AST26 Report, 2025](https://argo.ucsd.edu/wp-content/uploads/sites/361/2025/04/Europe_NatRep_AST26_VF.pdf))
- **OneArgo** (desde 2020): expansión a profundidad completa y parámetros biogeoquímicos (BGC-Argo)
- **Euro-Argo ONE** (2023–2026): proyecto H2020 que integra floats costeros y de recuperación

**Floats costeros (ARVOR-C):**
- Ciclos de ~24 h en lugar de 10 días
- Operan en aguas poco profundas (50–300 m)
- Transmiten por Iridium SBD tras cada ciclo
- Diseñados para ser **recuperados y reutilizados** → reduce costes y huella ambiental

---

## SLIDE 4 — El problema: recuperación de floats

Cuando un perfilador termina su misión operativa, hay que **ir a buscarlo con un barco**.

**El reto:**
- El float transmite su posición GPS cada ciclo (~24 h para ARVOR-C)
- Entre el último fix y el momento de recuperación, el float ha derivado
- Sin predicción de posición, el tiempo de búsqueda puede superar horas → coste elevado

**El impacto económico de la recuperación:**
- Reduce el coste por perfil adicional en ~57% para floats Core y ~77% para BGC ([Copernicus, 2026](https://egusphere.copernicus.org/preprints/2026/egusphere-2026-5975/))
- Un float ARVOR-C recuperado y redesplegado puede generar años adicionales de datos

**Lo que necesita el operador del barco:**
- Coordenadas de predicción de posición
- Velocidad y rumbo de deriva
- Tiempo desde el último fix GPS

---

## SLIDE 5 — La solución: Argo SBD Decoder

Herramienta Python open-source que, en menos de un minuto, convierte un correo de Iridium en coordenadas de recuperación.

**Pipeline completo:**
```
Email Iridium  →  .sbd  →  Decodificación  →  CSV  →  Plots + KMZ + Forecast
```

**Funcionalidades principales:**
1. Descarga automática de SBDs desde cualquier cuenta de email (Gmail, GMX, Outlook, Yahoo, iCloud)
2. Decodificación de 87 tipos de float (NKE, APEX, NOVA)
3. Generación de productos oceanográficos (T-S, secciones, mapa)
4. **Predicción de posición para recuperación**
5. KMZ para Google Earth

**Desarrollado por:** SOCIB e IEO-CSIC
**Basado en:** Cadena de procesamiento MATLAB de Coriolis ([DOI: 10.17882/45589](https://doi.org/10.17882/45589))
**Código abierto:** https://github.com/ldiaz-barroso/argo-sbd-decoder

---

## SLIDE 6 — La interfaz

Captura de pantalla de la GUI + descripción de las 3 secciones:

**1. Configuración del float**
- Selección del tipo de float por nombre de modelo (>50 tipos NKE)
- IMEI, WMO, carpeta de trabajo, batimetría GEBCO

**2. Descarga de SBDs**
- Proveedor de email con autoconfiguración IMAP
- Rango de fechas con selector de calendario
- Búsqueda automática en todas las carpetas del buzón

**3. Decode & productos**
- Botones: Decode SBDs · TS + Sections · Map · Forecast Position · All products
- Preview de plots en tiempo real
- Log con resaltado en verde del forecast de recuperación

---

## SLIDE 7 — Cómo funciona la decodificación

**El mensaje SBD:**
- Fichero binario de 100 bytes por frame
- Nombre: `YYYYMMDD_MOMSN_IMEI_MTMSN.sbd`
- Llega como adjunto de email desde `sbdservice@sbd.iridium.com`

**Extracción bit a bit:**
- Función `get_bits()`: traducción directa de `get_bits.m` de Coriolis
- Cada campo tiene un número de bits y posición exactos, definidos por versión de firmware
- Por eso hay 87 IDs de decoder diferentes

**Conversiones CTD (NKE):**
| Variable | Fórmula |
|---|---|
| Presión | `(complemento2(raw,16) + offset) / 10` dbar |
| Temperatura | `complemento2(raw,16) / 1000` °C |
| Salinidad | `raw / 1000` PSU (o con offset según firmware) |

---

## SLIDE 8 — Productos oceanográficos

### Diagrama T-S
- Temperatura vs. Salinidad por perfil, coloreado cronológicamente
- Isolíneas de densidad potencial (σ₀) con la librería `gsw`

### Secciones verticales
- Temperatura y salinidad en profundidad a lo largo del tiempo
- Batimetría GEBCO opcional
- Versión full-depth y 0–300 dbar

### Mapa de trayectoria
- Posiciones GPS coloreadas por orden temporal
- Distinción entre fixes con perfil CTD y fixes solo GPS
- Batimetría GEBCO como fondo

---

## SLIDE 9 — Predicción de posición para recuperación

**Algoritmo:** Gene Massion (MBARI) — extrapolación del vector de desplazamiento

Para cada par de fixes consecutivos i-1 → i:
1. Se proyectan a UTM (pyproj)
2. Se calcula ΔEast, ΔNorth, rumbo y velocidad
3. Se extrapola ese mismo vector desde i → posición predicha i+1

**KMZ para Google Earth:**
- Track de trayectoria (línea cian)
- Posiciones con perfil CTD (círculos verdes, P1, P2…)
- Posiciones predichas (chinchetas rojas, Pred N)
- Vectores de predicción (líneas rojas)

**Forecast en el log de la GUI** (en verde):
```
RECOVERY FORECAST - GO TO:
>>> 38.884665, 3.570367 <<<
Drift speed: 2400 m/h  |  Heading: 047°
Last known fix (2026-11-09 07:45 UTC): 38.877432, 3.562105
```

---

## SLIDE 10 — Análisis de comportamiento del ciclo

Script independiente (`float_behavior.py`) para analizar el comportamiento del float a partir del Technical Message del software NKE:

**Métricas calculadas por ciclo:**
- Duración de ascenso, superficie y descenso (min)
- Velocidad de ascenso y descenso calculada (m/s)
- Presión de grounding (cBar)
- Tiempo en superficie antes de transmitir

**Output:**
- `cycle_summary.csv` con unidades explícitas
- Gráfico Gantt con fases coloreadas (verde=ascenso, azul=superficie, naranja=descenso), velocidades sobre cada barra y presión de grounding

*(Mostrar figura del timeline de ciclos del ARVOR-C)*

---

## SLIDE 11 — Validación

**Float:** ARVOR-C 300534067321210 · Mediterráneo occidental · Nov 2026

| Métrica | Valor medio |
|---|---|
| Duración ascenso | 16.4 ± 1.8 min |
| Duración superficie | 15.2 ± 3.7 min |
| Duración descenso | 6.7 ± 0.7 min |
| Velocidad ascenso | 0.099 m/s |
| Velocidad descenso | 0.241 m/s |
| Presión grounding | ~95 cBar |

Comportamiento muy consistente entre ciclos → float operando correctamente.

---

## SLIDE 12 — Plataformas y distribución

**Multiplataforma:** Windows · macOS · Linux

Empaquetado con **PyInstaller** → ejecutable standalone, sin necesidad de instalar Python.

**Dependencias open-source:**
`numpy` · `pandas` · `matplotlib` · `gsw` · `pyproj` · `xarray` · `netCDF4`

**Repositorio público:**
👉 https://github.com/ldiaz-barroso/argo-sbd-decoder

**Financiación:**
Euro-Argo ONE project · Horizonte Europa · Grant Agreement No. 101188133

---

## SLIDE 13 — Conclusiones

✅ Herramienta open-source completa para operaciones con perfiladores Argo costeros
✅ Cubre el ciclo completo: descarga → decodificación → visualización → predicción
✅ 87 tipos de float soportados (NKE, APEX, NOVA)
✅ Sin software propietario ni licencias
✅ Del email de Iridium a las coordenadas de recuperación en < 1 minuto
✅ Validado en recuperación real de ARVOR-C en el Mediterráneo occidental

**Trabajo futuro:**
- Validación con datos APEX propios
- Interfaz web para uso remoto desde el barco
- Integración del análisis de comportamiento del ciclo

---

## Referencias

- Argo Program: https://argo.ucsd.edu
- NOAA Argo: https://globalocean.noaa.gov/research/argo-program/
- Euro-Argo ERIC: https://www.euro-argo.eu
- Coriolis decoder (DOI: 10.17882/45589): https://doi.org/10.17882/45589
- Argo float recovery analysis: https://egusphere.copernicus.org/preprints/2026/egusphere-2026-5975/
- Argo 25 años (NOAA): https://www.noaa.gov/news/argo-crown-jewel-of-ocean-observing-systems-turns-25
- Repositorio app: https://github.com/ldiaz-barroso/argo-sbd-decoder
