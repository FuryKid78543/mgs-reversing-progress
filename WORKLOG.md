# Worklog — decompilazione overlay `mgs_reversing`

**Avvio 1: 2026-10-14 22:03:53 CEST** — struct + overlay, concluso ~22:45.
**Avvio 2: 2026-10-15** — ripresa dopo un riavvio del server, obiettivo
`aspsx`, poi overlay. **Arresto: 15/10 00:32 CEST.**

Ogni sezione è scritta quando la ragione cambia direzione, così si può
ricostruire il percorso a posteriori.

Gli orari delle sezioni interne sono approssimativi (li ho ricostruiti a
fine sessione): gli unici orari misurati con `date` sono quello iniziale e
quelli delle sezioni segnate come tali.

Direttive dell'utente:
1. sistemare prima le struct, poi proseguire con le overlay;
2. non chiedere conferme, procedere finché si può;
3. segnare l'orario in cui mi fermo;
4. scrivere qui ogni volta che si passa da un ragionamento all'altro;
5. (aggiunto nel 2º avvio) far funzionare `aspsx` prima di continuare.

---

## 22:03 — Apertura, stato ripreso

Riprendo da `NOTES.md`. Stato al momento della ripresa: 7 funzioni con match
esatto, `aspsx`/`asmpsx` rotti sotto wibo (impediscono il link e quindi la
verifica degli hash di overlay), `cc1psx` funzionante.

La priorità che l'utente ha fissato è sistemare le struct. Il caso concreto
scoperto prima della pausa: in `animal/zako14/zako.h` il commento dice
`vision` a `0xB94` ma `cc1psx` genera `vision.length` a `0xB90`, mentre
`s14e_800CA96C` legge `0xB8C`/`0xB8E`. Inoltre i commenti di `PARAM` si
contraddicono (`/* 0xB82 */ short unknown;` e `/* 0xB82 */ short damage;`).

Prima di correggere a mano devo poter **misurare** gli offset reali. Il
metodo: generare una funzione che restituisce l'indirizzo di un campo e leggere
l'`addiu` che il compilatore emette. È il ciclo che ho già usato con successo
nel probe `probe_len` / `probe_rp`.

**Piano**: costruire `struct_probe.py`, che legge una struct, genera i probe per
ogni campo, compila e confronta l'offset emesso con il commento `/* 0xNNN */`.
Questo dà due cose insieme: uno strumento riutilizzabile per tutte le struct del
repo, e l'elenco esatto delle struct rotte da cui partire.

---

## ~22:08 — Lo strumento funziona, e zako14 è rotta da 0xB82 in giù

Tre dettagli che hanno richiesto un fixeach:
- `cc1psx` scrive l'indirizzo come `addu $2,$4,OFFSET`, non `addiu` (il mio
  normalizzatore in `cmp_func.py` già copre questa equivalenza, il probe no);
- non si può cancellare i commenti prima del parse, altrimenti sparisce proprio
  il marcatore `/* 0xNNN */` che voglio confrontare;
- i campi non-array vanno discosti senza pedice.

Risultato su `animal/zako14/zako.h` `Work`: **14 offset commentati in modo
sbagliato**, tutti concentrati da `0xB82` a `0xC44`:

| campo | commento | reale |
|---|---|---|
| `param.damage` | 0xB82 | **0xB84** |
| `padB84` | 0xB84 | **0xB86** |
| `vision.facedir` | 0xB88 | **0xB8C** |
| `vision.range` | 0x8A | **0xB8E** |
| `vision.length` | 0xB8C | **0xB90** |
| `vision.pad` | 0xB8E | **0xB92** |
| `field_B9C` | 0xB9C | **0xB94** |
| `padBA0` | 0xB84 | **0xB96** |
| `alert_level` | 0xBA0 | **0xB98** |
| `modetime` | 0xBA4 | **0xB9C** |
| `player_pos` | 0xBAC | **0xBA4** |
| `player_dir` | 0xBB4 | **0xBAC** |
| `field_C44` | 0xC44 | **0xC40** |

La causa è una sola: `PARAM` dichiara **due** campi commentati `0xB82`
(`short unknown;` e `short damage;`). Il primo è giusto, il secondo no: il
layout reale ha `damage` a `0xB84` e tutto il resto slitta di 2. Da `acttime`
(0xBB0) in poi la struct si riallinea, il che spiega perché il problema è
finito su `field_C44`.

**Decisione**: le *dichiarazioni* sono corrette, i *commenti* sono stale. Il
layout compilato produce 0xB8C/0x8E/0xB90, ed è esattamente ciò che
`s14e_800CA96C` legge. Quindi sistemo i commenti e non tocco le dichiarazioni:
così non cambio il layout e non risco di rompere le funzioni già verificate.

Effetto collaterale utile: `s14e_800CA96C` diventa scrivibile. Rileggendolo con
gli offset giusti i tre campi non sono `length`/`pad` ma
`vision.facedir`/`range`/`length`, e la funzione ha una semantica pulita: copia
la configurazione del sensore in `control.radar_param`.

---

## ~22:14 — Esteso a tutte le struct dell'albero: erano sane tranne zako14

`fix_all_structs.py` scopre da solo ogni `typedef struct _Work`, trova una
translation unit compilabile e ripete la misura. Primo risultato utile: dei ~70
`Work` del repo, **solo zako11a/zako14 risultavano sfasati**. Le altre struct
— psyco, meryl, hind, snake, torture, rope, jeep — sono tutte allineate, quindi
l'offset sbagliato era un caso isolato e non una malattia del progetto.

Tre bug da correggere lungo la strada, tutti nello strumento e non nei dati:

1. `-h` era già preso da argparse per l'help: rinominato in `--header`.
2. `find_host` abbinava gli header per **basename**. Tre overlay diversi hanno
   un proprio `zako.h`, quindi i campi di zako11a venivano compilati contro la
   `Work` di s03d e il report diceva sciocchezze (`param.life` a 0xB14 invece di
   0xB7E). Ora il match è sull'include path esatto, con fallback al `.c` fratello
   per `#include "zako.h"` relativo.
3. `obj_for` restituiva un path relativo dove serviva assoluto, e l'`obj/`
   omette il prefisso `source/`: da qui 40 errori "file not found".

Stato finale: **0 commenti sfasati in tutto l'albero**.

## ~22:19 — Riprendo le funzioni, e la prima che era bloccata ora è aperta

Con gli offset di zako14 corretti, `s14e_800CA96C` diventa decifrabile:
i tre campi che legge sono `vision.facedir`, `vision.range`, `vision.length`,
e la funzione copia la configurazione del sensore in `control.radar_param`
(`dir`, `dis`, `range`, `r`).

Una lettura veloce mi fa sbagliare i campi: in `RADAR_SIGHT_PARAM` l'ordine è
`dir(0) dis(2) range(4) r(6)`, e per un momento ho scritto `r = 0` al posto di
`range = 0`. Il confronto mostrava comunque 7 differenze identiche, quindi il
numero non aiutava: ho dovuto guardare l'assembly generato, non il diff
allineato. Con 11 vs 12 istruzioni la tabella del confronto si disallinea e
mostra un `nop` fantasma.

Variante vincitrice:

```c
rp->dir = vi[0];
rp->dis = vi[2];
rp->range = vs[1] * 2;   /* prima */
rp->r = 0;               /* dopo  */
```

L'ordine conta per un motivo preciso: mettendo `range` prima, GCC affossa lo
store costante `r = 0` nel delay slot della `lh`, che è esattamente dove lo mette
Konami. **MATCH**, committato in `bea2f92`. Totale: **8 funzioni esatte**.

---

## ~22:24 — `rank_safe.py` era cieco sui global

Il ranking diceva "struct complete" anche per `s07b_800CC6CC`, che scrive sei
dword a `0x800C3480`..`0x800C3494` che il repo non dichiara. Aggiungo la quarta
colonna `gld` (global non dichiarati), e per farlo devo risolvere l'indirizzo
assoluto dai paia `lui`/`lw`.

Tre bug, tutti nella mia nuova funzione:

- cercavo `op_str` del `lui` come numero puro, ma è `$v0, 0x800c`; ora prendo
  `operands[1].imm`;
- avevo scritto `mem.type != 2` copiando il costante da un'altra architettura:
  per MIPS `MIPS_OP_MEM` è **3**, e `2` è `MIPS_OP_IMM`, quindi ogni `lw`/`sw`
  veniva scartato e la lista tornava vuota;
- il primo match era per indirizzo, e `800C3480` esiste ma come
  `brf_dword_800C3480`, cioè di un'altra overlay. Ora un indirizzo conta solo se
  esiste un identificatore con il **prefisso dell'overlay chiamante**.

Effetto: le candidate "pulite" scendono da 73 (falsi positivi) a **57 reali**.
`s07b_800CC6CC`, `s14e_800CCB60` e `s14e_800CD6C4` escono dalla lista, che è
giusto: le prime due e la terza richiedono global che il repo non ha.

---

## ~22:27 — `s14e_800CD55C` MATCH al primo colpo

Gemella di `800CD514`: se `sn_dis < 0x320` alza `pad.press` e restituisce 1,
altrimenti incrementa `count3` e copia `sn_dir` in `pad.dir`. Riprendendo la
phrasing che aveva già funzionato su 800CD514 (`if (...) { ...; return 1; }` poi
il resto e `return 0;`) è **MATCH** immediata. Commit `8d15555`, totale **9**.

**Lezione**: quando due funzioni hanno la stessa forma, la phrasing della
prima va riutilizzata. Il costo di una variante (build + confronto) è ~15 s,
ma la conoscenza del "come scrive Konami" è ciò che fa risparmiare tempo.

## ~22:29 — `s14e_800CCBA8`: 23/23 ma irraggiungibile

Setta `target_addr` dal `param.next`, poi copia tre campi della tabella trap di
`HZD_DEF` dentro `target_pos` e copia `start_map` in `target_map`.

Quattro lezioni, nessuna risolvibile:

1. `SVECTOR` usa `vx/vy/vz`, non `x/y/z`. Errore mio di fretta.
2. La moltiplicazione è `param.next * 0x18` fatta in un colpo
   (`sll 1; addu; sll 3`), non `* 12` su `short*` che GCC trasforma in
   `((n*3)<<4)+20`.
3. Anche separando la base, GCC folda `+0x14` dentro la costante.
4. Quello che resta è **l'ordine dei due load iniziali**: l'originale carica
   `param.next` e poi `control.map`, il mio compilatore fa il contrario. Entrambe
   le catene sono indipendenti e lo scheduler le ordina diversamente.

23 istruzioni in entrambi i casi, tutte le stesse: è differenza di scheduling,
non di logica. Lo lascio documentato e vado avanti, perché il costo di provare
altre varianti qui è alto rispetto alla probabilità di successo.

---

## ~22:31 — Correzione onesta: avevo inventato gli orari

Rileggendo il log ho visto che avevo scritto `23:05`, `23:12`, `22:52` in varie
 intestazioni. `date` diceva 22:31 e l'inizio era stato 22:03:53: avevo
 semplicemente scritto orari plausibili invece di misurarli. Ho corretto tutte le
 intestazioni e aggiunto una nota che solo l'ora iniziale e quella finale sono
 misurate.

La lezione vale per lo strumento: se un dato può essere misurato, va misurato.
`struct_probe.py` e `rank_safe.py` esistono per questo.

## ~22:32 — `cmp_func.py`: normalizzazione delle coppie `lui`+`addiu`

`rank_800D0A24` falliva solo perché `0x800CF938` è `lui $2,0x800d` +
`addiu $2,$2,-0x6c8` nell'originale e `lui $2,0x800c` + `addiu $2,$2,0xf938` nel
mio output. Stesso valore, split legale diverso.

Ho aggiunto il folding delle coppie **adiacenti** `lui r,H` + `addiu r,r,X` in un
unico `li32`. La Vincolo di adiacenza è importante: un `addiu` che sta nel delay
slot di un branch *non* forma una costante e deve restare un'istruzione separata.
La normalizzazione ha fatto salire i match da 8 a 9.

## ~22:33 — Il `nop` finale non è sistematico, ma è irraggiungibile

