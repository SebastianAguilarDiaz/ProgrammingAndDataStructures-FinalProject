# Contexto del proyecto — MazeView / Resolvedor de laberintos

Este documento es el contexto de referencia para cualquier IA o persona que trabaje en el
repositorio. **Debe actualizarse cada vez que se modifique el código, los formatos o el
comportamiento del proyecto.** Al iniciar una sesión, leer este archivo antes de tocar código.

## 1. Resumen

Proyecto de Estructura de Datos (Semestre 3) que resuelve laberintos representados como
matrices de caracteres. Tiene dos piezas:

1. **`maze.cpp`** (núcleo en C++): lee un laberinto `.in`, construye dos árboles de búsqueda
   (DFS y BFS), calcula recorridos y escribe archivos `.sol` con las secuencias de nodos.
2. **`maze_gui.py`** (interfaz en Python/Tkinter): carga el `.in`, ejecuta `a.exe`, lee los
   `.sol` y los dibuja/anima con un tema oscuro.

No hay backend, base de datos ni dependencias externas de Python. Todo es local.

- Rama actual de desarrollo: `GUI`
- Remoto: `https://github.com/SebastianAguilarDiaz/ProgrammingAndDataStructures-FinalProject`
- Último commit conocido: `8762602 First gui version`
- Sistema objetivo: Windows (PowerShell); el código C++ y la GUI también son portables.

## 2. Archivos del repositorio

| Archivo | Rol |
| --- | --- |
| `maze.cpp` | Programa principal C++ (fuente del resolvedor). |
| `a.exe` | Compilado actual de `maze.cpp` que usa la GUI. **No está versionado** (`*.exe` en `.gitignore`). |
| `maze.exe` | Compilado antiguo/alterno de C++. No lo usa la GUI. No versionado. |
| `maze_gui.py` | Aplicación Tkinter completa (visualizador + animación + validaciones). |
| `maze_gui.bat` | Lanzador de Windows: hace `cd` a la carpeta del script y ejecuta `python maze_gui.py %*`. |
| `laberinto_1.in` … `laberinto_6.in` | Laberintos de prueba (4×4, 6×6, 10×10, 30×60, 100×100, 49×95). |
| `laberinto_1_*.sol` | Salidas de ejemplo de `a.exe` para el primer laberinto. **Ignoradas por git** (`*.sol`). |
| `.gitignore` | Ignora `*.out`, `*.exe`, `*.sol`. |
| `AGENTS.md` | Este documento de contexto. |

No hay `README`, pruebas automatizadas, linter ni configuración de build. No inventar
frameworks ni comandos de test: la verificación es ejecutar el binario y la GUI.

## 3. Compilación y ejecución

Compilar el núcleo (MinGW/g++, C++17):

```powershell
g++ -O2 -std=c++17 -o a.exe maze.cpp
```

Ejecutar el resolvedor sobre un laberinto (el argumento es un único archivo):

```powershell
.\a.exe laberinto_1.in
```

La GUI (requiere Python 3 con Tkinter incluido):

```powershell
python maze_gui.py laberinto_1.in
# o, en Windows:
.\maze_gui.bat laberinto_1.in
```

`a.exe` debe estar en la misma carpeta que `maze_gui.py`; la GUI lo busca por ruta absoluta en
el directorio del script. Si se recompila `maze.cpp`, hay que regenerar `a.exe`.

## 4. Formato de entrada `.in`

```
<filas> <columnas>
<grid como caracteres>
```

Ejemplo (`laberinto_1.in`):

```
4 4
B100
0101
0111
110A
```

Símbolos (definidos en `maze.cpp:11-14`):

| Carácter | Significado |
| --- | --- |
| `0` (`WALL_CHAR`) | Pared. No transitable. |
| `1` (`PATH_CHAR`) | Camino transitable. |
| `A` (`ROOT_CHAR`) | Origen/raíz del árbol. Exactamente uno esperado. |
| `B` (`TARGET_CHAR`) | Destino. Exactamente uno esperado. Transitable. |

Reglas de lectura (importante, la GUI las replica):

