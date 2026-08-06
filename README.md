# Radar SECOP — ITGEM

Monitoreo automático de contratación pública en SECOP, filtrado por los
criterios del instituto y con alertas por correo más un dashboard.

Corre solo cada 30 minutos en GitHub Actions. No hay servidor que mantener.

---

## Por qué está construido así

El buscador público de SECOP tiene tres problemas conocidos: procesos que se
publican y se retiran en minutos, tildes que hacen desaparecer resultados, y
ruido que dificulta encontrar lo relevante.

Los tres tienen la misma causa: **depender del buscador de ellos**. Por eso este
sistema no busca en SECOP — lo **espeja**.

1. **Descarga un tajo amplio** por geografía (Valle del Cauca), sin ningún filtro
   de texto del lado del servidor.
2. **Filtra localmente** sobre texto normalizado sin tildes ni mayúsculas, así
   que `capacitación`, `CAPACITACION` y `capacitacion` son el mismo token. El
   truco de la tilde deja de existir porque nunca pasamos por su índice.
3. **Archiva todo lo que ve** con `first_seen` / `last_seen`. Un proceso retirado
   del portal sigue documentado acá, y el retiro mismo se reporta como hallazgo.

El punto 3 es el que ningún otro sistema le va a dar: una alerta que dice
*"este proceso estuvo publicado 18 minutos y fue retirado"*.

---

## Puesta en marcha

```bash
cd secop-radar
pip install -r requirements.txt

# 1. Ver el esquema real de los datasets (requiere internet)
python scripts/discover_schema.py

# 2. Ver cómo se ve todo, sin red ni credenciales
python scripts/demo.py

# 3. Barrido real sin enviar correo
python scripts/run.py --dry-run

# 4. Barrido completo, todos los perfiles
python scripts/run.py

# Sólo un perfil
python scripts/run.py --perfil marketing
```

Las pruebas corren sin red:

```bash
python tests/test_radar.py
```

---

## Perfiles: un radar por sector

Cada archivo en **`perfiles/`** es un radar independiente. No hay nada de un
sector escrito en el código — el motor sólo sabe normalizar texto, puntuar y
archivar; qué busca se lo dice el YAML.

```
perfiles/
├── educacion.yml     → formación y capacitación (ITGEM)
└── marketing.yml     → publicidad y comunicaciones
```

Agregar un cliente es agregar un archivo. Para desactivar uno sin borrarlo,
`activo: false`.

**Los datos se descargan una sola vez y se evalúan contra todos los perfiles.**
Sumar perfiles no multiplica las consultas a SECOP ni acerca al límite de tasa:
la descarga usa la unión de las geografías y después cada perfil recorta lo suyo
localmente.

| Bloque | Para qué |
|---|---|
| `nombre` | Cómo aparece en alertas y dashboard |
| `activo` | `false` lo apaga sin borrarlo |
| `alertar_a` | Destinatarios de ESTE perfil. Vacío = usa `ALERT_TO` |
| `geografia.departamentos` | Dónde buscar |
| `keywords.criticas` | Si aparece alguna, es candidato fuerte |
| `keywords.deseables` | Suman puntos, no bastan solas |
| `keywords.excluyentes` | Si aparece alguna, se descarta |
| `unspsc.familias` | Códigos oficiales de categoría |
| `valor.minimo` / `maximo` | Rango de contrato en pesos |
| `umbral_alerta` | Score mínimo para disparar correo (subir si hay ruido) |

Da igual escribir con tildes o sin ellas: todo se normaliza antes de comparar.

Un mismo proceso puede interesarle a varios perfiles a la vez: cada uno lleva su
propio puntaje y su propio seguimiento, y que desaparezca para uno no afecta al
otro.

### Cómo acertarle a los códigos UNSPSC

No busques los códigos en tablas. Dejá `unspsc.familias` vacío, corré
`python scripts/run.py --dry-run`, mirá qué código traen los resultados buenos y
agregá esas familias. En dos iteraciones queda afinado, con datos reales en vez
de adivinanzas.

### Cómo se calcula el score

| Señal | Puntos |
|---|---|
| Una palabra clave crítica | 35 |
| Dos o más | 45 |
| Categoría UNSPSC coincidente | 20 |
| Cada palabra deseable | 5 (tope 15) |
| Valor dentro de rango | +8 |
| Valor bajo el mínimo | −12 |
| Cierra en ≤3 días | +10 |
| Cierra en ≤10 días | +5 |
| Ya cerrado | −25 |