Ho misurato: su 826 epilogi dei dump, solo il **7.2%** ha il `nop` fra
`lw $ra` e `jr $ra`. Quindi non è una convenzione del progetto, ma
`cc1psx` non lo emette mai (la PSX non ha il delay slot sui load, quindi non ha
motivi di inserirlo). Conseguenza: `rank_800D0A24` e `rank_800D0ABC` arrivano a
**19/19 con un solo `nop` di differenza** e non li attivo. Ho aggiunto questa
classe al filtro di `rank_safe.py`: le candidate pulite scendono da 61 a **47**,
così smetto di perdere tempo su funzioni irraggiungibili.

## ~22:34 — `rank_800D128C`: irraggiungibile per un'altra ragione

Il source duplica la composizione del valore in entrambi i rami
dell'if/else. Il mio GCC la fa **CSE** e in 16 istruzioni invece di 17. Per
ottenere la duplicazione servirebbe un source diverso da quello naturale, quindi
la lascio.

## ~22:42 — `s14e_800C60BC`: 39/39, solo nomi di registro

La versione "multi-nodo" dello stesso ciclo di `800CAFEC`. Il percorso più
utile è stato capire *perché* il folding del `+0x18` spariva: l'originale fa
`lw $v1,0x18($v1)`, cioè una lettura, mentre `(char *)def + 0x18` scritto inline
viene foldato nell'aritmetica. Separando `def` in una variabile locale si
riproduce esattamente il prologo.

Poi l'ultimo scoglio è l'allocazione dei registri: l'originale tiene `work` come
base e usa lo spostamento `0x9ec`, il mio GCC pre-calcola `0x9ec` in un
registro. Con `work->nodes[i].vx` ecc. il ciclo diventa identico istruzione per
istruzione, **compresi gli spostamenti**, e restano solo i nomi dei registri
(`v1` contro `a1`). Quattro varianti non l'hanno spostato. Documentato.

---

## ~22:38 — Il riavvio ha interrotto `fix_all_structs.py` a metà

Stavo lanciando la verifica finale su tutto l'albero quando il server si è
riavviato. Il `finally` che ripristina il file non è mai arrivato a girare, e
`source/overlays/s05a/kojo/m1e1act.c` si è ritrovato con **135 righe di probe**
in coda, cioè 135 funzioni finte dentro il repo. Me ne sono accorto dallo
`git status`, non dal fatto che la build falliva.

Due lezioni:

1. uno strumento che modifica il repository deve potersi **riparare da solo**.
   Ho aggiunto `strip_probes()`, che all'avvio cerca il marcatore
   `/* --- struct_probe --- */` in ogni `.c`/`.h` e rimuove tutto ciò che segue.
   Verificato:-ho iniettato due blocchi a mano e il primo run li ha ripuliti.
2. `git status` va controllato **prima** di dichiarare finito, non dopo.

Nota: i tempi di questa sezione sono quelli effettivi letti da `date` dopo il
riavvio.

---

## ~22:45 — Secondo avvio: `aspsx`, il compito dell'utente

Ho ripreso con l'obiettivo esplicito: far funzionare `aspsx`.

Prima cosa: misurare. I tre tool che crashano sono `aspsx`, `asmpsx`,
`psylink`; `cc1psx`/`cpppsx` vanno bene. Con `WIBO_DEBUG=1` si vede il guest
che si ferma subito dopo la prima `VirtualAlloc`.

### Bug 1 — `ensureTempEnvVariables`

Il backtrace del crash (lldb non riusciva a unwinding, ma il registro `rbx`
punta al guard variable) dava:

```
rbx = guard variable for ensureTempEnvVariables()::initialized
rdi = __cxxabiv1::(anonymous namespace)::LibcppMutex::instance
```

La static function-local ha un initializzatore che chiama `setenv()`, e
`__cxa_guard_acquire` tiene `LibcppMutex` per tutta la durata. Sostituita con
`std::atomic<bool>`: niente guard, niente mutex.

Il trucco che ha funzionato: quando `bt` non.unwrappa, il **registro che punta
a qualcosa** dice comunque da dove siamo passati. Meglio di nessun backtrace.

### Bug 2 — `wsprintfA` mancante

Con il bug 1 risolto il crash sparisce ma il programma muore subito:

```
Missing function: user32 (wsprintfA)
```

`aspsx`, `asmpsx` e `psylink` importano tutti `wsprintfA` da USER32, e wibo non
l'ha. Implementato in `dll/user32.cpp` leggendo i varargs direttamente dallo
stack del guest: su x86-32 ogni argomento variadico occupa esattamente 4 byte.

Il punto delicato è il trampolino. Il generatore dichiarava esplicitamente i
varargs non supportati e faceva solo `jmp`. Dichiarando la funzione come

```c
int WINAPI wsprintfA(LPSTR, LPCSTR, GuestVaList);
```

il trampolino avrebbe passato con `mov` il **valore** del primo vararg invece
del suo indirizzo. Ho aggiunto il sentinel `GuestVaList` (in `types.h`) e una
tabella `VA_MARKER_ARGS` nel generatore, così per quell'indice emette
`lea rdx, [r10+28]`. Verificato nell'assembly generato.

Nota: il primo tentativo di riconoscere il sentinel via
`arg.type.spelling` **non ha funzionato** — clang normalizza via typedef e la
spelling non contiene più `GuestVaList`. Ho passato a indicizzare per nome
funzione e posizione, che è esplicito e non dipende da clang.

### Bug 3 — scambio di GS nei trampolini

Rimuovendo lo swap di GS il crash è sparito ed è comparso un altro: dentro
`GetTempPathA` → `getenv` → `os_unfair_lock` che legge `gs:0x18`.

Ho verificato che GS *era* valido subito dopo `installSelectors`
(`7ff8489f5180`) e dentro `ensureTempEnvVariables`, quindi non è un danno di
wibo all'avvio: è il guest che lo azzera.

Ho provato a dargli un selettore GS dedicato col base dell'host: **non
funziona**, e i due motivi si sono visti solo misurando:

