"""Ground truth about the running game, read out of the emulator instead of the screen.

The screen is a guess; this is a fact. np2kai does not expose the game's memory through
the libretro memory interface (`get_memory_data(2)` hands back 3 MB of extended RAM that
the DOS game barely touches -- 3.2% non-zero, no script in it, and every other region
reports size 0). But `serialize()` dumps the whole machine in 2.8 ms, and the AI5 engine
keeps the `.mes` script it is executing in there **byte for byte**, so the compiled files
in `en/` are themselves the lookup table.

Two things come out of that, every turn, with no vision model in the loop:

  identify()  which scene is loaded -- START.MES, FLOOR05C.MES, ... All 84 scripts have
              distinct fingerprints, so this is exact, not a similarity score. Use it for
              "did the room change", which pixel hashes can only approximate.
  churn()     whether the machine is idle-waiting-for-input, busy, or genuinely wedged.
              A pixel hash cannot tell those apart, which is why "no new screen for N
              turns" kept firing on a game that was simply waiting for a keypress.

Scripts load back-to-back in a stack: START.MES sits at 0x095f0, is 11103 bytes, and the
next slot begins at 0x0c150 -- exactly len+1, rounded up to even. So one anchor is enough
to walk the whole stack; the expensive rescan only runs when the anchor no longer matches.

Self-test: `.venv/bin/python state.py [some.state]`
"""
import hashlib, os, pathlib
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "en"          # translated, compiled
ORIGINALS = HERE.parent / "work"      # the untouched Japanese .MES

FIRST_SLOT = 0x095f0      # where the first (system) script sits; verified across runs
SCAN_LIMIT = 0x40000      # rescan window: the game lives in low DOS memory
FP_OFF, FP_LEN = 64, 64   # fingerprint window -- all 84 scripts are distinct here
CHURN_BLOCK = 0x10000     # 64 KB blocks for the idle/busy/wedged classification
CHURN_SPAN = 0x100000     # classify over the low 1 MB only
VRAM_LO, VRAM_HI = 0xa0000, 0xc0000   # PC-98 text + graphics planes: the liveness signal
VM_LO = 0x09000           # AI5 interpreter working area, just below the first script slot


def _load_scripts():
    """Index BOTH builds.

    ⚠️ Indexing only the translated files made this sensor blind on any image carrying an
    original script -- an experiment that swapped one .mes in reported `scene=START.MES`
    forever, because the resident Japanese script matched nothing. Names of originals are
    suffixed `:ja`, so a lookup also tells you WHICH BUILD is actually loaded, which is
    precisely the check such an experiment needs and which had to be done by hand before.
    """
    out = {}
    for p in sorted(SCRIPTS.iterdir()):
        if p.name.endswith(".rkt.mes"):
            out[p.name[:-len(".rkt.mes")]] = p.read_bytes()
    if ORIGINALS.is_dir():
        for p in sorted(ORIGINALS.iterdir()):
            if p.name.endswith(".MES") and p.is_file():
                out[p.name + ":ja"] = p.read_bytes()
    # ⚠️ Знать два билда мало. Эксперимент, который СОБИРАЕТ третий вариант скрипта
    # (emu/size_ladder.py, emu/bisect_crash.py), кладёт в образ файл, которого нет ни в
    # en/, ни в work/ -- и тогда identify() честно не узнаёт сцену и молча возвращает
    # только START.MES. Читается это как «игра не дошла до локации», а на самом деле
    # означает «датчик не знает этих байтов». Ровно та же слепота, про которую
    # предупреждает docstring выше, только на шаг дальше.
    # → эксперимент передаёт свой .mes через WW_EXTRA_MES (можно через ":").
    for extra in filter(None, os.environ.get("WW_EXTRA_MES", "").split(":")):
        q = pathlib.Path(extra)
        if q.is_file():
            out[q.name + ":var"] = q.read_bytes()
    return out


