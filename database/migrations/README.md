# Migration policy

Tento adresář obsahuje sekvenční `.sql` migrace aplikované v pořadí podle
čísla v názvu souboru (`001_`, `002_`, ...). `database.database.Database.migrate()`
sleduje aktuální verzi v tabulce `schema_version` a aplikuje jen migrace
s vyšším číslem.

## Tvrdé pravidlo: migrace nikdy nesmí způsobit ztrátu dat

Toto pravidlo existuje, protože update programu nikdy nesmí vyžadovat ani
riskovat smazání uživatelských dat (viz `philosophy.md`, princip 2.5 —
Consent & Control se vztahuje i na to, že uživatel neztrácí historii jen
proto, že aktualizoval program).

Konkrétně to znamená:

**Povoleno:**
- `CREATE TABLE IF NOT EXISTS ...`
- `ALTER TABLE ... ADD COLUMN ...` (nový sloupec, ideálně s `DEFAULT`)
- `CREATE INDEX IF NOT EXISTS ...`
- Nová migrace, která data pouze **přidává** nebo **kopíruje/transformuje**
  do nových struktur při zachování původních dat (a ideálně i původních
  tabulek, dokud není jasné, že migrace proběhla bez problémů).

**Zakázáno bez výslovné výjimky a zálohy navíc:**
- `DROP TABLE`
- `DROP COLUMN`
- `DELETE FROM ...` mimo cílené opravy datové konzistence
- Jakákoli operace, která by při selhání uprostřed migrace mohla nechat
  data v nekonzistentním nebo ztraceném stavu

Pokud se v budoucnu ukáže jako nutné odstranit nebo zásadně restrukturovat
tabulku, postupuje se takto:
1. Nová migrace vytvoří novou strukturu vedle staré (ne místo ní).
2. Runtime kód se přepne na novou strukturu.
3. Stará struktura se ponechá alespoň jedno "vydání" jako záloha uvnitř DB.
4. Teprve samostatná, explicitně označená migrace starou strukturu odstraní
   — a to až po ověření, že nová struktura funguje a záloha (viz níže)
   existuje.

## Zálohy

`Database.migrate()` automaticky vytvoří zálohu (`backup.create_backup`,
reason=`pre_migration`) těsně před aplikací jakýchkoli nových migrací,
pokud databázový soubor už existuje. Navíc `Database.ensure_daily_backup()`,
volané při startu aplikace, zaručuje maximálně jednu automatickou zálohu
za den i mimo migrace. Zálohy se rotují (`backup.rotate_backups`) podle
`BACKUP_RETENTION_COUNT` z konfigurace (default 14).

Zálohy žijí v `data/backups/`, tedy mimo zdrojový kód — update programu
(např. `git pull`) se jich nedotkne.