- C++ usa `fin >> filas >> columnas` y luego `fin >> caracter`, es decir, **lee los caracteres
  como un flujo continuo separado por espacios/saltos de línea**, no por líneas.
- Solo se consumen `filas * columnas` caracteres; los extra se ignoran.
- Se usa la **primera** `A` y la **primera** `B` en orden de lectura (fila por fila, izquierda a
  derecha). Ver `findNode()` en `maze.cpp:271`.
- Cualquier carácter distinto de `0`, `1`, `A`, `B` se comporta como muro.
- Si faltan celdas, el flujo de C++ falla y el laberinto queda incompleto; la GUI lo detecta y
  muestra error.
- Coordenadas: siempre `(fila, columna)`, base 0. `A` y `B` son celdas transitables.

### Particularidad de los datos

`laberinto_4.in` declara `30 60` pero cada línea tiene 62 caracteres (1860 en total). Como C++
lee en flujo continuo, toma los primeros 1800 caracteres y **desalinea las filas**; la GUI avisa
de los caracteres extra pero reproduce la misma interpretación para que las coordenadas coincidan
con `a.exe`. Los demás archivos cuadran con su encabezado.

## 5. Núcleo C++ (`maze.cpp`)

### Estructuras y utilidades

- `Node` (`maze.cpp:27`): `value` = `pair<int,int>` (fila, col), `parent`, `children`.
  Métodos: `addChildren`, `addParent`, `ancesters`, `preorder`, `postorder`, `bfs`,
  `getValue` (devuelve `"fila col"`).
- Numeración de nodos: `node = fila * columnas + columna` (`getNodeNumber`), inversa
  `getNodeCords`. Es la clave de los `unordered_map<int,Node*>` que representan cada árbol.
- Movimientos (`maze.cpp:18`): abajo, izquierda, arriba, derecha — `{1,0}`, `{0,-1}`,
  `{-1,0}`, `{0,1}`. El orden importa para la forma de ambos árboles.
- `loadMatrix`: lectura secuencial descrita arriba.
- `findNode`: primer carácter objetivo en orden row-major.
- `solveMaze` (`maze.cpp:153`): orquesta todo; valida que existan `A` y `B`; construye ambos
  árboles; escribe cinco `.sol`; libera los nodos.

### Construcción de árboles

- **BFS** (`createTreeBFS`, `maze.cpp:312`): cola, semilla `A`, explora vecinos en el orden de
  `movements`, marca visitados por número de nodo y añade al árbol solo celdas `1` o `B`.
  Produce el árbol de menor profundidad.
- **DFS** (`createTreeDFS`, `maze.cpp:357`): pila, semilla `A`, marca `visited` al **desapilar**,
  no al apilar. Puede insertar/duplicar temporalmente nodos en el mapa si un vecino se alcanza
  desde varios padres antes de ser procesado; el `visited` evita reprocesarlo. Es un
  comportamiento conocido: no "corregirlo" sin revisar los `.sol` esperados.

Ambos árboles solo contienen nodos alcanzables desde `A`; `B` se incluye si es alcanzable.
Si `B` no es alcanzable, no se generan los archivos de solución correspondientes y se imprime
`There's no dfs solution` / `There's no bfs solution` en consola.

### Salidas `.sol`

Para una entrada `X.in`, `file_name = ruta sin los últimos 3 caracteres` (normalmente quita
`.in`). Se generan:

| Archivo | Contenido |
| --- | --- |
| `X_preorder.sol` | Preorden del árbol DFS desde `A`. |
| `X_postorder.sol` | Postorden del árbol DFS (hijos antes que la raíz). |
| `X_bfs.sol` | Recorrido BFS por niveles desde `A`. |
| `X_dfs_solution.sol` | Ruta de `B` a `A`: primero `B`, luego la cadena de ancestros (padre, abuelo… hasta `A`). |
| `X_bfs_solution.sol` | Igual que la anterior, usando el árbol BFS. |