SCRIPT_BYTES = _load_scripts()
# fingerprint -> [names]. Near-identical variants DO collide in one 64-byte window
# (SENTO10A.MES and SENTO11.MES differ by 40 bytes in 22 KB), so a collision resolves by
# comparing more bytes at the slot rather than raising -- a sensor that dies on an
# unexpected pair is worse than one that looks a little harder.
_FP = {}
for _n, _b in SCRIPT_BYTES.items():
    if len(_b) >= FP_OFF + FP_LEN:
        _FP.setdefault(hashlib.blake2b(_b[FP_OFF:FP_OFF + FP_LEN],
                                       digest_size=8).digest(), []).append(_n)
_AMBIG = sum(1 for v in _FP.values() if len(v) > 1)

# corpus-unique needles, for the rescan that finds the anchor again after it moves
_seen = {}
for _n, _b in SCRIPT_BYTES.items():
    for _i in range(0, len(_b) - 24, 23):
        _seen.setdefault(_b[_i:_i + 24], []).append((_n, _i))
_NEEDLES = {}
for _g, _where in _seen.items():
    if len(_where) == 1:
        _n, _i = _where[0]
        _NEEDLES.setdefault(_n, []).append((_i, _g))
_NEEDLES = {n: v[:12] for n, v in _NEEDLES.items()}
del _seen


def _at(state, base):
    """Name of the script whose bytes start at `base`, or None. O(1) in the common case.

    A name ending in `:ja` is an ORIGINAL Japanese script -- so this also answers "which
    build is actually loaded", which is exactly what an experiment that swaps one .mes has
    to verify and previously had to check by hand.
    """
    if base < 0 or base + FP_OFF + FP_LEN > len(state):
        return None
    cands = _FP.get(hashlib.blake2b(state[base + FP_OFF:base + FP_OFF + FP_LEN],
                                    digest_size=8).digest())
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    for n in cands:                       # ambiguous window: compare a longer prefix
        b = SCRIPT_BYTES[n]
        k = min(len(b), 4096)
        if state[base:base + k] == b[:k]:
            return n
    return cands[0]


PROBE_AHEAD = 512         # ⚠️ не догадка, а замер: см. комментарий в _next_slot


def _next_slot(base, name):
    """Начало следующего слота — ПРЕДПОЛОЖЕНИЕ, которое обязано проверяться.

    ⚠️ «Встык, с выравниванием до чётного» — правило, откалиброванное на ОДНОМ образе.
    На QA-сборке оно промахивается: `START.MES` (11 103 б) лежит по 0x95f0, арифметика даёт
    0xC150, а `FLOOR05.MES:ja` реально лежит по **0xC170** — на 32 байта дальше. Промах не
    приводил к ошибке: walk() просто обрывался и возвращал ['START.MES'], что читается как
    «комната не загружена». Ровно то же читалось бы, если бы игра и правда не загрузила
    комнату, — то есть датчик молча путал две разные ситуации.
    """
    n = len(SCRIPT_BYTES[name])
    return base + n + (n & 1)


def walk(state, first=FIRST_SLOT):
    """Стек скриптов от `first`: [(base, name), ...], внешний первым.

    Возвращает (стек, оборван?). `оборван` = дошли до места, где скрипт не опознан, но в
    пределах PROBE_AHEAD его тоже не нашли. Вызывающий обязан различать «стек кончился» и
    «датчик потерял след» — раньше оба выглядели одинаково.
    """
    out, base, seen = [], first, set()
    while base not in seen:
        seen.add(base)
        name = _at(state, base)
        if name is None:
            # арифметика промахнулась -- ищем начало следующего слота рядом, прежде чем
            # объявлять, что стек кончился
            for d in range(2, PROBE_AHEAD, 2):
                name = _at(state, base + d)
                if name is not None:
                    base += d
                    break
            if name is None:
                return out, bool(out)
        out.append((base, name))
        base = _next_slot(base, name)
    return out, False