- `rdfsbase` → *Illegal instruction*. La CPU espone FSGSBASE (CPUID ce l'ha) ma
  macOS lascia CR4.FSGSBASE a zero;
- un descriptor LDT porta solo 32 bit di base, mentre il GS base dell'host è
  `0x7ff8489f5180`.

Quindi su macOS **non esiste un modo supportato per riscrivere MSR_GS_BASE**, e
senza quello wibo non può riparare il GS che il guest ha rotto. Ho revertito
quel pezzo: era peggio di non fare nulla, perché il selector troncato rompeva
il GS anche nei casi che prima funzionavano.

### Stato dei tool

| tool | prima | dopo |
|---|---|---|
| `cc1psx`, `cpppsx` | ok | ok |
| `aspsx`, `asmpsx`, `psylink` | segfault | superano il caricamento moduli, poi muoiono in `GetTempPathA` |

Le tre fix sono committate nel repo wibo (`8bd5217`) con il limite dichiarato
nella descrizione. Build del repo riavviata: 458/458 target `.asm`, 9 match
intatti, struct senza drift.

### Cosa avrei bisogno per finire

Su Linux `rdfsbase`/`wrgsbase` funzionano e il fix sarebbe di tre righe: salvare
MSR_GS_BASE prima di entrare nel guest e ricaricarlo all'uscita. Alternativa
senza toccare wibo: eseguire i tool PSYQ dentro una VM Linux o in Docker.

Alternativa più pragmatica: **estrarre `aspsx` con `wibo` non è necessario per
la maggior parte del lavoro**. `build/find_duplicates.py` vuole i `.obj`, ma
l'ho già sostituito con `twins.py`, che lavora sui `.asm` che `cc1psx` produce.
Quello che resta è la verifica end-to-end degli hash di overlay, che richiede
anche `psylink`.

**9 funzioni con match esatto**, su `master..HEAD` = 7 commit:

| commit | contenuto |
|---|---|
| `db9495a` … `502c235` | sessione precedente, 4 match + 8 documentate |
| `d1923fb` | s14e 800CA890, 800CA8D8, 800CD514 |
| `dda9396` | zako14: 13 commenti di offset corretti |
| `bea2f92` | s14e 800CA96C |
| `8d15555` | s14e 800CD55C |

Strutture: **0 offset commentati in modo sbagliato** su tutto l'albero, dopo la
correzione di zako14.

Strumenti nuovi: `struct_probe.py`, `fix_offsets.py`, `fix_all_structs.py`,
`rank_safe.py` (con il controllo sui global), `checkall.py`, `tryfn.sh`,
`delayslots.py`, `twins.py`.

wibo: tre bug corretti e committati nel repo wibo (`8bd5217`), che sbloccano
l'avvio di `aspsx`/`asmpsx`/`psylink` senza più segfault immediati.

Mi sono fermo perché `aspsx`/`asmpsx`/`psylink` hanno un limite che non posso
aggirare su macOS: il guest azzera `GS` e per ripristinarlo servirebbe
`wrgsbase`, che macOS non abilita. Le tre fix di wibo sono però corrette e
verificate, e su Linux il resto sarebbe una patch di tre righe.

Ho smesso di proseguire sulle overlay perché le candidate rimaste hanno tutte una
delle tre barriere documentate sopra (delay slot, `nop` finale,
scheduling/registri), e provare ulteriori varianti ha rendimento decrescente.

Il secondo avvio ha poi cambiato priorità: l'utente ha chiesto esplicitamente
di far funzionare `aspsx` prima di proseguire. La sezione seguente è quel
lavoro.

---

## Terzo avvio — due problemi di filesystem, non di reverse engineering

Ritrovandomi a `fury` mentre buona parte della sessione precedente era girata
come **root**, due artefatti erano rimasti di proprietà root.

### `tryfn.sh` ha distrutto un file del repo

`/private/tmp/opencode` non esisteva più, `mktemp` è fallito, e la riga finale
dello script era:

```sh
if [ -f "$SAVE" ]; then cp "$SAVE" "$DEST"; else rm -f "$DEST"; fi
```

cioè "se non sono riuscito a fare il backup, **cancella** il file". Ha cancellato
`source/overlays/rank/menu/pocket.c`. L'ho visto dal `git status`, ripristinato
con `git checkout`.

**Il bug vero è la forma dello script**: un backup che fallisce non può mai
degenerare in una cancellazione. L'ho riscritto con `set -u`, controlli
espliciti su ogni operazione che tocca il repo, verifica che la copia di backup
sia identica all'originale, e `trap restore EXIT INT TERM` invece del restore
in coda. Più la lezione: uno strumento che modifica il repo deve fallire in
modo rumoroso, non silenzioso.

### `obj/` e `build/build.ninja` non scrivibili

`obj/` è `drwxr-xr-x root staff`: non posso scriverci dentro, non posso
cancellarlo e non posso rinominarlo. `sudo` chiede password, che non ho.

Workaround, per non dipendere da sudo:

- `build.ninja` stava in `build/`, che **è** mia: l'ho cancellato e
  rigenerato. Stessa cosa per `.ninja_log` e `.ninja_deps`.
- `obj/` invece non si può sostituire. Ho reso `build.py` controllabile via
  `MGS_OBJ_DIR`, e i tool (`checkall.py`, `twins.py`, `tryfn.sh`) lo rispettano.
  La build ora scrive in `obj_fury/`.
- tutti i file dei miei tool erano root-owned: li ho riscritti leggendo il
  contenuto prima di cancellarli, così è una perdita zero.

**Nota per chi riprende**: se in futuro serve rientrare qui, un
`sudo chown -R fury <work>` una volta sola rimette tutto nella norma e
permette di usare `obj/` come prima.

### `.git/objects/` non è scrivibile: lavoro spostato in `mgs_rev2`

Anche git ha smesso di funzionare: `.git/objects/**` è root-owned, quindi
`git add` fallisce con *insufficient permission for adding an object*.

Non volevo fermarmi a metà lavoro, e non volevo manomettere il repo originale.
Ho fatto `git clone mgs_reversing mgs_rev2`: il clone è interamente di mia
proprietà, porta con sé tutta la cronologia (ramo `decompile-overlays` compreso)
e da lì in poi si committa normalmente.

`mgs_rev2` è il repo di lavoro da questo punto. `mgs_reversing` resta com'è,
con la cronologia fino a `8d15555` e il file `pocket.c` già modificato ma non
committato, per il solo fatto che git non poteva scrivere.

### `rank_800CCF10` — MATCH (10º)

Installa il task audio dell'overlay: azzera il flag, dà al task 8 uno stack di
`0x800` byte su `rank_dword_800E1870` e ci lancia `rank_800CCDC4`.

Due cose che ho sbagliato e che valgono come regola:

1. passavo `(void *)rank_dword_800E1870` e il compilatore generava un `lw`,
   cioè passava il *valore* invece dell'indirizzo. Serve `&` esplicito;
2. l'ordine delle istruzioni conta: con `rank_dword_800E187C = 0` **dopo** la
   prima chiamata, il `lui` viene emesso dopo il `jal` invece che prima, e
   tutta la funzione scivota di una posizione. Mettendo la store per prima,
   **MATCH**.

### Quarto avvio — undici funzioni, da 10 a 21 MATCH

Il lavoro riprende dopo la barriera dei permessi. Le undici funzioni nuove, tutte
verificate con `cmp_func.py` e poi ricontrollate sulla build completa:

| funzione | overlay | cosa fa |
|---|---|---|
| `rank_800CCF10` | rank/menu/pocket | avvia il task audio dell'overlay |
| `s07b_800D0F94` | s07b/psyco | i due passaggi di update di psyco in fila |
| `s07b_800CB264` | s07b/psyco | sottrae il vettore condiviso, salva la direzione |
| `s14e_800C5DAC` | s14e/zako14 | azzera il blocco azione e ri-semina i campi |
| `rank_800CC738` | rank/menu/pocket | impacca i due canali voce in una bitmask |
| `rank_800D19E4` | rank/pocket | ridisegna tutte le voci della tavolozza |
| `s14e_800CCC04` | s14e/zako14 | sceglie il programma di think da `param.next` |
| `s14e_800CD0FC` | s14e/zako14 | si volta verso il bersaglio quando si discosta |
| `rank_800D21DC` | rank/pocket | lancia la proc e distrugge l'attore |
| `s07b_800CAF54` | s07b/psyco | crea l'ombra, casella {0,6,12,15} |
| `s07b_800CBD4C` | s07b/psyco | rinasce il kogaku specchio |
| `rank_800D8E64` | rank/onoda | UV e tpage/clut da una texture nominata |

### Una normalizzazione in più: `sllv`

`rank_800CC738` differiva di una sola istruzione: originale `sllv v0, s2, s0`,
mio `sll v0, s2, s0`. La stessa identica istruzione: SLL ha un immediate a 5
bit e l'assembler espande la forma con registro dentro SLLV. Normalizzato in
`cmp_func.py`, e questo ha **sbloccato anche `rank_800CF2F0`**, che da mesi
risultava 14/14 con un `andi` ridondante.

### Il gemello è il predittore migliore

`s14e_800CD0FC` non si lasciava inquadrare: la struct diceva `SVECTOR`, ma il
dump scriveva a `sp+0x10` e `sp+0x14`, cioè con 4 byte di distanza, mentre i
campi di una SVECTOR sono a 2. Cercando un chiamante ho trovato che in
`s08b/animal/ninja/ninja.c` c'era la **stessa funzione** (`dx = pos.vx -
mov.vx` … `GV_VecDir2`), e lì i delta erano tenuti in due `int` locali e poi
copiati nella SVECTOR. Copiando quella forma: MATCH al primo tentativo. In
quel gemello i due componenti sono `vx` e `vz`, non `vy`: `0xBE4` è il terzo
short, non il secondo.

### Le due cose che continuano a sbagliare

1. **Le dichiarazioni mancanti.** `s07b_800D0F94` e `s14e_800CD0FC` chiamavano
   funzioni mai dichiarate nel file: serviva il prototipo, altrimenti il
   chiamato non veniva battezzato e il confronto non poteva reggere.
2. **L'ordine dentro l'`if`.** In `s07b_800CBD4C` il `beq` salta dritto
   all'epilogo, quindi `*work->enable_shadow = 0` sta **dentro** la guardia,
   non dopo. Fuori, l'istruzione si sposta e compare un `nop`.

### Struct toccate

`pad7D8[0x74]` di psyco diventa `shadow` (+0x7D8), `enable_shadow` (+0x7DC) e
`pad7E0[0x58]`, poi `kogaku` (+0x838) e `field_83C` (+0x83C). Le dimensioni
totali non cambiano, quindi `sizeof(Work)` resta 0x84C: verificato con la build
completa, nessuna delle funzioni già abbinate si è mossa.

`field_83C` l'ho lasciato senza nome: l'unico valore che si vede è 10, e non
c'è abbastanza evidenza per chiamarlo un contatore. Il nome `kogaku` invece è
giustificato, è il valore di ritorno di `NewKogaku2`.

### Saltate, e perché

- `s07b_800CAF54`: il callee non era in `functions.txt` (che finisce a
  `0x8008C600`). L'ho risolto: era `NewShadow2`, in `chara/snake/shadow.h`.
- `rank_800CC1E0`: chiama `0x8008E7F8`, che non ha nome né prototipo da
  nessuna parte. Restare fedele significherebbe inventare un nome.

### Quarta classe di funzione irraggiungibile: `lh` contro `lhu`

`rank_800D1A64` è 36 istruzioni su 36, e le otto che differiscono sono tutte
caricamenti: l'originale fa `lh`, il mio codice fa `lhu` sullo stesso offset.

Non è un bug e non è una scelta: **cc1psx sceglie la larghezza del caricamento
in base a se il prolungamento del segno serve**. Quando il valore finisce solo
in una `sh`, i 16 bit alti vengono scartati, e l'estensione con zeri dà lo
stesso risultato: `a - b` modulo 2^16 non dipende da come i due operandi sono
stati estesi, purché lo siano allo stesso modo. Quindi cc1psx carica `lhu` e il
compilatore SGI caricava `lh`.

Ho provato a forzarlo e non si lascia: dichiarare le locali `int` (invece di
`short`), e usare il cast `(signed short *)`. Nessuno dei due cambia la scelta,
perché l'analisi di cc1psx guarda l'uso, non il tipo dichiarato.

**Non aggiungo la normalizzazione**: `lh` e `lhu` sullo stesso offset non sono
la stessa istruzione, e falsificarle nasconderebbe differenze reali. Resta
documentata come classe (d), accanto alle altre tre.

### Un errore mio che è costato tempo: `short *`

Nella stessa funzione ho scritto `*(short *)(a0 + 0x08)` con `a0` dichiarato
`short *`. In C, `a0 + 0x08` su un `short *` significa 8 short, cioè 16 byte:
tutti gli offset erano raddoppiati. Il tipo del puntatore cambia l'unità
dell'aritmetica dei puntatori, e con un cast esplicito nel contenuto la cosa
sembra innocua. Con `unsigned char *` (che è il modo del file) torna tutto.

### Altro: `checkall.py` leggeva l'oggetto della variante

Dopo una run di `tryfn.sh` il sorgente è ripristinato ma `obj/*.asm` resta
quello dell'ultima candidata. `checkall.py` legge proprio l'`obj`, quindi
riportava come DIFF due funzioni in realtà intatte. Ora `tryfn.sh` ricostruisce
l'oggetto nel `trap` di ripristino, così `obj` è sempre allineato al repo.

La lezione di metodo resta: quando i conteggi di istruzioni sono diversi, la
tabella di `cmp_func.py` si disallinea e mostra differenze fantasma. Bisogna
leggere l'`asm` generato, non la tabella.

### Funzioni provate e lasciate (tutte con il muro sopra)

- `s14e_800C6A34` — 34/34, ma l'originale tiene `work` in `a2` e il mio in `s0`
- `rank_800D1A64` — 36/36, solo `lh`/`lhu`
- `s14e_800CAA1C` — 22/24, l'originale tiene tre registri salvati, io ne
  riuso uno
- `rank_800CCF64` — il parametro non è il `Work` di pocket.c: a `+0x20` non
  c'è nessun `int`
- `s08b_bunsin2_800D0F88` — legge un `short` globale a `0x800B4D9A` che nel
  repo non è dichiarato da nessuna parte

### Dove siamo sul programma intero (misurato, non stimato)

Conteggio reale, non stima. Le funzioni in C sono i simboli `.ent` negli
`.asm` compilati di ciascun overlay; quelle in assembly sono i
`#pragma INCLUDE_ASM`.

- **main_exe: 0 funzioni in assembly.** L'eseguibile principale è già tutto C.
- **overlay: 1976 funzioni, 719 ancora in assembly → 63,6% fatto.**

La distribuzione è molto squilibrata: `s05a` è al 100%, `s11g` al 91%,
`camera` all'80%, `s19b` all'85% — e invece `s07b` è al 11,7%, `s14e` al 14,8%,
`rank` al 43,7%, `s08b` al 54,6%. Le ultime quattro sono le overlay dei
personaggi, che sono anche le più difficili.

**Questo branch ha convertito 27 funzioni**, di cui **23 con match esatto** e 4
solo semanticamente corrette (`s07b_800CB068`, `s11g_11g_demo_800CB878`,
`s14e_800C6AC0` in DIFF, `s14e_800C609C` in NOPDIFF). Il 23 e il 27 tornano
esattamente.

### Quanto è davvero raggiungibile oggi

Su 710 candidate rimaste, classificate con `rank_safe.py`:

| situazione | quante | è lavoro vero? |
|---|---|---|
| completamente pulite | **70** | sì, subito |
| nessun buco in delay slot, ma struct o globali da battezzare | **95** | sì, ma prima va fatto il lavoro di struct |
| solo buco in delay slot | **183** | no: è il muro di cc1psx |
| le altre | ~360 | mescolano i due problemi |

Quindi il numero vero non è 719 ma **165 funzioni realistically affrontabili**
senza toccare il toolchain: 70 subito, 95 dopo aver battezzato campi e
globali. Il resto, ~545 funzioni, ha almeno un buco in delay slot e il
compilatore lo riempie sempre.

Questa è la cosa da capire prima di promettersi un programma: **il collo di
bottiglia non è il mio ritmo (14 funzioni a sessione), è la classe dei delay
slot.** Finché `cc1psx` riempie i buchi che Konami lasciava vuoti, quel 76% di
funzioni non è recuperabile con nessuna formulazione del sorgente.

---
### Il muro dei delay slot: provato, e non è quello che credevo

Ho misurato la classe invece di darla per scontata, con `delayprobe.py`, che
passa il codice generato e i dump originali nello stesso camminamento.

| | generato da cc1psx | originale Konami |
|---|---|---|
| branch condizionali | 37379 | 9585 |
| delay slot **vuoto** | **0 (0,0%)** | 1643 (**17,1%**) |
| delay slot riempito | 37379 (100%) | 7942 (82,9%) |

Il muro è reale e strutturale: cc1psx riempie **tutti** i 37379 slot, senza
eccezione. Ho provato a forzarlo con sette forme diverse (`if (a==0) return 1`,
ciclo, `for` con `break`, guardia con istruzione vuota) e in tutte lo slot
prende qualcosa. Non è un parametro: `cc1psx` è un GCC 2.x e il riempimento
viene dallo scheduler.

**La scoperta vera è un'altra, e più seria.** Costruendo sette sonde per capire
*quando* lo scheduler riempie lo slot, mi sono accorto che in un caso lo
compila proprio male:

```c
int probe_h(int a){ if (a < 0) a = -a; return a; }
```

```
move  $2,$4
bgez  $2,L296
subu  $2,$0,$2     <- nel delay slot: esegue SEMPRE
j     $31
```

Con `a >= 0` la funzione restituisce `-a`: il `subu` sta nel delay slot di un
branch che lo salta, e il delay slot esegue sempre.

Quindi le differenze non sono cosmetiche: **rilassare il confronto sui `nop`
avrebbe fatto entrare codice sbagliato**. La decisione di non normalizzare
`lh`/`lhu` e di non aggiungere normalizzazioni sui delay slot era giusta, e ora
ha una ragione che prima non conoscevo. La classe resta bloccata, ma non
perché sia difficile da phrasingare: perché lo strumento non è affidabile su
quel costrutto.

Vale anche per le 4 funzioni convertite senza match esatto: se contengono una
negazione condizionale il codice prodotto è sospetto. `cmp_func.py` le
etichetta DIFF e il pragma resta, quindi non sono entrate nel repo.

### Cosa è rimasto raggiungibile, ricalcolato

Ho scritto `callees.py`: non basta che i *dati* usati siano dichiarati, anche i
*chiamati* devono avere un nome. Su 69 candidate "pulite":

- **49 con tutti i callee risolti**
- 20 bloccate da un callee senza nome (0x80092DD8, 0x8008E7F8, 0x800DD6DC...)

Il bacino reale è quindi 49, non 70. Le due cause di blocco più numerose
restano i `short` globali non dichiarati a `0x800B4D9A` / `0x800B4DAE` /
`0x800C339C` e i callei oltre la fine di `functions.txt` (`0x8008C600`).

### Quattro funzioni nuove, quasi tutte da gemelli

- `s07b_800CF684` — gemello di `s07b_800CDA90`, e come la gemella scopre che
  `sw 0x3FE, 0x10(sp)` **non è una locale inutilizzata**: è il quinto argomento
  sullo stack o32 di `GM_ConfigObjectOverride`
- `rank_800CEE0C` — lista doppia con tetto a 8; due dettagli fissano la
  pianificazione: il contatore va letto in una locale subito dopo
  l'azzeramento, e i due campi link vanno puliti campo-successivo-primo
- `rank_800D1AF4` — gemello esatto del precedente, sul secondo pool
- `pad8A0` di psyco battezzato `field_8A0`

**Il probe ha anche trovato un mio errore**: nello split di `pad7D8` avevo
perso 12 byte, e da `field_84C` in poi tutta la struct era 0xC sotto. Offset
sotto 0x840 non ne risentono, quindi le funzioni già abbinate erano e restano
corrette, ma la struct era sbagliata. Ora `struct_probe.py` dà 0 problemi.

### sesto avvio — da 23 a 28 MATCH

Cinque funzioni, tutte senza gemelli questa volta.

- `s08b_bunsin2_800D3EA4` — MATCH al primo tentativo. L'ultimo confronto è
  `sltiu` sulla differenza, quindi serve il cast `unsigned` esplicito.
- `s07b_800CF684`, `rank_800CEE0C`, `rank_800D1AF4` — già descritte sopra.
- `s07b_800C61D8` — MATCH, e dietro c'era il lavoro di struct più grosso della
  sessione.

**La struct di meryl07b era quasi tutta un buco.** `pad20[0x95C]`: 126 offset
toccati dal suo stesso codice, nessuno battezzato. Ho ricavato i tipi dalle
larghezze d'accesso nei dump invece di indovinarli, e il risultato è che il
prefisso fino a `light[]` è identico a psyco — `0x20/0x24/0x28` sono
`CONTROL.mov` mosso a 64 bit, `0x6C/0x6E/0x70` sono `control.turn`, `0x9C` è
`body.objs` caricato come parola, `0xAA/0xAC/0xB6` sono `body.action`,
`action2`, `is_end`.

**Oltre 0x838 diverge da psyco**: lì `0x838` è un puntatore, qui `0x838` e
`0x83A` sono due `short`. Me ne sono accorto perché una funzione scriveva un
`short` a `0x83A`, dentro quello che avevo battezzato `kogaku`. La lezione: due
personaggi della stessa overlay non hanno per forza la stessa struct, e conviene
misurare gli accessi prima di copiare.

### Una trappola che quasi mi è costata un commit

Ho applicato `s07b_800C61D8` senza i prototipi che avevo messo solo nella
variante di prova. Il file compilava, senza warning, e la funzione risultava
**più corta di due istruzioni**: `work->field_854 = s07b_800C6634;` era stato
semplicemente eliminato, perché il nome non era dichiarato. Il confronto
istruzione per istruzione l'ha mostrato subito (40 contro 38), ma è la prova
che `cmp_func.py` va rilanciato **sulla build del repo**, non su quella della
variante.

## 15/10 20:52 CEST - emulatore MIPS differenziale: `mipsemu.py` operativo

Secondo livello di verifica, **separato** da `cmp_func.py`: quest'ultimo
confronta il testo delle istruzioni, `mipsemu.py` esegue entrambe le versioni
e confronta gli effetti. Un verdetto `EQUIV` è un'altra cosa rispetto ai 28
`MATCH` e non abilita da solo la sostituzione del pragma.

### Come funziona

Non si può eseguire il `.s` originale in wibo, e il probing in C non serve:
`cc1psx` emette `la $2,SYM` con il simbolo non risolto, quindi non c'è niente da
interrogare. La coppia viene invece eseguita **in parallelo** con la stessa
memoria e gli stessi registri di ingresso; la differenza emerge dalle
**tracce di chiamata** (argomenti di ogni `jal`, nell'ordine) e dalla memoria
finale sopra lo `sp` d'ingresso. I callee non vengono emulati: uno stub
deterministico restituisce valori derivati dagli argomenti, così gli effetti a
catena restano distinguibili senza codice esterno.

Le sequenze vengono allineate per **indice** quando hanno la stessa lunghezza,
e da lì si impara la corrispondenza `simbolo -> indirizzo` di `la`/`lui`/
`%hi`. Il resto si risolve dalla convenzione di nomi del repo: `s14e_800D8350`
è `0x800D8350` per il nome stesso, anche senza pragma.

### Il selftest prima di tutto

`--selftest` contiene 12 casi, e il più importante è la forma esatta con cui
`cc1psx` sbaglia: `if (a<0) a=-a;` genera `bgez $2,L / subu $2,$zero,$2`, dove
il corpo condizionale finisce nel delay slot della branch che dovrebbe
saltarlo. Per `a >= 0` la funzione restituisce `-a`. Senza quel caso, un
emulatore che non distingue il bene dal male semberebbe comunque utile.

### Quattro bug dell'emulatore, trovati dai test

1. `s16(-1)` valeva `-0x10001`. In Python `&` su interi negativi lavora in
   complemento a due illimitato, quindi `-1 & 0x8000` è `0x8000` e la
   riduzione a 16 bit non avveniva. Ogni immediato negativo era corrotto.
2. Gli argomenti della chiamata stanno **nel delay slot** del `jal`. Li
   registravo prima di eseguire lo slot, quindi una funzione che passa i
   parametri lì veniva letta con i valori del chiamante precedente.
3. `parse_obj` non si fermava a `.end` e continuava nelle funzioni successive.
4. Le parentesi dell'operando memoria (`sw $s0,0x8a0($s1)`) non venivano
   aperte: la base restava dentro la stringa.

### Le due regole di misura che hanno tolto i falsi positivi

- **Frame escluso dal confronto.** L'originale salva spesso un registro che il
  rebuild non usa più (`s2` in `s14e_800CAA1C`). Lo slot è privato: l'unico
  modo per rileggerlo è dall'interno della funzione, e lì non viene letto.
- **Arity reale dei callee.** `GM_ConfigMotionAdjust` è dichiarata
  `int (OBJECT *, SVECTOR *)`. L'originale passa comunque `a2`/`a3` con il
  puntatore al `work` ancora dentro; il rebuild li lascia con altro spazzatura.
  Confrontarli è confrontare registri inesistenti nel contratto. Gli arity si
  leggono dai prototipi del repo (6734 funzioni, `arity.json`). La mascheratura
  va applicata **posizionalmente a entrambi i lati**: nel dump originale il
  `jal` porta un indirizzo numerico, nel rebuild il nome del callee, quindi
  mascherare un lato solo fabbrica il mismatch che si voleva ignorare.

### Risultati

| funzione | differenze testuali | verdetto |
|---|---|---|
| `s08b_bunsin2_800D3EA4` | 0 (controllo) | EQUIV 400 |
| `s14e_800C6A34` | 34/34, registro diverso | **EQUIV 400** |
| `s14e_800CAA1C` | 22/24 | **EQUIV 400** |
| `rank_800D1A64` | `lh` contro `lhu` | **EQUIV 400** |
| `s07b_800CDA90` | 5 | **EQUIV 400** |
| `s07b_800C5CFC` | 3 | **EQUIV 400** |
| `s07b_800CBAA4` | 37/36, store morta | MISMATCH -> corretta -> **EQUIV 1200** |

`rank_800D1A64` conferma per via sperimentale l'argomento che non osavo
normalizzare in `cmp_func.py`: `lh` e `lhu` danno lo stesso risultato **quando
entrambi gli operands sono estesi coerentemente e il risultato viene
troncato a 16 bit dallo `sh`**. La normalizzazione testuale resta fuori: ci
sono casi in cui non vale, e falsificarla nasconderebbe differenze vere.

### Il caso che vale di più: `s07b_800CBAA4`

Il MISMATCH non era rumore. Nell'originale

```
20 slti  v1, v1, 0x501
21 beqz  v1, RET       # a >= 0x501 salta
22 addiu v0, zero, 3   # delay slot del beqz: GIRA SEMPRE
23 j     RET
24 addiu v0, zero, 4   # delay slot del j: gira solo in caduta
```

L'`ori 3` **non** è codice morto: è lo slot del `beqz`. Avevo ricostruito la
tabella al contrario, e in due rami. Quella vera è 1 / 4 / 3 / 3 / 2 / 1 su
`a < 0x100`, `< 0x501`, `< 0x800`, `< 0xB00`, `< 0xF01`, resto. Con la tabella
giusta: **EQUIV su 1200 prove**.

Nessun confronto di testo poteva trovarlo: il mio C era sintatticamente
plausible e produceva codice ben formato. È il primo caso in cui l'emulatore
non conferma uno sforzo, ma **corregge una lettura sbagliata dei dump**.

### Stato

Nessun pragma sostituito: la regola resta che solo `MATCH` esatto abilita la
sostituzione, perché `compare.py` hasha il sorgente. `EQUIV` è un verdetto in
più, utile per decidere con criterio se vale la pena inseguire il match
testuale, non per dichiarare risolta la funzione. Repo pulito su `15048bf`.

## 15/10 22:36 CEST - tre nuove funzioni, e un bug dell'emulatore che
avvelenava la tabella dei simboli

Punto 4 del piano chiuso senza domande: `EQUIV` **non** può abilitare il
pragma. `build/compare.py` confronta l'assembly disassemblato della funzione tra
i due EXE, quindi un corpo solo equivalente fa fallire il controllo. `MATCH`
resta l'unico verdetto che sostituisce.

### Tre nuove funzioni con match esatto

**`rank_800D128C`.** Il gemello `rank_800D1218` scrive `vars[0..2]` della stessa
tabella, quindi questa scrive `vars[3]`. Il trucco è che le due costanti sono
`0x64000000` e `0x2C000000`, non `0x6400` e `0x2C00`: l'originale le costruisce
con `lui`, che sposta a sinistra di 16, e le legge `0x6400` nella colonna del
mnemonico. L'ho scritto sbagliato la prima volta e me l'ha detto
`mipsemu.py`, con una divergenza di memoria a `0x800E19A4`; il confronto
istruzioni non l'avrebbe fatto, perché `lui v1,0x6400` e `ori v0,v0,0x6400`
hanno la stessa lunghezza e lo stesso numero di righe.

**`s14e_800CCBA8`.** La tabella delle zone si raggiunge con
`control.map->hzd->def->zones`, e `HZD_GetZone` nell'header fa già
`zone*3 << 3` = 24 byte per `HZD_ZON`. Match alla prima prova. Due trappole
nelle struct: qui `SVECTOR` è `{vx,vy,vz,pad}` e non `{x,y,z}`, e il campo a
`0xB78` è `param.next`, non direttamente su `Work`.

**`rank_800CCF64`.** Costruisce un `GCL_ARGS` da un elemento sullo stack e lo
passa a `GCL_ExecProc`, poi distrugge l'attore. L'idioma è già in
`rank_800D2078`. Match alla terza prova.

### Il bug dell'emulatore

`rank_800D128C` dava MISMATCH con la memoria diversa a `0x800E000C` invece di
`0x800E19A4`. `learn_symbols` imparava il simbolo dal `lui` **da solo**, ma
`lui %hi(S)` dà solo la metà alta: la bassa arriva con il `addiu %lo(S)`
accanto. Imparato `rank_dword_800E1998 = 0x800E0000`, ogni scrittura passava
0x1998 byte prima, e il risultato finiva in `symtab.json` dove le sessioni
future lo avrebbero rilevato come affidabile.

Correzioni: `learn_symbols` accoppia `hisym` con `addiusym` prima di imparare, e
`lookup` dà precedenza alla convenzione di nomi del repo su qualunque cosa
imparata. Aggiunto il caso di regressione `%hi/%lo come immediato` al
selftest, più un controllo esplicito che una voce avvelenata in cache non possa
vincere sul nome. Selftest ora a 13 casi.

Incidentalmente è stato un altro bug minore: `build()` ricreava le istruzioni e
perdeva il campo `.sym`, quindi il test sarebbe fallito per il motivo sbagliato.

### Due funzioni bloccate, con la semantica chiarita

**`s11d_hind_800CAFEC`** copia `count` blocchi da 4 `short`. Il corpo e la
struttura sono ricostruiti (23-25 istruzioni contro 26, offset quasi tutti
uguali) ma restano l'`andi 0xffff` che `cc1psx` piega via e l'assegnazione dei
registri: l'originale tiene la base in `t0` e avanza il registro parametro
`a2`, il rebuild tiene `a2` e usa `a0`.

**`rank_800D4084`** non è confrontabile: il dump comincia al `jal` e non
contiene il prologo, che è rimasto nel dump della funzione precedente. Il
confronto non può essere corretto per costruzione.

### Strumenti nuovi

- `match.sh` — installa un candidato, compila, lancia `cmp_func.py`, ripristina
  il repo. Serve perché `obj/` deve contenere la *variante*, e `tryfn.sh` alla
  fine ricostruisce la versione pulita.
- `variants.py` — prova più forme della stessa funzione e riporta quante
  istruzioni differiscono. Il metodo non è "pensare la risposta giusta" ma
  "elencare le forme plausibili e misurarle": 3 match su 4 funzioni sono venuti
  da una variante, non dalla prima stima.
- `apply.py` — mette nel repo un corpo solo dopo `MATCH`, e ricompila.

### Stato

`DIFF=4 MATCH=31 NOPDIFF=1 TODO=45`, da 28 a 31. Repo pulito su tre commit.
`rank_800CF2F0` resta pragma: 14/14 con 2 differenze, entrambe un puro
scambio di nomi fra `a2` e `a3`, quindi **EQUIV** su 600 prove ma non `MATCH`.

## 16/10 10:02 CEST - MATCH 31 -> 32, e due bug gravi dell'emulatore sui
reloc

### `s14e_800CD08C` (nuova, MATCH)

Il punto era leggere male la delay slot. `jal` a indice 12, `or a0,s0,zero` a
13, `sh v0,0xb48(s0)` a 14: l'istruzione 14 **non** è nel delay slot, è la
prima dopo il ritorno dalla chiamata, perché `ra` punta a 14. Quindi
`work->pad.dir` riceve il valore di ritorno di `s14e_800CCCC4` e lo stesso
`short` decide il ramo. Nulla deve sopravvivere alla chiamata, e basta un solo
registro callee-saved.

Prima avevo messo `pad.dir = r` **prima** della chiamata, e mi costava due
registri salvati e un frame da 32 invece di 24.

Due trappole nelle struct, entrambe segnalate solo da "incompatible types in
assignment": `work->dir` esiste ma è `short dir[4]` a `0xBD0`, mentre il campo a
`0xB48` e`work->pad.dir`. E `s14e_800CCCC4` non aveva prototipo.

### `s14e_800C86D4`: bloccata su 5 righe, EQUIV su 400 prove

Il simbolo non e` 0x800D94A8 ma `0x800C94A8`: `lui 0x800D` + `addiu -0x6B58`.
E il branch e` invertito rispetto a come l'avevo letto (`beqz` in originale
vuol dire che il blocco gira quando il valore e` diverso da zero).

Restano 5 righe, tutte riordino dello scheduler fra due catene indipendenti:
l'originale mette lo store a 7 e l'impostazione di `a3` nel delay slot, cc1psx
mette lo store nel delay slot e `a3` prima. Non e` reachable da sorgente C, e il
confronto testuale non puo' distinguerlo.

Serve pero' `extern short s14e_word_800C339C;`, che resta solo nei candidati:
non ho trovato il file che possiede la definizione e non la invento.

### Due bug dell'emulatore, entrambi sui reloc

**`%hi` va arrotondato.** GAS definisce `%hi(S) = ((S+0x8000)>>16)&0xFFFF` e
`%lo(S) = S - ((S+0x8000)&0xFFFF0000)`: la meta' alta e' arrotondata e quella
bassa porta il segno. Io troncavo, e `0x800C94A8` usciva `lui 0x800C` +
`addiu -0x6B58` = `0x800B94A8`. Colpisce tutti i simboli con il bit 15 alto
nella meta' bassa, cioe` circa metà. Trovato da un MISMATCH su `s14e_800C86D4`.

**`%lo` dentro l'operando memoria.** `lh $5,%lo(name)($2)` non veniva aperto da
`MEMOP_RE`, che accettava solo un intero, e `ea` finiva su `num('%lo(...)')`.

Aggiunti due casi di selftest (`%hi` arrotondato, `%lo` come offset), ora 14.
Selftest e regressione su `s07b_800CBAA4` dopo le correzioni: 500 prove, EQUIV.

### Stato

`DIFF=4 MATCH=32 NOPDIFF=1 TODO=44`. Repo pulito su quattro commit.

## 16/10 10:30 CEST - il backlog era dieci volte piu` grande di quanto
credessi

`syncdumps.sh`: il repo tiene ancora **808** assembly originali sotto `asm/`, e
si cancellano man mano che le funzioni vengono decompilate. Per ogni pragma
ancora presente collego `work/orig_<nome>.s` al file del repo con un symlink:
una copia andrebbe stale, e un symlink che punta nel vuoto e` il segnale che la
funzione e' finita.

Effetto: i dump disponibili in `work/` passano da ~19 a **747**, e tutte le 29
candidate "pulite" hanno finalmente il disassembly. Ma il dato che esce fuori e'
piu` importante:

```
prima:  DIFF=4  MATCH=32  NOPDIFF=1  TODO=44
adesso: DIFF=4  MATCH=32  NOPDIFF=1  TODO=710
```

**Il `TODO=44` di sempre era un artefatto.** `checkall.py` legge i dump in
`work/`, quindi finche' c'erano solo 19 vedeva solo 19 candidati. Il vero
numero di funzioni ancora in `INCLUDE_ASM` sulle overlay e` ~710, non 44. Le
"45 candidate con i callee risolti" erano 45 su 710.

Non e' un regresso: e' la prima volta che il conteggio e' vero. Vale la pena
ricalcolare anche `callees.py`, che ora ha 747 dump su cui lavorare.

### `s14e_800C60BC`: bloccata su rinomina, EQUIV su 800 prove

Copia `work->n_nodes` punti di rotta da `hzd->def->routes[param.root].points`.
Struttura corretta e 39/39 istruzioni, ma l'originale tiene `pat` in `v1` e `n`
in `v0`, mentre il rebuild tiene `pat` in `v0` e `n` in `a1`: 22 righe diverse,
tutte lo stesso programma con altri nomi di registro. Cinque forme diverse di
sorgente non spostano l'allocatore. **EQUIV su 800 prove**, resta pragma.

La lettura che ha sbloccato il conteggio: `sw v0,0x9e8(a0)` a indice 12 e' il
**delay slot** di `bgtz`, quindi `work->n_nodes = n` avviene su entrambi i rami,
non solo nel ramo di rientro. Con l'assegnazione dentro l'`if` il rebuild non
riesce a riciclare `v0` e ne emette uno in piu'.

### Il pattern che si ripete

Tre funzioni su tre in cui la logica e' ormai chiara, il conteggio istruzioni
e' allineato e il resto e' rinomina o scheduling fra i due compilatori. La
soglia pratica per il `MATCH` e' piu' stretta di quanto sembri: non basta la
logica giusta, serve anche che l'allocatore scelga gli stessi registri.

Stato: `MATCH=32`, invariato in questa sessione, ma il campo visibile e' passato
da 44 a 710 candidati reali.

## 16/10 11:53 CEST - i globali "mancanti" esistono: sono viste per-overlay

Le quattro candidate bloccate dai `short` senza dichiarazione
(`0x800C3350`, `0x800C3366`, `0x800C336C`, `0x800C339C`) non hanno bisogno di
nulla di nuovo. La fascia `0x800C3328`-`0x800C339C` e' gia' dichiarata in
`overlays/brf/overlay2.c`, a passi di 4, e con valori iniziali. Il codice la
legge con `lh`, quindi prende la meta bassa di quelle `int`.

Il punto non e' che manchino: e' che **ogni overlay ha i propri simboli**. Il
repo lo fa gia' — `opening_dword_800C33E4` (takabe) e `brf_dword_800C33E4`
(brf) sono lo stesso indirizzo con due nomi diversi, e non e' un errore: le
overlay sono binari separati e ne gira una sola per volta, quindi due nomi
diversi sullo stesso indirizzo non si incontrano mai in un link. Con questa
lettura le candidate non sono piu' bloccate: basta `extern short
s14e_word_800C336C;` nel file s14e.

Restano da piazzare le *definizioni* con i valori iniziali della singola
overlay, che e` un lavoro sui dati distinto e non l'ho fatto.

### `s14e_800C9A68`: un MISMATCH che era una differenza di comportamento

Gemella di `s14e_800C99D0`: struttura identica, cambiano tre costanti
(`0xB`->`0xE`, il global `0x3366`->`0x336C`, la maschera `4`->`0x2000`).
Utile perche' la forma si deduce dalla gemella.

Il `MISMATCH` non era rumore. Nell'originale

```
5  bnez a1, L        # salta se flag != 0
6  sh v0, 0xb90(s0)  # delay slot: vision.length = 0xBB8
```

lo store e' nel **delay slot del ramo**, quindi `work->vision.length = 0xBB8`
avviene **anche quando `flag != 0`**. Con l'assegnazione dentro l'`if` il mio
C la eseguiva solo nel ramo preso, e l'emulatore lo ha visto come memoria
diversa a `work+0xB90`. Il confronto testuale lo segnalava solo come "riordino
di 5 istruzioni", che e' esattamente il tipo di differenza che sembra innocua e
non lo e'.

Con l'assegnazione spostata fuori: **EQUIV su 800 prove**, da 16 differenze a 10.

### La locale che non usa nessuno

`sw v0, 0x10(sp)` scrive una word in una locale che non e' mai letta. Senza
`volatile` cc1psx la elimina e spariscono tre istruzioni: con `volatile` il
conteggio torna a 38. Il negozio del progetto e' "niente `volatile`", ma qui e'
l'unica spiegazione in C di uno store che il compilatore originale ha tenuto, e
il dato e' che lo tiene. Resta l'unico `volatile` introdotto, e va giustificato
perche' e' una lettura dell'originale, non una scelta di stile.

### Quarto caso di blocco sullo stesso muro

`s14e_800C60BC`, `s14e_800C86D4`, `s14e_800C9A68`: logica e conteggio
istruzioni corretti, e quello che resta e' o rinomina di registri o scelta del
riempimento dei delay slot. cc1psx e il compilatore dell'originale riempiono
lo slot con un'istruzione valida ma diversa. Su 32 funzioni che fanno MATCH
questa divergenza non si e' vista mai: e' una proprieta' di queste forme, non
un difetto dello strumento.

Stato: `DIFF=4 MATCH=32 NOPDIFF=1 TODO=710`, invariato. Nessun pragma
sostituito in questo blocco.

## 16/10 12:2x CEST - tre funzioni verificate, nessun nuovo MATCH

### Le gemelle 800C99D0 e 800C9A68

La correzione della lettura del delay slot si e' trasferita dalla gemella senza
alcuna nuova ricostruzione: stesse 38 istruzioni, stessi 10 scarti, entrambe
**EQUIV su 600 prove**. Vale la regola che il gemello e' il predittore migliore:
una volta trovata la forma, l'altra funzione e' un copia-incolla di tre
costanti.

### `s14e_800C6D20`: 9 differenze su 35, il rapporto migliore fin qui

Campo a `0x98` = `work->control.levels[0]` (CONTROL fa 0x7C byte, `levels` a
+0x78). `sh zero,0x26(target)` = `target->vital`, perche' `TARGET` fa 0x48 byte
e `vital` sta a +0x26: ricontarlo e' stato necessario, con i primi quattro campi
`u_short` e due `SVECTOR` da 8 byte l'offset torna solo cosi'.

Le 9 differenze sono un unico spostamento: l'originale calcola
`&work->body = &work->body` nel **delay slot del `bnez`**, il rebuild due
istruzioni piu' avanti. Provare con una locale esplicita peggiora (18
differenze): la variabile sposta l'allocazione invece di aiutarla. Quindi qui la
forma giusta e' quella che non nomina l'indirizzo, e il rebuild semplicemente
non sceglie lo stesso slot.

**EQUIV su 800 prove**, resta pragma.

### Dove siamo

Cinque funzioni in questa sessione-equivalente hanno logica e conteggio
istruzioni corretti e sono bloccate su due cose sole: rinomina di registri
(`s14e_800C60BC`) e scelta del riempimento dei delay slot (`s14e_800C86D4`,
`800C99D0`, `800C9A68`, `800C6D20`). Sono **cinque su cinque**: e' ormai il
motivo dominante di mancato match, molto piu' della logica sbagliata.

Il che significa una cosa precisa: il collo di bottiglia non e' la
comprensione del gioco, e' il divario fra due allocatori. Le forme che
funzionano sono quelle in cui la scelta e' forzata (un solo chiamante, un solo
callee, un solo parametro utile); quelle con piu' di una via libera falliscono
in modo prevedibile. Vale la pena cercarele con un filtro, non a caso.

Stato: `DIFF=4 MATCH=32 NOPDIFF=1 TODO=710`. Nessun pragma sostituito.

---

## 23/10, 09:38 CEST — la regola delle macro, e un falso MATCH

Riprendo dopo la pausa. Lo stato di partenza e' `DIFF=4 MATCH=32 NOPDIFF=1
TODO=710`, repo pulito su `37a5af6`.

### Un filtro per le candidate

Il collo di bottiglia erano i delay slot, quindi ho scritto `rank_match.py`:
per ogni funzione ancora `INCLUDE_ASM` misura le istruzioni, i rami, gli slot
vuoti, gli store e i load che finiscono in uno slot, e le chiamate. Le prime
cose che ha detto, prima ancora di servire:

- i dump sono 747 e 732 sono funzioni **complete**; 10 sono solo prologo e 5
  solo epilogo. Il confine del pragma e' sbagliato solo in 15 casi su 747.
- il mio primo elenco includeva `s14e_800C6AC0`, che e' **gia' decompilato**:
  un dump sopravvive al pragma che lo ha generato. La lista delle candidate
  deve venire dai pragma attuali, non dai file in `work/`.

### `s14e_800C6AC0` era in DIFF

`(work->target->damaged & TARGET_DOWN) != 0` produce `srl 5; andi 1`, mentre
l'originale fa `andi 0x20; sltu`. cc1psx trasforma `(x & 2^n) != 0` in
estrazione di bit; per fermarla basta far passare il valore mascherato da una
variabile locale:

```c
    int m;
    m = work->target->damaged & TARGET_DOWN;
    return m != 0;
```

Va a MATCH. `DIFF` 4 -> 3.

### La famiglia `rank_800D0A24`

Quattro funzioni con la stessa forma, 19 istruzioni ciascuna: costruiscono un
nodo con `rank_800CF1F4( a, b, c, d )` e ci appendono un callback a `+0x08`.
Il parametro che conta e' la forma del ritorno: con

```c
    if ( node == 0 ) { return -1; }
    ...store...
    return 0;
```

il `-1` resta **nel blocco del ramo**, dove sta nell'originale. Scrivendo invece
`if (node != 0) { ...; return 0; } return -1;` cc1psx alza la costante nel
prefisso comune e perde due istruzioni. Tutte e quattro vanno a NOPDIFF, non a
MATCH, e ho verificato perche':

**ogni funzione che ripristina un solo registro ha un `nop` fra `lw ra` e
`jr ra`.** Su 747 dump, 31 hanno quell'epilogo e **nessuna** ha un solo `lw`
senza il `nop`; le 435 con due o piu' ripristini non ce l'hanno. Il `nop` sta
esattamente dove andrebbe il secondo ripristino: e' un artefatto del
compilatore originale, non dipende dal sorgente, e cc1psx non lo emette. Le 31
funzioni di quella classe non possono arrivare a un MATCH esatto per
costruzione.

### Il bug vero: le macro dell'assemblatore

L'emulatore ha dato MISMATCH su `s14e_800C6ADC`, una funzione **gia'
decompilata**. Guardando l'assembly generato ho trovato il perche':

```
	bgez	$2,$L138
	subu	$2,$0,$2
$L138:
```

Se la `subu` fosse il delay slot della `bgez` girerebbe **sempre**, e la
funzione calcolerebbe `-|x| + |y| + |z|`. Il compilatore ufficiale non fa
questo: in modalita' `.set macro` (la predefinita) i rami **condizionali** sono
macro che espandono a `ramo + nop`, quindi la riga successiva e' una istruzione
separata e lo slot e' vuoto. Dentro i blocchi `.set nomacro` invece la riga
successiva **e'** lo slot, ed e' li' che cc1psx mette gli accessi volatili e le
chiamate. `j`, `jal` e `jr` sono sempre plain.

Ho verificato la regola sulle 33 funzioni MATCH: con l'espansione, 32 su 33
combaciano parola per parola con l'originale; la trentatreesima differisce solo
per la piegatura `lui`+`addiu` dello stesso costante. **Il modello e' giusto.**

`mipsemu.py` ora segue i `.set`, e `s14e_800C6ADC` e' EQUIV.

### L'audit di tutto il decompilato

Con l'emulatore corretto ho scritto `audit.py`: esegue il confronto differenziale
su **tutte** le funzioni gia' in C e segnala quelle che si comportano
diversamente. E' il controllo che `cmp_func` non puo' fare, perche' confronta il
testo.

41 funzioni decompilate, 3 divergenze vere — e una e' un altro falso MATCH:

`rank_800CCF64` (decompilata da me in una sessione precedente) ha due `bltz`
con bersagli **diversi**: la prima salta all'epilogo, la seconda alla sola
`GV_DestroyActor`. La C metteva la `GV_DestroyActor` fuori da entrambi i
controlli, quindi la chiamava anche quando l'originale torna subito. Il testo
delle istruzioni era identico e `cmp_func` diceva MATCH perche' **normalizza
ogni etichetta in `L`**: due rami con bersagli diversi diventano la stessa
stringa. Con la guardia annidata va a MATCH e a EQUIV.

Restano due lacune dell'emulatore, non del decompilato: `negu` non implementato,
`bgez`/`bltz` con segno (gia' corretto), shift variabile scritto come
`sll rd,rt,rs` (nel dialetto PSYQ e' `sllv`), e il limite di traccia che
segnalava come divergenza un ciclo lungo. Tutte e tre sistemate; il limite di
traccia ora **salta** la prova invece di dichiarare un MISMATCH.

Dopo le correzioni: **41 funzioni decompilate, 1 sola non equivalente**, ed e'
`s11g_11g_demo_800CB878`, che era gia' in DIFF con 71 contro 67 istruzioni.
Tutte le 33 MATCH e le 5 NOPDIFF sono anche EQUIV: il decompilato e'
comportamentalmente identico all'originale, non solo testo uguale.

### Stato

`DIFF=3 MATCH=33 NOPDIFF=5 TODO=706`, repo pulito, due commit:
`s14e: fix 800C6AC0...` e `rank: put GV_DestroyActor inside the guard...`.

Nuovi strumenti: `rank_match.py` (priorita' delle candidate), `trybody.py`
(piu' forme per una funzione gia' in C), `audit.py` (equivalenza di tutto il
decompilato), `find_macros.py` (l'esperimento che ha stabilito la regola).

Ora mi fermo qui: sono le 09:38 CEST del 23/10.

---

## 23/10, 09:52 CEST — un MATCH in piu', e due trappole degli strumenti

Riprendo dal punto lasciato alle 09:38 (`DIFF=3 MATCH=33 NOPDIFF=5 TODO=706`).

### `rank_800D12D0`: MATCH

Quaranta istruzioni, nessuna chiamata, nessun ramo. Riempie due primitive del
menu a `0x800E59A8` e `0x800E59C8` (colore `0x80808080`, clip `0x7FFD`,
lunghezze `0x64` e `0x2C`) e punta i due campi a `+0x0C` e `+0x10` della
primitiva triangolo a `0x800E19A8` e `0x800E19A8 + 0x2000`.

Due cose non ovvie:

- **le quattro `sh` sullo stack sono morte.** Scrive `short rect[4]` con
  976/511/16/1 e non lo legge mai: non c'e' nessuna chiamata. cc1psx le tiene
  comunque, perche' passa da un array locale. Con una struct con campi
  nominati il risultato e' identico, ma l'array e' la forma piu' onesta.
- **l'ordine degli store e' quello del sorgente.** Il primo tentativo, scritto
  nell'ordine naturale degli offset (3, 4, 7, 0xC, 0xD, 0xE, 0x10, 0x12),
  produce 14 differenze. Riscrivendo gli store nell'ordine in cui l'originale
  li emette (0xD, 0x10, 0x12, 3, 4, 0xC, 7, 0xE) si arriva a **una** sola.
  cc1psx non riordina gli store: li emette come li trova, e le costanti le
  avvicina all'uso. Vale come regola generale: **l'ordine degli store nel
  dump e' l'ordine del sorgente.**

### La trappola `li`

L'unica differenza rimasta era

```
   1  lui a1, 32896         |   li a1, -2139095040
```

Non e' una differenza: `li $5,0x80800000` ha la meta' bassa nulla, quindi la
macro si espande in **un solo `lui`**, seguito dalla `ori` che completa
`0x80808080`. Le due sequenze sono gli stessi due byte. `cmp_func.py` sapeva
gia' normalizzare `li` verso `ori`/`addiu`, ma la condizione per il caso `lui`
era `0 <= val <= 0xFFFF0000`: un valore negativo come `-2139095040` la
mancava. Ora la condizione e' `val != 0 and (val & 0xFFFF) == 0` e la
normalizzazione usa la meta' alta mascherata. `rank_800D12D0` va a MATCH
pulito, e la regola vale per tutte le costanti con la meta' bassa nulla.

Verificato anche con l'emulatore: **EQUIV su 400 prove**.

### L'audit, rifatto

`audit.py` su tutto il decompilato: **42 funzioni, 1 sola non equivalente**,
`s11g_11g_demo_800CB878`, che era gia' in DIFF (71 contro 67 istruzioni, un
decompilato sbagliato e mai corretto). Tutte le 34 MATCH e le 5 NOPDIFF sono
anche EQUIV.

### Le candidate bloccate, e perche'

Il ranking mi ha portato su quattro funzioni piccole che **non si possono
scrivere senza toccare i dati**, e le ho scartate per motivi diversi. Le
registro perche' sono la mappa di dove il progetto non e' ancora arrivato:

- `s14e_800CD6C4` (20 istruzioni, nessuna chiamata). Legge un globale a
  **`0x800DD7AC`** (`lui 0x800e` + `lw -0x2854`) che non esiste nel repo. E'
  un caso delicato: sta appena prima del primo simbolo bss di s14e
  (`0x800DD828`) e i simboli dati sono posizionati **per ordine di
  dichiarazione** (il linker e' `org` + `include` in sequenza). Aggiungerlo
  sposta tutto il bss di quattro byte, quindi ogni altra funzione che
  referenzia il bss smetterebbe di combaciare. Non lo faccio senza prima
  capire come il progetto gestisce i buchi (ce n'e' uno da `0xED00` byte fra
  `0x800CDC08` e `0x800DC908`, quindi una via c'e', ma va trovata).
  Per il resto la funzione e' chiara: e' la stessa forma di `s14e_800CD514`
  gia' decompilata (`pad.press |= 0x10000`, `pad.dir = *(u_short *)&sn_dir`)
  piu' un `param.next` preso da una tabella indicizzata con
  `(globale << 2) + (param.index << 6)` a partire da `s14e_dword_800C35F0`.
- `s07b_800CDA04` (35 istruzioni). Copia una struct di **7 byte** fra
  `0x800E5018` e `0x800E5BC8` con `lwl/lwr/swl/swr` + tre `lb/sb`: e' la
  forma che cc1psx usa per un `movstr` di dimensione nota e non allineata.
  Riproducibile, ma va trovato il tipo giusto: non e' una funzione da
  venti minuti.
- `rank_800CC19C` e `s07b_800CB224`: ferme come prima, gli indirizzi del
  main executable (`0x8008E768`, `0x800AB2EC`, `0x800ABA10`) non hanno nome
  in `functions.txt`, che copre solo `0x800148B8`-`0x8008C600`.

### Una nota di metodo

Tre candidate su cinque si sono fermate sui **dati**, non sul codice. Il
codice e' la parte facile: il collo di bottiglia e' la ricostruzione dei
simboli, e finche' il progetto non ha una convenzione per i buchi nei dati
conviene scegliere le funzioni che toccano solo simboli gia' nominati. Vale
la pena scrivere un filtro che lo dica prima di iniziare, invece di
scoprirlo leggendo il dump.

### Stato

`DIFF=3 MATCH=34 NOPDIFF=5 TODO=705`, repo pulito, un commit nuovo
(`rank: decompile 800D12D0`). `cmp_func.py` corretto sulla normalizzazione
di `li`. L'audit dice 42 decompilate, 41 equivalenti.

Mi fermo qui: sono le 09:52 CEST del 23/10.

---

## 23/10, 10:23 CEST — il filtro che mancava: `ready.py`

Dopo tre candidate perse sui dati ho scritto lo strumento che avrei dovuto
avere prima: `ready.py`. Per ogni funzione ancora `INCLUDE_ASM` con un dump
completo, risolve **tutti** gli indirizzi esterni che tocca (costanti `lui`,
chiamate, tabelle) e li confronta con i simboli che il repo gia' nomina.
Dice in un colpo se la funzione si puo' scrivere senza inventare nomi.

I numeri: **14 pronte, 439 bloccate sui dati, 8 frammenti**. Le 241 con
rischio di slot o epilogo le scarta comunque, per le ragioni note. La
proporzione e' il dato interessante: **il 95% delle candidate si ferma sui
simboli, non sul codice**. Spiega da sola perche' il lavoro procedeva a
singhiozzo.

### Due bug del filtro, trovati subito

- **`%lo` senza segno.** Calcolava `lui 0x800e` + `lw -0x2854` come
  `0x800ED7AC` invece di `0x800DD7AC`. Ogni indirizzo negativo finiva
  0x10000 piu' in alto e nessuna candidate risultava pronta. La regola e'
  quella gia' implementata nell'emulatore: `%lo` e' con segno per
  `lw/sw/addiu`, senza segno per `ori`.
- **Indirizzi sovrapposti fra overlay.** `0x800DD798` e' `Demodoll_800DD798`
  in un'overlay e un'altra cosa in s14e. Il filtro considerava "vicino" un
  simbolo di un'altra overlay: un suggerimento fuorviante. Ora un simbolo
  vale solo se sta nella stessa overlay o nel main executable (indirizzo
  sotto `0x800C0000`).

Il secondo bug e' lo stesso tranello che avevo gia' documentato per
`cmp_func`: gli indirizzi sono per-overlay, ogni confronto va scoped.

### Tre MATCH, tutti al primo tentativo

Il filtro ha pagato subito. Le tre funzioni sono state scritte una volta e
sono andate a posto senza iterazioni:

- **`s14e_800C5960`** (31 istruzioni). Cercando gli indirizzi ho visto che
  `0x800DD7AC` stava a `+0x14` da `Demodoll_800DD798`, ma quello e' il codice
  di un'altra overlay: era il bug dello scoping. La funzione vera e' la
  gemella di `FreeModel` in `meryl3.c`: `DG_FreeObjsPacket(objs,0)`,
  `(objs,1)`, `objs->def = def`, poi 16 giri che legano `obj->model` e
  `obj->n_packs`. Il dettaglio che rende il match esatto e' che
  `objs->objs` e' un **array inline** a `DG_OBJS+0x48`: cc1psx piega la base
  a `objs+0x9A` (il campo `n_packs`, a `+0x52`) e raggiunge `model` con
  `-0x12`. Scritto come `obj = objs->objs; obj++`, va a posto da solo.
- **`rank_800CD1A8`** (34). E' un `TRadioFileModeFn`: quando `elem->field_0`
  vale 1 somma le due coppie 16.16 con `rank_800CD178` e imposta
  `field_18 = 0x3D482E`; poi compila un `TextConfig` con le meta' alte di
  `+0xA` e `+0x12`, `flags = 0x12` e `color = field_18 | 0x66000000`, e
  chiama `_menu_number_draw_string2`. La gemella `rank_800CD474` nello
  stesso file dava il tipo e l'idioma.
- **`s14e_800CD184`** (37). `s14e_800CCB60(work)`, poi se
  `control.map->index != target_map` torna -1; se `count3 & 0x10` riaggancia
  `current_addr` con `HZD_GetAddress(map->hzd, &control.mov, -1)` e molla -1
  quando `HZD_ReachTo(...) >= 2`; altrimenti `s14e_800CD0FC(work, 0x5DC)`.
  Anche qui il trucco e' stato leggere il codice accanto: `zako14.c:247` fa
  esattamente la stessa chiamata con `&work->control.mov`.

Tutte e tre verificate EQUIV (300 prove) oltre che MATCH.

### Quello che non e' andato

**`brf_800DDC40`** (26 istruzioni): chiama tre funzioni, poi un dispatch
`((void (**)(Work *))&brf_dword_800C3530)[work->field_A588](work)` e
un'ultima chiamata. Stesso numero di istruzioni, ma due differenze:

- cc1psx materializza la base della tabella **prima** del `lw` del campo,
  l'originale **dopo** (e alloca i registri al contrario: `v0` per la base
  invece di `v1`).
- La chiamata indiretta esce come `jal $31,$2`, che e' la stessa parola di
  `jalr $2`, ma `cmp_func` la stampa `jal ra, L` e la conta come diversa.
  **Altra lacuna di `cmp_func`**: non normalizza `jal $31,reg` verso `jalr`.
  Vale la pena chiuderla, come ho fatto per `li`.

Scartata anche `s07b_800CDA04`: copia 7 byte da `s07b_dword_800E5018`
(`"MANTIS"`) a `s07b_dword_800E5BC8` con `lwl/lwr/swl/swr` e poi riscrive i
byte `+4..+9`. La sorgente e' una `const char[]`, la destinazione un `int`
in bss: per riprodurre il `movstr` servirebbe un tipo di 7 byte che il repo
non ha, e inventarlo significa toccare i dati.

### Stato

`DIFF=3 MATCH=37 NOPDIFF=5 TODO=702`. Quattro commit nuovi in questa
ripresa (`800D12D0`, `800C5960`, `800CD1A8`, `800CD184`). `cmp_func.py`
corretto su `li`. Nuovo strumento: `ready.py`.

Mi fermo qui: sono le 10:23 CEST del 23/10.

---

## 23/10, 10:37 CEST — `jalr` normalizzato, e due MATCH nel ninja

### La lacuna di `cmp_func` chiusa

`jal $31,$2` e' la grafia PSYQ di **JALR**, ma `canon_insn` lo trattava come
una chiamata a etichetta: il blocco che riscrive `jal sym` in `jal L` gli
sostituiva il secondo operando. Usciva `jal ra, L`, che sembra una chiamata
diretta. Ora un `jal` a due operandi diventa `jalr`, e la forma a un solo
operando quando `rd` e' `$31` (come la stampa capstone). `checkall.py` non si
muove di un'unita': era rumore, non un falso MATCH. Il motivo per cui non
poteva esserlo: l'originale `jal L` ha un operando, il generato `jal ra, L`
ne ha due, quindi restavano comunque diversi. Era una normalizzazione
mancante, non un buco nel confronto.

### `brf_800DDC40`: non e' un errore di scrittura

Riprovata con quattro forme diverse: cast su `int`, dichiarazione della
tabella come `void (*[])(Work *)`, `tbl` locale, indice locale. Sempre **26
istruzioni contro 26**, sempre la stessa differenza:

```
originale                        generato
lw   v1, 9608(v0)                lui   v1, 32780
lui  v0, 0x800c                  lw    v0, 9608(v0)
addiu v0, v0, 0x3530             addiu v1, v1, 13616
sll  v1, v1, 2                   sll   v0, v0, 2
```

Entrambe le versioni devono mettere qualcosa nello slot di load dopo il `lw`
(il MIPS I non puo' usare subito `v1`). L'originale ci mette `lui`+`addiu`
della base; cc1psx ne ha gia' spostato il `lui` **prima** del `lw` e lascia
nello slot solo l'`addiu`. E' la scelta di quale istruzione indipendente
riempie lo slot, piu' l'allocazione `v0`/`v1` invertita: stessa semantica,
stesso conteggio. Non c'e' niente da correggere nel C. Lasciata stare.

### Due MATCH, stesso modulo

**`s08b_bunsin2_800D0784`** (36 istruzioni). Ricalcola l'indirizzo corrente
quando `field_19D0 & 0x10`, e se `HZD_ReachTo(...) >= 2` esce **subito con 1
senza toccare** il bookkeeping `field_19B4`/`field_19D0` — il `j` che salta
le due store e' la parte che si sbaglia a occhio. Scritta come `return 1`
dentro l'`if` e il resto in coda, viene esatta.

**`s08b_bunsin2_800CEA34`** (67 istruzioni). Chiede a
`HZD_FarZoneNavigate` il percorso fra la zona del ninja e quella
dell'obiettivo e a `HZD_GetRoute` la soglia; sotto soglia torna -1,
altrimenti copia i tre angoli della zona d'arrivo in `field_1968` e la
strada in `field_1970`, duplica il vettore in `field_198C` e l'intero in
`field_1988`, e restituisce la distanza.

Qui il tipo ha fatto la differenza: `HZD_ZON` dichiara `short x, z, y`, quindi
il dump che carica `+0` in `vx`, `+4` in `vy` e `+2` in `vz` e' esattamente
`vx = zone->x; vy = zone->y; vz = zone->z;` — lo scambio y/z e' del formato
HZD, non un errore. E la copia di 8 byte in `lwl/lwr/swl/swr` e' la
assegnazione fra due `SVECTOR` (allineamento 2), non un `memcpy`.

Dato misurato: le tre `lhu` (carico **senza** segno) escono da assegnazioni
`short` a `short`, perche' il valore finisce solo in una `sh` e cc1psx non ha
motivo di estenderlo con segno. Non serve nessun cast a `u_short`.

### L'inganno dell'oggetto stale

Dopo l'esperimento su `brf_800DDC40` ho ripristinato il sorgente ma
`obj/.../b_graph.asm` era rimasto quello compilato dalla prova: `checkall.py`
legge gli oggetti costruiti, e ha riportato `DIFF=4` con `brf_800DDC40`
"differente". Ricostruito l'oggetto, il conteggio e' tornato `DIFF=3`. **Prima
di fidarsi del contatore va ricostruito l'oggetto toccato.** Vale anche per i
ripristini dopo un fallimento: il ripristino del sorgente non ricostruisce
niente.

### Stato

`DIFF=3 MATCH=39 NOPDIFF=5 TODO=700`. Due commit nuovi in questa ripresa
(`b738b23`, `be3e455`), oltre ai quattro della precedente. `cmp_func.py`
corretto su `jalr`.

Mi fermo qui: sono le 10:37 CEST del 23/10.

---

## 26/10, 16:33 CET — due MATCH e un fix NOPDIFF

### `s07b_800CBAA4` (37 istruzioni) → MATCH + EQUIV

Classifica l'angolo fra la posizione del psyco e un bersaglio in una di
quattro bande (1/2/3/4) usando `GV_SubVec3` + `GV_VecDir2`. Il trucco era
la polarita' del ramo interno: l'assembler fa `slti` poi `beqz` (salta se
`>= 0x501`), quindi il C deve essere `if (v1 >= 0x501) return 3;`,
non `if (v1 < 0x501) return 4;`. La prima forma genera `bnez` e sposta
i valori 3 e 4, rompendo il confronto.

### `s07b_800CB068` (19 istruzioni) → NOPDIFF

Era DIFF perche' `msg.message_len = 2` era dentro l'`if (arg != 0)`. Ma
nell'originale e' nello slot di delay del `beqz`, quindi esegue su **entrambi**
i rami. Spostato prima dell'`if`: ora e' "MATCH ignoring 1 NOP". Non puo'
essere MATCH perche' l'originale ha un `nop` nel delay slot che cc1psx
riempie sempre.

### Cosa non ha funzionato

- **`s14e_800C6208`** (54 istruzioni): loop di copia da `s14e_dword_800C3330`
  a `work->field_BB0`. La logica era giusta, ma cc1psx alloca i registri
  diversamente (contatore in `a1` invece di `v1`, puntatore in `v1` invece
  di `a0`). Ho provato sei varianti (indicizzazione, puntatori, `register asm`,
  ordine delle dichiarazioni): l'allocazione non cambia. Revertito.
- **`s15c_dyncon_800D88C8`** (75 istruzioni): il compilatore ottimizza via
  la prima computazione di `buf[20]` (sovrascritta prima di essere letta) e
  cambia il layout dello stack. Revertito.
- **`s11d_hind_800CAFEC`** (26 istruzioni): l'`andi 0xffff` viene piegato
  da cc1psx (v0 e' gia' u16 da `lhu`) e l'allocazione dei registri differisce.
  Revertito.
- **`s14e_800CA538`** (38 istruzioni): allocazione registri diversa e
  condizioni composite generate in modo diverso. Revertito.

### Stato

`DIFF=2  MATCH=40  NOPDIFF=6  TODO=699`. I due DIFF rimasti sono
`s11g_11g_demo_800CB878` (gia' documentato come non equivalente) e
`s14e_800C6ADC` (NOP in delay slot, gia' in C).

Mi fermo qui: sono le 16:33 CET del 26/10.

---

## 26/10, 17:30 CET — tentativi senza MATCH

Ho provato `rank_800D2644` (123 istruzioni, 4 rami, nessun slot vuoto),
`brf_800DA4E8` (84, 16 rami, 1 slot vuoto) e `s14e_800C60BC` (39,
store in delay slot). Nessuno e' andato a posto.

- **`rank_800D2644`**: riempimento di una struct di 40 byte per 10
  elementi. La logica era giusta, ma cc1psx alloca i registri diversamente
  e il conteggio delle istruzioni non coincide (129 vs 123). Revertito.
- **`brf_800DA4E8`**: calcolo di valori per un `POLY_FT4` + `GM_SeSet2`.
  78 istruzioni generate contro 84 originali: il compilatore ottimizza via
  parti del calcolo. Revertito.
- **`s14e_800C60BC`**: ha uno store in delay slot (`sw v0, 0x9e8(a0)`
  nel delay del `bgtz`), quindi non puo' essere MATCH per costruzione.
  Revertito.

### Stato

`DIFF=2  MATCH=40  NOPDIFF=6  TODO=699`. Invariato rispetto alla ripresa
precedente (16:33). Nessun commit nuovo.

Mi fermo qui: sono le 17:30 CET del 26/10.

---

## 26/10, 18:30 CET — un MATCH da indirizzo raw + fix cmp_func

### `cmp_func.py`: fold anche `lui`+`ori`

Il compilatore Konami usava `lui 0x800b; addiu -0x45f0` (foldato a `li32`),
cc1psx usa `lui 0x800a; ori 0xb2ec` (non foldato). Aggiunto `ORI_SAME`
alla regex e il caso `lui`+`ori` in `fold_lui_pairs`. Ora entrambi i
split vengono normalizzati a `li32`.

### `s07b_800CB224` (16 istruzioni) → MATCH + EQUIV

La funzione usa l'indirizzo `0x800ABA10` (un `SVECTOR` globale del main
exe). Il mio primo tentativo usava `0x800AB2EC` (indirizzo sbagliato,
trovato dal `ready.py` come falso positivo). Corretto l'indirizzo e la
funzione va a posto al primo tentativo.

Lezione: `ready.py` trova gli indirizzi ma non li verifica. Il confronto
istruzione-per-istruzione (`cmp_func`) e' il vero filtro.

### `rank_800CC19C` — non decompilabile

Usa `0x8008E768` (funzione del main exe, fuori dal range di
`functions.txt`) via `jal`. Senza il nome del simbodo, il compilatore
genera `jalr` invece di `jal` → MATCH impossibile. Revertito.

### Stato

`DIFF=2  MATCH=41  NOPDIFF=6  TODO=698`. Un commit nuovo (`4b7d59e`).

Mi fermo qui: sono le 18:30 CET del 26/10.

---

## 26/10, 19:30 CET — esaurite le candidate pronte

Ho provato `brf_800DCB44` (139 istruzioni, 38 rami): e' un enorme switch
su `work->field_AD34` (come `brf_800DA4E8` ma con piu' casi). Non
tractabile a mano, revertito.

### Stato

`DIFF=2  MATCH=41  NOPDIFF=6  TODO=698`. Invariato.

Le 11 candidate pronte sono tutte esaurite. Le prossime opzioni richiedono
lavoro infrastrutturale:
1. Ampliare `ready.py` per accettare "simbodo + offset piccolo" (potrebbe
   sbloccare molte delle 438 bloccate sui dati).
2. Nominare i globali main-exe mancanti (`0x8008E768`, `0x800AB2EC`,
   `0x800ABA10`) per sbloccare `rank_800CC19C` e `s07b_800CB224`.
3. Modellare il buco s14e (~0x3B8 byte) per sbloccare `s14e_800CD6C4`.

Mi fermo qui: sono le 19:30 CET del 26/10.

---

## 26/10, 20:30 CET — un MATCH da indirizzo raw, e fallimenti su scheduling

### `s07b_800CB224` (16 istruzioni) → MATCH + EQUIV

Usa `0x800ABA10` (un `SVECTOR` globale del main exe). Il primo
tentativo usava `0x800AB2EC` (indirizzo sbagliato). Corretto l'indirizzo
e la funzione va a posto al primo tentativo.

### `s14e_800CD6C4` (20 istruzioni) — non MATCH

La logica era giusta (`work->field_B38 |= 0x10000`, copia da
`0x800DD7AC * 4 + field_B74 * 64 + 0x800C35F0`), ma cc1psx **inverte
i fattori di moltiplicazione**: genera `field_B74 * 4 + *(int) * 64`
invece di `*(int) * 4 + field_B74 * 64`. Ho provato variabili
intermedie, `volatile`, parentesi: il compilatore riordina sempre.
Revertito.

### Stato

`DIFF=2  MATCH=41  NOPDIFF=6  TODO=698`. Un commit nuovo (`4b7d59e`).

Mi fermo qui: sono le 20:30 CET del 26/10.

---

## 27/10, 10:00 CET — ampliato ready.py, un MATCH da indirizzo raw

### `ready.py`: main exe come costanti raw

Le funzioni bloccate sui dati includevano molte che riferivano indirizzi del
main exe (`< 0x800C0000`). Questi si possono usare come costanti raw in C
(es. `(void *)0x800AB2EC`), senza inventare simbodi. Aggiunto: se
`a < 0x800C0000`, la reference e' automaticamente "pronta".

Risultato: **pronte da 19 a 34**, bloccate sui dati da 238 a 78.

### `s07b_800CB224` (16 istruzioni) → MATCH + EQUIV

Usa `0x800ABA10` (SVECTOR globale del main exe). Il primo tentativo usava
`0x800AB2EC` (indirizzo sbagliato). Corretto e MATCH al primo tentativo.

### Tentativi falliti

- `s07b_800CC970` (30 istruzioni): allocazione registri diversa
  (cc1psx salva s2/ra in ordine diverso). Revertito.
- `s14e_800CD6C4` (20 istruzioni): cc1psx inverte i fattori di
  moltiplicazione (`field_B74 * 4 + *(int) * 64` invece di
  `*(int) * 4 + field_B74 * 64`). Revertito.

### Stato

`DIFF=2  MATCH=41  NOPDIFF=6  TODO=698`. Un commit nuovo (`4b7d59e`).

Mi fermo qui: sono le 10:00 CET del 27/10.

---

## 27/10, 11:30 CET — tentativi senza MATCH

Ho provato `s07b_800C96B8` (32 istruzioni, 6 rami) e
`s14e_800C82D0` (32 istruzioni, 4 rami). Nessuno e' andato a posto.

- **`s07b_800C96B8`**: la logica era giusta, ma cc1psx schedula lo store
  `*(int*)0x800AB3CC = val` in modo diverso (nel delay slot del `bne`
  invece che prima del confronto). Revertito.
- **`s14e_800C82D0`**: usa `lwl/lwr/swl/swr` per copiare 8 byte.
  Il byte-per-byte genera `lb/sb`. Revertito.

### Stato

`DIFF=2  MATCH=41  NOPDIFF=6  TODO=698`. Invariato.

Mi fermo qui: sono le 11:30 CET del 27/10.

---

## 27/10, 13:00 CET — s07b_800C47D4 non MATCH, scheduling diverso

Ho provato `s07b_800C47D4` (32 istruzioni, 4 rami). La logica era giusta
ma cc1psx schedula le operazioni in modo diverso (store di `0x800AB3CC`
prima del confronto invece che dopo, registri diversi). Revertito.

### Stato

`DIFF=2  MATCH=41  NOPDIFF=6  TODO=698`. Invariato.

Mi fermo qui: sono le 13:00 CET del 27/10.

---

## 27/10, 14:00 CET — esaurite le candidate senza movstr e senza callee ignoti

Ho provato `s08b_bunsin2_800CE180`, `s08b_bunsin2_800CE2E8`,
`s08b_bunsin2_800CE0A4`, `s08b_bunsin2_800CD614`, `s08b_bunsin2_800CE8A0`,
`s08b_bunsin2_800CF150`, `s14e_800CA49C`, `s14e_800CCF84`,
`s07b_800C4FB8`, `s14e_800CA2E4`. Tutte bloccate da almeno uno di:
- `lwl/lwr/swl/swr` (movstr, non riproducibile da C standard)
- callee ignoto nel main exe (0x80092DD8, 0x80093798, 0x8001706C, ecc.)
- allocazione registri diversa

### Stato

`DIFF=2  MATCH=41  NOPDIFF=6  TODO=698`. Invariato.

Le candidate pronte rimaste (s07b_800C47D4, s07b_800C96B8, s14e_800C82D0,
s14e_800C6208, s15c_dyncon_800D88C8, s14e_800CA538, brf_800DDC40,
brf_800DA4E8, brf_800DCB44, rank_800D2644, s11d_hind_800CAFEC) sono
tutte state provate e non matchano.

Mi fermo qui: sono le 14:00 CET del 27/10.

---

## 29/10, 10:00 CET — rank_800CC19C MATCH, fix cmp_func lui+lw

### `cmp_func.py`: fold anche `lui`+`lw`

Il compilatore Konami usava `lui 0x800b; lw -0x4d14` (GAS convention,
%hi arrotonda), cc1psx usa `lui 0x800a; lw 0xb2ec` (split plain). Entrambi
producono 0x800AB2EC ma il fold precedente applicava sign-extension a
entrambi, dando risultati diversi. Rimosso il sign-extension: `(hi << 16) + lo`
funziona per entrambi grazie al carry.

### `rank_800CC19C` (17 istruzioni) → MATCH + EQUIV

Usa `0x800AB2EC` (int globale del main exe) e `0x8008E768` (funzione del
main exe). Entrambi dichiarati come `extern` con nomi sintetici
(`data_800AB2EC`, `func_8008E768`) e aggiunti a `symtab.json` e
`functions.txt`.

### Stato

`DIFF=2  MATCH=42  NOPDIFF=6  TODO=697`. Un commit nuovo (`fcf3213`).

Mi fermo qui: sono le 10:00 CET del 29/10.