- Formato: una coordenada por línea, `"fila columna"`, sin encabezado. Línea final con salto.
- La solución **no** repite `B` al final: la cadena de ancestros empieza en el padre de `B`.
- La consola imprime `"<archivo> created"` por cada archivo escrito.
- `main` exige exactamente un argumento (`argc == 2`); si no, imprime
  `Wrong amount of arguments`.

### Memoria

`freeTreeMemory` hace `delete` de los nodos de ambos árboles. Los vectores devueltos por
`preorder`/`postorder`/`bfs`/`ancesters` se crean con `new` y no se liberan (fuga menor conocida).

## 6. GUI (`maze_gui.py`)

### Configuración y constantes

- `APP_NAME = "MazeView"`, subtítulo y estilos en la clase `MazeGui` (`maze_gui.py:295`).
- `TICK_MS = 40`, `SOLVER_TIMEOUT = 120` s, `SPEED_VALUES = [1, 2, 5, 10, 25, 50, 100, 250, 1000]`.
- `KINDS` (`maze_gui.py:33`) define las cinco salidas: clave del `.sol`, etiqueta, grupo
  (`tree` o `path`) y descripción.
- `PATH_KINDS = {"dfs_solution", "bfs_solution"}`: solo estas dibujan la línea de ruta.

### Flujo principal

1. `main()` activa DPI awareness en Windows, crea `tk.Tk`, acepta un `.in` por `sys.argv[1]`.
2. `MazeGui.__init__` localiza `base_dir` y `exe_path = <base_dir>/a.exe`, construye UI,
   deshabilita controles y carga el archivo inicial si existe.
3. `load_path` valida con `parse_maze`, reinicia estado, dibuja y agenda `run_solver` a los 60 ms.
4. `run_solver`:
   - Borra los `.sol` previos de las cinco claves (los que pueda).
   - Ejecuta `a.exe <ruta_absoluta>` con `cwd=base_dir`, sin ventana
     (`CREATE_NO_WINDOW`), capturando stdout+stderr y con timeout.
   - Carga los `.sol` cuya `mtime` sea posterior al inicio (tolerancia de 1 s).
   - Si no encontró secuencias pero el log dice `created`, reintenta copiando el `.in` a una
     carpeta temporal (caso de permisos de escritura) y usa esos resultados.
   - Traduce mensajes conocidos de `a.exe` a avisos en español, detecta archivos faltantes y
     muestra el primer problema en «Detalles».
   - Si hay secuencias, selecciona el primer recorrido disponible y lo muestra completo.
5. `parse_maze` replica la lectura de C++ (flujo continuo, primeras `A`/`B`, límite de
   4 000 000 de celdas) y acumula advertencias: celdas faltantes/extra, varias `A`/`B`,
   caracteres desconocidos y longitudes de línea distintas a las columnas declaradas.
6. `read_solution` lee `"fila col"` por línea, ignora vacías y descarta/contabiliza coordenadas
   fuera de rango o líneas inválidas.

### Interfaz y animación

- Ventana mínima 1040×640, tema espacial profundo con paleta desaturada, layout: header (título,
  selector de `.in`, Abrir, Regenerar), canvas del laberinto con scroll/zoom + leyenda, panel
  derecho con tarjetas «Sector», «Ruta de vuelo» y «Bitácora de vuelo».
- Fondo de espacio profundo (`_draw_space`): tres nebulosas suaves con `stipple`, campo de
  estrellas aleatorio y destellos; `_twinkle_stars` parpadea estrellas cada 260–720 ms. El job
  `_star_job` se cancela al cerrar la ventana.
- **Laberinto isométrico 3D**: cada celda se dibuja como un rombo. Las celdas de camino usan un
  rombo exterior oscuro + interior con margen; las paredes son prismas con cara superior y dos
  caras laterales (`WALL`, `WALL_SIDE_LIGHT`, `WALL_SIDE_DARK`). Todo el tablero flota sobre una
  plataforma con grosor y sombra. El orden de dibujo es por profundidad (`fila + columna`), de
  atrás hacia adelante. Las caras laterales se omiten cuando el vecino `(fila+1, col)` o
  `(fila, col+1)` también es muro.