def resident(state, limit=SCAN_LIMIT):
    """Все известные скрипты, реально лежащие в памяти. ~120 мс, поэтому только на промахе.

    ⚠️ Согласия иголок НЕДОСТАТОЧНО. На QA-состоянии голосование выдавало пять скриптов, из
    которых три — призраки: `START.MES:ja` по 0x9625 (11 согласных иголок!) и `FLOOR05.MES`
    по 0xc1e9. Два билда одного скрипта делят длинные куски байтов, и иголки садятся на
    сдвинутую базу. Поэтому каждый кандидат ДОСВЕРЯЕТСЯ побайтно — призраки отваливаются.
    """
    low = state[:limit]
    cands = {}
    for name, needles in _NEEDLES.items():
        agree = {}
        for off, g in needles:
            j = low.find(g)
            if j != -1:
                agree[j - off] = agree.get(j - off, 0) + 1
        for base, n in agree.items():
            if n >= 3 and base >= 0:
                cands.setdefault(base, []).append((n, name))
    out = []
    for base in sorted(cands):
        for _, name in sorted(cands[base], reverse=True):
            b = SCRIPT_BYTES[name]
            k = min(len(b), 4096)
            if state[base:base + k] == b[:k]:
                out.append((base, name))
                break
    return out


def rescan(state, limit=SCAN_LIMIT):
    """Стек, когда якорь уехал. Теперь это сверенный скан, а не walk() от нижней базы.

    ⚠️ Прежняя версия находила самую нижнюю базу и шла от неё ТОЙ ЖЕ арифметикой, что и
    walk(). То есть «запасной путь» опирался на то же непроверенное правило и падал вместе
    с основным: на QA-состоянии оба возвращали ['START.MES'], хотя `FLOOR05.MES:ja` лежал
    в памяти по 0xC170.
    """
    return resident(state, limit)


def identify(state, first=FIRST_SLOT):
    """What is loaded right now.

    {"scene": <current scene>, "stack": [...], "anchor": <base to reuse next turn>,
     "rescanned": bool}

    `scene` is the innermost script -- the outermost slot is the always-resident system
    script (START.MES), the one on top of the stack is the room/event actually playing.
    Pass last turn's `anchor` back in to keep this on the O(1) path.
    """
    # ⚠️ Быстрый путь по арифметике убран НАСОВСЕМ, и вот почему. Он опирался на правило
    # «следующий слот встык», которое верно не на всех образах (см. _next_slot), а его
    # промах выглядел как готовый короткий ответ. Попытка чинить это флагом «оборван» не
    # помогла: стек ВСЕГДА где-то кончается, флаг всегда истинен, полный скан всё равно
    # выполнялся каждый раз -- только теперь ещё и под видом оптимизации.
    # 118 мс против секунд на ход агента. Точность здесь дешевле догадки.
    stack = resident(state)
    rescanned = True
    return {"scene": stack[-1][1] if stack else None,
            "stack": [n for _, n in stack],
            "anchor": stack[0][0] if stack else first,
            "rescanned": rescanned}