El techo real es 98, así que nada satura en 100 y el ranking sigue siendo útil
cuando caen varias oportunidades buenas a la vez. Cada alerta lista las señales
que la dispararon: el score nunca es una cifra sin explicación.

---

## Configuración en GitHub Actions

En **Settings → Secrets and variables → Actions**:

| Secret | Para qué | Obligatorio |
|---|---|---|
| `SOCRATA_APP_TOKEN` | Token gratuito de datos.gov.co. Sin él la API limita fuerte la tasa de consultas | Recomendado |
| `SMTP_USER` | Correo remitente | Sí |
| `SMTP_PASS` | Contraseña de aplicación (en Gmail: *App Password*, no la clave normal) | Sí |
| `ALERT_TO` | Destinatarios, separados por coma | Sí |
| `SMTP_HOST` | Por defecto `smtp.gmail.com` | No |
| `SMTP_PORT` | Por defecto `587` | No |
| `SMTP_FROM` | Por defecto igual a `SMTP_USER` | No |

Sin credenciales SMTP el barrido igual corre y archiva: sólo se salta el envío.

---

## Alcance y límites

**Lo que hace hoy (Fase 1):** cobertura completa y confiable vía la API de datos
abiertos, con archivo histórico, scoring explicable, correo y dashboard.

**Lo que no hace todavía:** la API de datos abiertos **se refresca por ciclos**,
no en tiempo real. Sirve perfecto para cobertura sistemática, pero por sí sola no
atrapa un proceso que vive cinco minutos. Para eso hace falta una segunda capa
que consulte la búsqueda pública de SECOP II en vivo y con mucha más frecuencia
— pendiente de reconocimiento técnico.

**La jugada de la Fase 1.5:** el dataset `paa` (Plan Anual de Adquisiciones)
contiene lo que las entidades *planean* comprar, meses antes de publicar el
proceso. En vez de correr detrás de contratos efímeros, se llega antes que
todos. Se activa agregando `paa` a la lista `datasets` en `criterios.yml`.

---

## Estructura

```
secop-radar/
├── perfiles/               # un YAML por sector: lo único que se edita
│   ├── educacion.yml
│   └── marketing.yml
├── radar/
│   ├── config.py           # carga de perfiles y uniones
│   ├── normalize.py        # normalización de texto (anti-tildes)
│   ├── fields.py           # alias de columnas -> esquema real
│   ├── socrata.py          # cliente de la API, paginación y reintentos
│   ├── scoring.py          # relevancia explicable 0-100
│   ├── store.py            # archivo SQLite + detección de retiros
│   ├── notify.py           # correo HTML
│   └── report.py           # dashboard estático
├── scripts/
│   ├── discover_schema.py  # imprime el esquema vivo
│   ├── demo.py             # muestra sin red
│   └── run.py              # barrido completo
└── tests/test_radar.py     # 53 pruebas, sin red
```

> La base de datos cambió de formato al agregar perfiles (la clave primaria pasó
> de `id` a `id + perfil`). Si venís de una versión anterior, borrá
> `data/radar.db` y se reconstruye sola en el siguiente barrido; el código avisa
> con un mensaje claro en vez de corromper el archivo.

### Sobre `fields.py`

Las columnas de los datasets de SECOP se renombran entre refrescos y la
documentación publicada va por detrás del esquema vivo. Por eso cada campo
lógico declara varios nombres candidatos y se resuelve contra las claves que
realmente vienen en la respuesta. Si algo queda sin resolver,
`discover_schema.py` lo dice y basta con agregar el nombre real a la lista.

---

## Origen de los datos

Datos abiertos de SECOP publicados por Colombia Compra Eficiente vía
`datos.gov.co` (API Socrata). Son datos públicos consumidos por su API oficial:
no hay scraping ni acceso no autorizado de por medio.

| Dataset | ID |
|---|---|
| SECOP II — Procesos de Contratación | `p6dx-8zbt` |
| SECOP II — Contratos Electrónicos | `jbjy-vk9h` |
| SECOP I | `xvdr-vrge` |
| Plan Anual de Adquisiciones | `b6m4-qgqv` |