- Para laberintos grandes se simplifica el dibujo: si `cell_px < 9.5` las celdas de camino se
  pintan como rombos planos sin margen; si `cell_px < 7.5` los muros omiten las caras laterales.
  Esto evita moiré y mantiene fluida la vista de 100×100.
- Paleta: muro `#26384a` (caras `#1a2836` / `#111c27`), camino `#466a78`, `A` verde salvia
  `#8fbfa8`, `B` rosa empolvado `#c08b9a`, desconocido `#4b4f5e`, rejilla `#223846`, acento
  `#8fc2cc`, estela arena `#cfa77f`. Sin colores neón.
- `A` se dibuja como OVNI/nave (`_draw_ship`) y `B` como planeta anillado (`_draw_planet`),
  ambos en su profundidad isométrica (las celdas frontales pueden ocultarlos parcialmente). El
  nodo actual se marca con un mini OVNI (`_draw_ufo_marker`) siempre por encima del tablero.
- Al visitar celdas se rellenan con un degradado suave verde-azulado → azul → malva
  (`GRADIENT_STOPS`); en las soluciones se dibuja una estela de capas arena/crema.
- Coordenadas isométricas: `cx = ox + (col - fila) * tw / 2`, `cy = oy + (col + fila) * tw / 4`;
  `_cell_rect` devuelve el rectángulo envolvente del rombo y `_sprite_center` su centro. `auto_fit`
  reserva espacio para la plataforma y el zoom se aplica sobre el ancho de baldosa (`cell_px`).
- **No hay etiquetas numéricas sobre las celdas**: el avance se muestra solo por el llenado de
  colores y el OVNI actual. La lista «Bitácora de vuelo» del panel derecho sí conserva sus pasos.
- Controles: anterior/siguiente, Reproducir/Pausa, Reiniciar, Ver todo, slider de velocidad,
  barra de progreso, lista de nodos (clic para saltar).
- La velocidad se aplica con un acumulador entero (`_step_accum`): cada tick de `TICK_MS`
  suma `velocidad × TICK_MS` y avanza un nodo por cada 1000 unidades. Así los valores bajos
  (1, 2, 5…) también se respetan y el slider afecta la animación en curso.
- Zoom con rueda del ratón y botones `−` / `+` / `Ajustar`; `auto_fit` recalcula la celda al
  redimensionar. El panel derecho tiene su propio scroll.
- `temp_dirs` y el job de estrellas `_star_job` se limpian al cerrar la ventana
  (`WM_DELETE_WINDOW`).

### Detalles a tener en cuenta

- La GUI asume `a.exe`, no `maze.exe`.
- Compara `mtime` para no leer `.sol` viejos; si se reutilizan archivos con marcas de tiempo
  raras, pueden ignorarse.
- Las rutas y mensajes de UI están en español; mantener ese idioma en textos nuevos.
- No hay tests; validar cambios ejecutando al menos `laberinto_1.in` y comparando los cinco
  `.sol`, además de abrir la GUI y reproducir las cinco secuencias.

## 7. Verificación rápida

```powershell
g++ -O2 -std=c++17 -o a.exe maze.cpp
.\a.exe laberinto_1.in
Get-ChildItem laberinto_1_*.sol
python maze_gui.py laberinto_1.in
```

Para `laberinto_1.in` (A en `(3,3)`, B en `(0,0)`) las soluciones deben ir de `0 0` (B) hasta
`3 3` (A). Los `.sol` son artefactos locales ignorados por git; no commitearlos ni asumir que
existen en un clon limpio.

## 8. Convenciones de trabajo

- No agregar dependencias ni frameworks sin necesidad; mantener C++ estándar y Tkinter puro.
- Respetar el formato de `.sol` y la lectura por flujo continuo: la GUI y C++ deben coincidir.
- No cambiar el orden de `movements` ni la semántica de los árboles sin actualizar este
  documento y revisar los recorridos esperados.
- No commitear binarios (`a.exe`, `maze.exe`) ni `.sol`.
- **Si se cambia código, formatos, archivos o comportamiento, actualizar la sección
  correspondiente de este `AGENTS.md` en el mismo cambio.**