# --- is it wedged, or just waiting for me? -----------------------------------------
def churn(states):
    """Classify a run of snapshots taken with NO input in between.

    A pixel hash says "nothing changed" for all four of these; they need different
    responses.

      busy    VRAM is being written -> the game is drawing. Healthy. Wait.
      idle    VRAM still, but the interpreter's working area moves -> the game is alive
              and waiting for a keypress. Healthy. Press something.
      dead    the machine still ticks (BIOS timers in low memory) but NOTHING draws and
              the interpreter is static -> the game has died and what is on screen is a
              stale framebuffer nobody ever cleared. Only a reload gets out of this.
      frozen  not a byte moves anywhere -> the emulator itself is wedged.

    ⚠️ `dead` is the case that cost us a whole debugging session. It looks exactly like a
    working game on screen -- the last frame is still sitting in VRAM, complete with the
    status panel -- so the agent kept "exploring" a corpse for 44 turns. Measured against
    a known-alive control on the same save-state:

        alive : blocks 0x0,0x10000,0x20000,0x60000,0xa0000,0xb0000,0xe0000 -> 2329 bytes
        dead  : block 0x0 only                                             ->  456 bytes

    VRAM (0xa0000/0xb0000 -- PC-98 text and graphics planes) is the liveness signal, and
    the AI5 interpreter's own working area just below the script buffers is the second.
    Movement in low memory alone is only the BIOS heartbeat and proves nothing.

    Returns {"class": ..., "blocks": {block_index: bytes_differing}, "total": int,
             "vram": int, "vm": int}.
    """
    if len(states) < 2:
        return {"class": "unknown", "blocks": {}, "total": 0}
    nblocks = min(min(len(s) for s in states), CHURN_SPAN) // CHURN_BLOCK
    span = nblocks * CHURN_BLOCK
    arrs = [np.frombuffer(s[:span], np.uint8).reshape(nblocks, CHURN_BLOCK) for s in states]
    acc = np.zeros(nblocks, np.int64)
    for k in range(1, len(arrs)):
        acc += (arrs[k] != arrs[k - 1]).sum(axis=1)
    blocks = {b: int(v) for b, v in enumerate(acc) if v}
    total = int(acc.sum())

    # VRAM planes: anything drawing at all touches these
    vram = sum(n for b, n in blocks.items() if VRAM_LO <= b * CHURN_BLOCK < VRAM_HI)
    # the interpreter's working area sits just below the first script slot
    vm = 0
    for k in range(1, len(states)):
        a = np.frombuffer(states[k - 1][VM_LO:FIRST_SLOT], np.uint8)
        c = np.frombuffer(states[k][VM_LO:FIRST_SLOT], np.uint8)
        vm += int(np.count_nonzero(a != c))

    if total == 0:
        cls = "frozen"
    elif vram:
        cls = "busy"
    elif vm:
        cls = "idle"
    else:
        cls = "dead"
    return {"class": cls, "blocks": blocks, "total": total, "vram": vram, "vm": vm}


def stats(st):
    """Player stats straight out of the machine -- no vision, no OCR, no guessing.

    Addresses came from a value search across the original Japanese build and the
    translated one booted to the same first room (see wordsworth.json). They are offsets
    into the serialize() blob, not physical RAM.

    ⚠️ These five fields are how the translation defect was caught: the translated build
    starts with level 0, STR 33164 and DEF 28740 (the panel prints those as "MAX") where
    the original has level 1, STR 8, DEF 7.

    ⚠️ HP нашёлся не поиском по значению, а ЧТЕНИЕМ СКРИПТА, который рисует панель
    (START.MES): `(number (+ (~ M 1) 1)) "/" (number (+ (~ M 9) 1))`. Блок игрока -- это
    массив M скриптов (uint16, шаг 2): M0 level, M1 HP-1, M2 exp, M3 STR, M4 DEF, M6 gold,
    M9 макс.HP-1. HP хранится НА ЕДИНИЦУ МЕНЬШЕ показанного -- поэтому все прошлые поиски
    «12 при 12/12» и не находили ничего. При смерти панель пишет 0000, то есть M1 = -1:
    читаем со знаком. Подтверждено замером в бою (emu/signal_audit.py).

    ⚠️ Вне подземелья блок переинициализирован: на титуле и в G_OVER память даёт
    STR 0 / DEF 0 / GOLD 100, а панель -- прежние значения. Судить по этим числам можно
    только в игре (`boot.in_game`).
    """
    import struct
    m = lambda i, fmt="<H": struct.unpack_from(fmt, st, M_BASE + 2 * i)[0]
    return {"level": m(0), "exp": m(2), "str": m(3), "def": m(4), "gold": m(6),
            "hp": m(1, "<h") + 1, "hp_max": m(9, "<h") + 1}


M_BASE = 0x1790e     # массив M скриптов = блок игрока (START.MES рисует панель из него)


X_AXIS = 0x37438
POS_AXIS = 0x3743a   # = Y_AXIS; имя оставлено, на него ссылается старый код
Y_AXIS = 0x3743a     # position along the direction of travel; the OTHER axis is not found
FACING = 0x3743c
COMPASS = ("north", "east", "south", "west")   # order unverified; the CYCLE is what is proven


def facing(st):
    """Which way the player is looking, 0..3. Read, not guessed from the picture.

    Found by pressing left eight times and looking for a byte that cycled 3,0,1,2,3,0,1,2,3 --
    a period-4 pattern repeated twice, which essentially nothing else in memory does.
    ⚠️ Which number means north is NOT established; only that the four values are the four
    directions and that `left` steps through them in order. Use it for "have I already faced
    this way from here", which needs no compass rose, not for absolute bearings.
    """
    return st[FACING] if len(st) > FACING else None


# Куда ведёт шаг вперёд при каждом курсе. Выведено из замкнутого круга (см. where()).
STEP = {0: (1, 0), 1: (0, -1), 2: (-1, 0), 3: (0, 1)}


def where(st):
    """Клетка игрока и курс. Прочитано из памяти, не угадано по картинке.

    Обе оси лежат рядом, как поля одной записи: X `0x37438`, Y `0x3743a`, курс `0x3743c`.

    Y нашли первым: человек прошёл вперёд-вперёд-назад-назад дважды, и байт дал
    5,4,3,4,5,4,3,4,5,4,3,2, не шелохнувшись на восьми чистых поворотах.

    X дался только со второго захода, и вот чем он отличался. Тот первый проход держал ОДИН
    курс, поэтому X всё время стоял и был неотличим от всего прочего, что стояло. Скриптовые
    пробы из сохранения тоже ничего не дали -- игрок упирался в стену (`emu/axis_walk.py`).
    Сработала запись ЖИВОГО прохода: 12 настоящих шагов на курсах 0 и 2, где Y не двигался,
    против 15 шагов на курсах 1 и 3, 14 поворотов и 2 упоров как контроля. Условию «меняется
    только на шагах поперёк известной оси» удовлетворил РОВНО ОДИН байт из 258 048.

    Подтверждение сильнее самого поиска: человек трижды обошёл квадратную комнату по часовой,
    и (X, Y, курс) трижды вернулись к тем же значениям -- цикл из 10 нажатий, клетки X 12..14,
    Y 1..2. Замкнутый обход не подделаешь совпадением.

    ⚠️ `left` крутит курс на +1 по кругу (2→3→0→1→2), а НЕ «вниз».
    """
    if len(st) <= FACING:
        return None
    f = st[FACING]
    return {"x": st[X_AXIS], "y": st[Y_AXIS], "facing": f,
            "pos": st[Y_AXIS],                       # старое имя, чтобы не рвать вызовы
            "ahead": STEP.get(f)}


def vram_delta(a, b):
    """Bytes differing in the VRAM planes between two snapshots. ~0.01 ms.

    This is the "has the game finished drawing?" question, and it is a different question
    from "have the pixels stopped changing". During a scene load the pixels are perfectly
    still because nothing is being drawn YET -- screenshot then and the model reasons about
    a half-composed image (PC-98 draws plane by plane, so a partial frame has visibly wrong
    colours). While the game is composing, it writes to the planes; when it stops, the
    frame is finished.
    """
    x = np.frombuffer(a[VRAM_LO:VRAM_HI], np.uint8)
    y = np.frombuffer(b[VRAM_LO:VRAM_HI], np.uint8)
    return int(np.count_nonzero(x != y))


if __name__ == "__main__":
    import sys, time
    path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "game_start.state"
    st = path.read_bytes()
    print(f"{len(SCRIPT_BYTES)} scripts indexed, {len(_FP)} distinct fingerprints")
    print(f"state: {path.name}  {len(st)/1e6:.1f} MB")
    t = time.perf_counter(); info = identify(st); fast = (time.perf_counter() - t) * 1000
    print(f"identify(): {fast:.2f} ms  {info}")
    t = time.perf_counter(); r = resident(st); slow = (time.perf_counter() - t) * 1000
    print(f"resident(): {slow:.1f} ms  {[n for _, n in r]} at {[hex(b) for b, _ in r]}")
    # walk() больше не питает identify(), но осталась как ПРОБА раскладки: расхождение с
    # resident() означает, что правило «слоты встык» на этом образе не работает. Это
    # информация, а не провал -- ровно так и обнаружилось смещение 0xC150 -> 0xC170.
    w, truncated = walk(st)
    if [n for _, n in w] != [n for _, n in r]:
        print(f"   ⚠️ арифметика слотов расходится с байтами: walk()={[n for _, n in w]}"
              f" at {[hex(b) for b, _ in w]}")
    ok = info["scene"] is not None and r == sorted(r)
    print("SELF-TEST:", "PASS" if ok else "FAIL -- ничего не опознано в памяти")

# --- КАРТА ЭТАЖА ИЗ ПАМЯТИ -------------------------------------------------------------
# Этаж не нужно нащупывать шагами: игра держит его целиком. На образе лежат `FL0..FL12.MP3`
# по 906 байт (расширение обманчиво, это данные 1993 года, не звук), и файл этажа грузится
# в память ЦЕЛИКОМ -- в стартовом состоянии `FL5.MP3` найден по смещению 0x15DB0 побайтно.
#
# Формат вскрыт без единого подгоняемого параметра. Заголовок 4 байта -- `0f 00 0f 00`,
# то есть 15x15. Дальше 225 клеток по 4 байта:
#   байт 0 = (юг << 4) | запад      байт 2 = номер события (лестница, триггер), обычно 0
#   байт 1 = (север << 4) | восток  байт 3 = флаг 0/1
# Ниббл: 0 -- открыто, 3 -- проём или дверь, 1 -- ещё какой-то проход (14 штук на этаж),
# 9 -- стена.
#
# ⚠️ Как это доказано, потому что подгонкой такое доказать нельзя. Общее ребро записано
# ДВАЖДЫ -- у клетки и у соседа. Если разбор верен, обе записи обязаны совпадать всегда:
# «ниббл 0 клетки == ниббл 2 южного соседа» дало 210 из 210, «ниббл 3 == ниббл 1 восточного»
# тоже 210 из 210. После этого разбор сверили с 38 измерениями, набитыми ногами живым
# агентом: 38 из 38 при правиле «проход <=> ниббл != 9».
MAP_ADDR = 0x15DF0
MAP_W = MAP_H = 15
MAP_BYTES = 4 + MAP_W * MAP_H * 4
# сторона (компас STEP) -> номер ниббла: 0=+x восток, 1=-y север, 2=-x запад, 3=+y юг
SIDE_NIB = {0: 3, 1: 2, 2: 1, 3: 0}
WALL = 9


def _map_nibbles(block, x, y):
    o = 4 + (y * MAP_W + x) * 4
    b0, b1 = block[o], block[o + 1]
    return [b0 >> 4, b0 & 15, b1 >> 4, b1 & 15]


# ⚠️ Совпадение рёбер -- почти инвариант, но НЕ абсолютный: у формата есть законная
# асимметрия, односторонние переходы. Измерено по всем 16 файлам карт (WW/FL*.MP3,
# по 906 б): 36 расхождений на 6720 рёбер, то есть 0.5 %, максимум 8 на этаж. Виды:
# (1,9) 32 раза, (3,9) 3, (0,1) 1 -- то есть «проход с одной стороны, стена с другой».
# Требование абсолютного совпадения браковало 10 настоящих карт из 16, и floormap()
# на этих этажах уходил искать блок по всей памяти вместо того, чтобы взять свой.
# Проверка остаётся сильной: значений нибблов всего четыре, и случайный блок не даст
# 97 % совпавших рёбер из 420 ни при каких обстоятельствах.
MAP_MAX_ASYM = 12          # 3 % рёбер; наблюдаемый максимум 8


def map_asymmetry(block):
    """Сколько рёбер записаны у соседей по-разному. -1 -- это вообще не блок карты."""
    if len(block) < MAP_BYTES or block[0:4] != b"\x0f\x00\x0f\x00":
        return -1
    bad = 0
    for y in range(MAP_H):
        for x in range(MAP_W):
            n = _map_nibbles(block, x, y)
            if y + 1 < MAP_H and n[0] != _map_nibbles(block, x, y + 1)[2]:
                bad += 1
            if x + 1 < MAP_W and n[3] != _map_nibbles(block, x + 1, y)[1]:
                bad += 1
    return bad


def _map_consistent(block):
    """Блок похож на карту этажа: рёбра сходятся, кроме горстки односторонних переходов."""
    bad = map_asymmetry(block)
    return 0 <= bad <= MAP_MAX_ASYM


def floormap(snap, addr=MAP_ADDR):
    """Карта текущего этажа из памяти или None.

    Сначала смотрим по известному адресу; если там не карта -- ищем по заголовку и проверяем
    инвариантом. Проверка не формальность: случайный блок его не проходит, так что найденное
    либо карта, либо ничего.
    """
    block = bytes(snap[addr:addr + MAP_BYTES])
    if not _map_consistent(block):
        block = None
        start = 0
        blob = bytes(snap)
        while True:
            i = blob.find(b"\x0f\x00\x0f\x00", start)
            if i < 0:
                return None
            cand = blob[i:i + MAP_BYTES]
            if _map_consistent(cand):
                block, addr = cand, i
                break
            start = i + 1
    cells = {}
    for y in range(MAP_H):
        for x in range(MAP_W):
            o = 4 + (y * MAP_W + x) * 4
            n = _map_nibbles(block, x, y)
            cells[(x, y)] = {"sides": {s: n[SIDE_NIB[s]] for s in (0, 1, 2, 3)},
                             "event": block[o + 2], "flag": block[o + 3]}
    return {"addr": addr, "w": MAP_W, "h": MAP_H, "cells": cells,
            "sig": hashlib.blake2b(block, digest_size=8).hexdigest()}


def map_side(fm, x, y, side):
    """'open' | 'wall' -- то, что говорит САМА ИГРА про эту сторону."""
    c = (fm or {}).get("cells", {}).get((x, y))
    if not c:
        return None
    return "wall" if c["sides"][side] == WALL else "open"


# --- охота на здоровье ------------------------------------------------------------------
# HP не нашлись ни рядом с блоком игрока, ни поиском пары 16-битных «12»: во всех 305 ходах,
# где статус-панель попадала в текст, здоровье было 0012/0012, а различать одинаковые числа
# нечем. Значит нужен момент, когда HP ОТЛИЧАЮТСЯ от максимума -- то есть бой. Тогда пара
# (текущее, максимум) становится приметной, и пересечение кандидатов по нескольким таким
# моментам оставляет один адрес. Это тот же приём, которым нашли координаты.
def hp_candidates(snap, cur, mx, span=2):
    """Смещения, где рядом лежат `cur` и `mx` как 16-битные слова."""
    import struct
    out = []
    blob = bytes(snap)
    needle = struct.pack("<H", int(cur))
    i = blob.find(needle)
    while i >= 0:
        for gap in range(0, (span + 1) * 2, 2):
            j = i + 2 + gap
            if j + 2 <= len(blob) and struct.unpack_from("<H", blob, j)[0] == int(mx):
                out.append(i)
                break
        i = blob.find(needle, i + 1)
        if len(out) > 4000:
            break
    return out


# --- состояние игры по раскладке, прочитанной из её собственных скриптов ------------------
# ⚠️ Всё ниже выведено не поиском по значениям, а ЧТЕНИЕМ СКРИПТОВ, которые это рисуют и
# задают, и подтверждено замером (emu/signal_audit.py, STATUS.md §18):
#
#   файл сохранения FLAG0..4 (3072 б) -- это ПРЯМОЙ снимок памяти с SAVE_BASE. Поэтому один
#   декодер читает и живую игру, и сейв на диске (`saved()`).
#   +0x000  имя текущей сцены строкой («floor05.mes») -- без 118-мс скана иголками identify()
#   +0x020  регистры (: N) скриптов -- ПОЛУБАЙТЫ, младший первым: регистр N в полубайте N.
#           Потому все прошлые поиски предметов байтами и словами пустели; PARA.MES задаёт
#           новой игре 900=8 901=2 902=0 903=1, и во всём снимке этот узор один.
#   +0x31e  массив M = блок игрока (stats выше), снаряжение M15/M17/M18/M19.
SAVE_BASE = 0x175f0
SAVE_SIZE = 3072
REG_BASE = SAVE_BASE + 0x20
ITEM_REGS = {900: 'Heal Herb', 901: 'Stamina Herb', 902: 'Gold Bar', 903: 'Ascension Stone'}
EQUIP_SLOTS = {'weapon': 15, 'armor': 17, 'helm': 18, 'shield': 19}


def _equip_names():
    """Названия снаряжения -- из того же скрипта, что рисует панель (START.MES), а не руками."""
    import re
    src = HERE.parent / 'en' / 'START.MES.rkt'
    names = {}
    try:
        for m, v, name in re.findall(r'\(if \(== \(~ M (\d+)\) (\d+)\) \(<> \(str " ([^"]+?) *"\)\)\)',
                                     src.read_text(encoding='utf-8')):
            names.setdefault(int(m), {})[int(v)] = name.strip()
    except OSError:
        pass
    return names


EQUIP_NAMES = _equip_names()


def scene_name(snap, base=SAVE_BASE):
    """Имя сцены, которую игра сама считает текущей: строка в начале области сейва."""
    raw = bytes(snap[base:base + 13]).split(b'\0')[0]
    try:
        return raw.decode('ascii').upper() or None
    except UnicodeDecodeError:
        return None


def reg(snap, n, base=REG_BASE):
    """Регистр (: n) скриптов -- полубайт, младший первым."""
    b = snap[base + n // 2]
    return (b >> 4) if n % 2 else (b & 15)


def items(snap, base=REG_BASE):
    return {name: reg(snap, n, base) for n, name in ITEM_REGS.items()}


def equipment(snap):
    import struct
    out = {}
    for slot, m in EQUIP_SLOTS.items():
        v = struct.unpack_from('<H', snap, M_BASE + 2 * m)[0]
        out[slot] = EQUIP_NAMES.get(m, {}).get(v, f'#{v}')
    return out


def names(snap, base=SAVE_BASE):
    """Имена героев так, как их ввёл игрок: те же поля, что переписывает `savenames`."""
    import sys as _sys
    _sys.path.insert(0, str(HERE.parent / 'tools'))
    import savenames
    out = {}
    for off, default in savenames.NAME_SLOTS.items():
        got = savenames.decode(bytes(snap[base + off:base + off + savenames.NAME_FIELD]))
        out[default] = got or None
    return out


def saved(flag_bytes):
    """Прочитать файл сохранения FLAG* тем же декодером, что живую игру."""
    pad = bytes(SAVE_BASE) + bytes(flag_bytes) + bytes(0x1000)
    return {'scene': scene_name(pad), 'stats': stats(pad), 'items': items(pad),
            'equipment': equipment(pad)}
